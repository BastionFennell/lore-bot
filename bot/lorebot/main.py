"""Discord entrypoint: ``python -m lorebot.main``.

Listens in exactly one channel from allow-listed users, drives the engine, shows
previews, and commits on the ✅ reaction. The engine and all content/git work run
in a worker thread so the event loop is never blocked; ``fetch_channel_history``
bridges back to the loop via ``run_coroutine_threadsafe``.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from datetime import datetime, timezone

import discord
from discord.ext import tasks

# Pacing between consecutive Discord API calls when fanning out a batch: separate
# preview messages and the two reactions per message, kept clear of rate limits.
REACTION_PACE = 0.3  # between the ✅ and ❌ on one message
MESSAGE_PACE = 0.5  # between consecutive preview messages / reaction groups

from . import engine as engine_mod
from . import gitops, harvest as harvest_mod, llm, preview, refrender, siteurls
from .config import Config, ConfigError, load_config
from .content import entries as entries_mod
from .content.index import ContentIndex
from .discord_io import CANCEL_EMOJI, CONFIRM_EMOJI, SeenMessages, split_message
from .pending import (
    AWAITING_CLARIFICATION,
    AWAITING_CONFIRMATION,
    PendingStore,
)

log = logging.getLogger("lorebot.main")

PENDING_TTL_SECONDS = 30 * 60

# Exact (case-insensitive, whitespace-trimmed) commands. Anything else flows to the
# engine as usual.
HARVEST_COMMAND = "harvest"
HARVEST_FROM_START_COMMAND = "harvest from start"


class LoreBot(discord.Client):
    def __init__(self, config: Config):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.reactions = True
        super().__init__(intents=intents)
        self.config = config
        self.store = PendingStore(str(config.sqlite_path))
        self.marks = harvest_mod.HarvestMarks(str(config.sqlite_path))
        self.llm_client = llm.build_client(config.anthropic_api_key)
        # Guard against gateway event redelivery processing a message twice.
        self._seen_messages = SeenMessages(maxlen=500)

    # --- lifecycle ---------------------------------------------------------
    async def on_ready(self):
        log.info("Logged in as %s; watching channel %s", self.user, self.config.channel_id)
        if not self.expiry_loop.is_running():
            self.expiry_loop.start()

    def _allowed(self, message: discord.Message) -> bool:
        if message.author.bot or message.author == self.user:
            return False
        if message.guild is None or message.guild.id != self.config.guild_id:
            return False
        if message.channel.id != self.config.channel_id:
            return False
        return message.author.id in self.config.allowed_user_ids

    # --- messages ----------------------------------------------------------
    async def on_message(self, message: discord.Message):
        if not self._allowed(message):
            return
        # Idempotency: the gateway can redeliver an event; process each id once.
        if not self._seen_messages.add(message.id):
            log.info("skipping already-processed message %s", message.id)
            return
        user_id = str(message.author.id)
        items = self.store.get(user_id)
        clarification = next((i for i in items if i.state == AWAITING_CLARIFICATION), None)
        confirmations = [i for i in items if i.state == AWAITING_CONFIRMATION]

        # Literal "cancel all" clears every pending item for the requesting user.
        if items and message.content.strip().lower() == "cancel all":
            self.store.clear(user_id)
            n = len(items)
            await self._send(
                message.channel,
                f"❌ Cancelled all {n} pending item{'s' if n != 1 else ''} — nothing was committed.",
            )
            return

        # Explicit RP-harvest commands (deterministic, like "cancel all").
        norm = message.content.strip().lower()
        if norm in (HARVEST_COMMAND, HARVEST_FROM_START_COMMAND):
            if items:
                # Harvest results would tangle with outstanding items — refuse.
                labels = ", ".join(self._item_label(i) for i in items)
                n = len(items)
                await self._send(
                    message.channel,
                    f"You have {n} pending item{'s' if n != 1 else ''} ({labels}) — "
                    "resolve or 'cancel all' before harvesting.",
                )
                return
            await self._harvest(message, from_start=(norm == HARVEST_FROM_START_COMMAND))
            return

        if clarification is not None:
            # A non-reaction text reply while awaiting clarification = an answer.
            await self._run(message, correction=message.content, prior=clarification)
            return
        if confirmations:
            # A non-reaction text reply while previews await confirmation = a
            # correction (the engine decides: revised proposal vs. still-waiting).
            await self._run(
                message, correction=message.content, prior=confirmations[0], outstanding=confirmations
            )
            return
        await self._run(message)

    async def _run(self, message: discord.Message, *, correction=None, prior=None, outstanding=None):
        index = ContentIndex(self.config.content_root)
        recent = await self._recent_messages(message)
        ctx = engine_mod.EngineContext(
            message_text=message.content,
            author=message.author.display_name,
            recent_messages=recent,
            history_fetch=self._make_history_fetch(message.channel),
            pending=(prior.context or {}) if prior else None,
            correction=correction,
        )
        loop = asyncio.get_running_loop()
        # Typing indicator shows the bot is working while the model thinks.
        async with message.channel.typing():
            outcome = await loop.run_in_executor(
                None,
                functools.partial(
                    engine_mod.run_engine,
                    client=self.llm_client,
                    model=self.config.anthropic_model,
                    context=ctx,
                    index=index,
                    effort=self.config.anthropic_effort,
                ),
            )
        await self._dispatch(message, index, outcome, correction=correction, outstanding=outstanding)

    async def _dispatch(self, message, index, outcome, *, correction=None, outstanding=None):
        user_id = str(message.author.id)
        outstanding = outstanding or []
        if isinstance(outcome, engine_mod.ProposedWrite):
            try:
                plans = preview.build_plans(self.config.content_root, index, outcome.operations)
            except (entries_mod.SlugCollisionError, entries_mod.EntryError) as e:
                # A bad (correcting) proposal leaves the outstanding previews alone.
                if not outstanding:
                    self.store.clear(user_id)
                await self._send(message.channel, f"⚠️ {e}")
                return
            if outstanding:
                # A valid correction replaces the outstanding previews.
                self.store.clear(user_id)
                n = len(outstanding)
                await self._send(
                    message.channel,
                    f"Replacing {n} outstanding preview{'s' if n != 1 else ''} "
                    "with the corrected proposal.",
                )
            await self._present_proposal(message, plans, outcome.operations)
        elif isinstance(outcome, engine_mod.Clarification):
            if outstanding:
                # The engine read this as unrelated / needs-clarification: keep the
                # outstanding previews and remind the user they're still waiting.
                labels = ", ".join(self._item_label(i) for i in outstanding)
                n = len(outstanding)
                await self._send(
                    message.channel,
                    f"Still waiting on {n} pending preview{'s' if n != 1 else ''}: {labels} — "
                    "react ✅/❌ on each, or say 'cancel all'.",
                )
                return
            text = outcome.question
            if outcome.options:
                text += "\n" + "\n".join(f"• {o}" for o in outcome.options)
            await self._send(message.channel, text)
            self.store.set_awaiting_clarification(
                user_id, question=outcome.question, context={"message_text": message.content}
            )
        elif isinstance(outcome, engine_mod.Conversational):
            # Don't clobber outstanding previews on an incidental reply.
            if not outstanding:
                self.store.clear(user_id)
            # Render inline {{refs}} in /ask answers to links before sending.
            text = refrender.render_refs(
                outcome.text,
                self.config.content_root,
                getattr(self.config, "site_base_url", None),
            )
            await self._send(message.channel, text)
        else:  # Error
            await self._send(message.channel, f"⚠️ {outcome.message}")

    async def _present_proposal(self, message, plans, operations):
        """Send one preview message per op (each with its own ✅/❌), persist a
        pending row per op, then add reactions — all paced to avoid rate limits."""
        user_id = str(message.author.id)
        items = []
        sent_msgs = []
        for k, plan in enumerate(plans):
            if k > 0:
                await asyncio.sleep(MESSAGE_PACE)
            sent = await self._send(message.channel, plan.preview)
            preview_msg = sent[-1] if sent else None
            sent_msgs.append(preview_msg)
            items.append(
                {
                    "operations": [operations[k]],
                    "preview_message_id": preview_msg.id if preview_msg else None,
                    "context": {
                        "message_text": message.content,
                        "label": plan.ops[0].target,
                    },
                }
            )
        # Persist rows before reacting so a fast ✅ always finds its pending item.
        self.store.set_awaiting_confirmation(user_id, items)
        for i, preview_msg in enumerate(sent_msgs):
            if preview_msg is None:
                continue
            if i > 0:
                await asyncio.sleep(MESSAGE_PACE)
            await preview_msg.add_reaction(CONFIRM_EMOJI)
            # Discord's add-reaction bucket is ~1/250ms; pace the second reaction.
            await asyncio.sleep(REACTION_PACE)
            await preview_msg.add_reaction(CANCEL_EMOJI)

    @staticmethod
    def _item_label(pending) -> str:
        """Short target label for an item ("kin", "gull-reef", …)."""
        if pending.context and pending.context.get("label"):
            return pending.context["label"]
        op = (pending.operations or [{}])[0]
        data = op.get("input", {}) or {}
        return (
            data.get("slug")
            or data.get("term")
            or data.get("title")
            or data.get("date_in_fiction")
            or "item"
        )

    # --- harvest -----------------------------------------------------------
    async def _harvest(self, message: discord.Message, *, from_start: bool):
        """Read the configured RP source(s) since each one's mark, run each transcript
        through the engine, and dispatch the outcome through the normal preview flow."""
        sources = self.config.rp_source_ids
        if not sources:
            await self._send(
                message.channel,
                "RP harvest is disabled — set `RP_SOURCE_IDS` in the bot's env "
                "(comma-separated channel/thread IDs) and restart to enable it.",
            )
            return
        async with message.channel.typing():
            for source_id in sources:
                await self._harvest_one(message, source_id, from_start=from_start)

    async def _harvest_one(self, message: discord.Message, source_id: int, *, from_start: bool):
        sid = str(source_id)
        try:
            channel = self.get_channel(source_id) or await self.fetch_channel(source_id)
        except discord.Forbidden:
            await self._send(
                message.channel,
                f"⚠️ I can't read source `{sid}` — I need View Channel + Read Message "
                "History on it (or its parent channel, for a thread).",
            )
            return
        except (discord.NotFound, discord.HTTPException) as e:
            await self._send(message.channel, f"⚠️ Couldn't open RP source `{sid}`: {e}")
            return

        label = getattr(channel, "name", None) or sid
        if from_start:
            self.marks.reset(sid)
        mark = self.marks.get(sid)
        after_id = mark.last_message_id if mark else None

        try:
            raw = await self._fetch_source(channel, after_id, harvest_mod.MAX_MESSAGES_PER_RUN)
        except discord.Forbidden:
            await self._send(
                message.channel,
                f"⚠️ I can't read message history in `{label}` — grant View Channel + "
                "Read Message History on its parent channel.",
            )
            return

        prepared = harvest_mod.prepare_harvest(raw)
        if not raw:
            since = (mark.harvested_at or "")[:10] if mark and mark.harvested_at else "the start"
            await self._send(
                message.channel, f"Nothing new to harvest since {since} ({label})."
            )
            return

        # The mark advances when a harvest RUNS (to the newest FETCHED id), so a partial
        # cap-limited run continues on the next `harvest`. `harvest from start` is the redo.
        self.marks.advance(sid, prepared.new_mark, datetime.now(timezone.utc).isoformat())

        if not prepared.transcript:
            since = (mark.harvested_at or "")[:10] if mark and mark.harvested_at else "the start"
            await self._send(
                message.channel, f"Nothing new to harvest since {since} ({label})."
            )
            return

        status = (
            f"Harvesting {prepared.count} new message(s) from {label} "
            f"({prepared.date_range})…"
        )
        if prepared.partial:
            status += (
                f"\n(Partial — hit the {harvest_mod.MAX_MESSAGES_PER_RUN}-message cap; "
                "run `harvest` again to continue.)"
            )
        await self._send(message.channel, status)

        index = ContentIndex(self.config.content_root)
        loop = asyncio.get_running_loop()
        outcome = await loop.run_in_executor(
            None,
            functools.partial(
                harvest_mod.run_harvest,
                client=self.llm_client,
                model=self.config.anthropic_model,
                index=index,
                transcript=prepared.transcript,
                author=message.author.display_name,
                effort=self.config.anthropic_effort,
            ),
        )
        await self._dispatch(message, index, outcome)

    async def _fetch_source(self, channel, after_id, cap: int) -> list[dict]:
        """Fetch up to ``cap`` messages after ``after_id`` (all if None), oldest-first,
        as plain dicts for :func:`harvest.prepare_harvest`. Bots/empties are filtered
        there, not here — the mark advances over them too."""
        after = discord.Object(id=int(after_id)) if after_id else None
        out: list[dict] = []
        async for m in channel.history(limit=cap, after=after, oldest_first=True):
            out.append(
                {
                    "id": str(m.id),
                    "created_at": m.created_at,
                    "author": m.author.display_name,
                    "content": m.content or "",
                    "is_bot": bool(m.author.bot),
                }
            )
        return out

    # --- reactions ---------------------------------------------------------
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == (self.user.id if self.user else None):
            return
        if payload.channel_id != self.config.channel_id:
            return
        pending = self.store.get_by_preview_message_id(str(payload.message_id))
        if pending is None or pending.user_id != str(payload.user_id):
            return
        emoji = str(payload.emoji)
        channel = self.get_channel(payload.channel_id)
        if emoji == CONFIRM_EMOJI:
            await self._commit(channel, pending, payload.user_id, member=payload.member)
        elif emoji == CANCEL_EMOJI:
            # Cancel just this item; any siblings in the batch keep waiting.
            self.store.clear_item(preview_message_id=str(payload.message_id))
            await self._send(
                channel, f"❌ Cancelled {self._item_label(pending)} — nothing was committed."
            )

    async def _commit(self, channel, pending, user_id, member=None):
        # The raw reaction payload carries the member for guild reactions; the
        # member cache is usually empty (no members intent), so prefer payload.
        member = member or (channel.guild.get_member(int(user_id)) if channel.guild else None)
        if member:
            username = member.name
        else:
            user = self.get_user(int(user_id))
            username = user.name if user else f"user-{user_id}"
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None,
                functools.partial(
                    gitops.apply_operations,
                    self.config.repo_path,
                    self.config.content_root,
                    pending.operations,
                    username,
                ),
            )
        except (entries_mod.SlugCollisionError, entries_mod.EntryError) as e:
            self.store.clear_item(row_id=pending.id)
            await self._send(channel, f"⚠️ {e}")
            return
        self.store.clear_item(row_id=pending.id)
        msg = f"{'✅' if result.ok else '⚠️'} {result.message}"
        if result.ok and result.committed:
            urls = siteurls.page_urls(
                self.config.site_base_url, self.config.content_root, pending.operations
            )
            if urls:
                # <angle brackets> suppress Discord's link-preview embeds.
                where = "\n".join(f"📖 <{u}>" for u in urls)
                note = " (live after the deploy, ~1 min)" if result.pushed else ""
                msg += f"\n{where}{note}"
            elif result.commit_sha:
                msg += f"\nCommit `{result.commit_sha[:8]}`."
        await self._send(channel, msg)
        if result.both_versions:
            for path, blob in result.both_versions.items():
                await self._send(channel, f"**{path}**\n```\n{blob[:1800]}\n```")

    # --- expiry ------------------------------------------------------------
    @tasks.loop(seconds=60)
    async def expiry_loop(self):
        expired = self.store.expire(PENDING_TTL_SECONDS)
        if not expired:
            return
        channel = self.get_channel(self.config.channel_id)
        if channel is None:
            return
        # One summary message for the whole sweep, not one per expired item.
        labels = ", ".join(sorted({self._item_label(p) for p in expired}))
        users = " ".join(f"<@{u}>" for u in sorted({p.user_id for p in expired}))
        n = len(expired)
        await self._send(
            channel,
            f"⌛ {n} pending item{'s' if n != 1 else ''} ({labels}) from {users} "
            "expired after 30 minutes and "
            f"{'were' if n != 1 else 'was'} cancelled.",
        )

    @expiry_loop.before_loop
    async def _before_expiry(self):
        await self.wait_until_ready()

    # --- helpers -----------------------------------------------------------
    async def _recent_messages(self, message, limit: int = 5) -> list[dict]:
        out = []
        async for m in message.channel.history(limit=limit + 1, before=message):
            out.append({"author": m.author.display_name, "content": m.content})
        out.reverse()
        return out

    def _make_history_fetch(self, channel):
        loop = asyncio.get_running_loop()

        def fetch(limit: int, before_message_id):
            async def _do():
                kwargs = {"limit": limit}
                if before_message_id:
                    kwargs["before"] = discord.Object(id=int(before_message_id))
                msgs = []
                async for m in channel.history(**kwargs):
                    msgs.append({"author": m.author.display_name, "content": m.content})
                return msgs

            future = asyncio.run_coroutine_threadsafe(_do(), loop)
            return future.result(timeout=30)

        return fetch

    async def _send(self, channel, text: str):
        sent = []
        for chunk in split_message(text):
            sent.append(await channel.send(chunk))
        return sent


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    try:
        config = load_config(require_discord=True)
    except ConfigError as e:
        raise SystemExit(str(e))
    bot = LoreBot(config)
    bot.run(config.discord_token)


if __name__ == "__main__":
    main()
