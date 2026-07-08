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

import discord
from discord.ext import tasks

from . import engine as engine_mod
from . import gitops, llm, preview
from .config import Config, ConfigError, load_config
from .content import entries as entries_mod
from .content.index import ContentIndex
from .discord_io import CANCEL_EMOJI, CONFIRM_EMOJI, split_message
from .pending import (
    AWAITING_CLARIFICATION,
    AWAITING_CONFIRMATION,
    PendingStore,
)

log = logging.getLogger("lorebot.main")

PENDING_TTL_SECONDS = 30 * 60


class LoreBot(discord.Client):
    def __init__(self, config: Config):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.reactions = True
        super().__init__(intents=intents)
        self.config = config
        self.store = PendingStore(str(config.sqlite_path))
        self.llm_client = llm.build_client(config.anthropic_api_key)

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
        user_id = str(message.author.id)
        pending = self.store.get(user_id)

        if pending is not None and pending.state == AWAITING_CONFIRMATION:
            # A non-reaction text reply at the confirmation stage = a correction.
            await self._run(message, correction=message.content, prior=pending)
            return
        if pending is not None and pending.state == AWAITING_CLARIFICATION:
            await self._run(message, correction=message.content, prior=pending)
            return
        await self._run(message)

    async def _run(self, message: discord.Message, *, correction=None, prior=None):
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
        await self._dispatch(message, index, outcome, correction=correction)

    async def _dispatch(self, message, index, outcome, *, correction=None):
        user_id = str(message.author.id)
        if isinstance(outcome, engine_mod.ProposedWrite):
            try:
                plan = preview.build_plan(self.config.content_root, index, outcome.operation)
            except (entries_mod.SlugCollisionError, entries_mod.EntryError) as e:
                self.store.clear(user_id)
                await self._send(message.channel, f"⚠️ {e}")
                return
            sent = await self._send(message.channel, plan.preview)
            preview_msg = sent[-1] if sent else None
            self.store.set_awaiting_confirmation(
                user_id,
                operation=outcome.operation,
                preview_message_id=preview_msg.id if preview_msg else None,
                context={"message_text": message.content},
            )
            if preview_msg:
                await preview_msg.add_reaction(CONFIRM_EMOJI)
                await preview_msg.add_reaction(CANCEL_EMOJI)
        elif isinstance(outcome, engine_mod.Clarification):
            text = outcome.question
            if outcome.options:
                text += "\n" + "\n".join(f"• {o}" for o in outcome.options)
            await self._send(message.channel, text)
            self.store.set_awaiting_clarification(
                user_id, question=outcome.question, context={"message_text": message.content}
            )
        elif isinstance(outcome, engine_mod.Conversational):
            self.store.clear(user_id)
            await self._send(message.channel, outcome.text)
        else:  # Error
            await self._send(message.channel, f"⚠️ {outcome.message}")

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
            await self._commit(channel, pending, payload.user_id)
        elif emoji == CANCEL_EMOJI:
            self.store.clear(pending.user_id)
            await self._send(channel, "❌ Cancelled — nothing was committed.")

    async def _commit(self, channel, pending, user_id):
        username = "unknown"
        member = channel.guild.get_member(int(user_id)) if channel.guild else None
        if member:
            username = member.name
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None,
                functools.partial(
                    gitops.apply_operation,
                    self.config.repo_path,
                    self.config.content_root,
                    pending.operation,
                    username,
                ),
            )
        except (entries_mod.SlugCollisionError, entries_mod.EntryError) as e:
            self.store.clear(pending.user_id)
            await self._send(channel, f"⚠️ {e}")
            return
        self.store.clear(pending.user_id)
        msg = f"{'✅' if result.ok else '⚠️'} {result.message}"
        if result.commit_sha:
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
        for p in expired:
            await self._send(
                channel,
                f"⌛ A pending operation from <@{p.user_id}> expired after 30 minutes and was cancelled.",
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
