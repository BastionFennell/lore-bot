"""RP-harvest: marks, transcript formatting, engine glue, command parsing, fetch path.

No real Discord/API calls — the engine is driven by the scripted FakeAnthropicClient
and the fetch path by a fake channel with an async ``history`` iterator.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from lorebot import harvest as harvest_mod
from lorebot import main as main_mod
from lorebot.engine import Conversational, ProposedWrite
from lorebot.harvest import HarvestMarks, prepare_harvest, run_harvest
from lorebot.main import LoreBot
from lorebot.pending import PendingStore
from tests.fakes import FakeAnthropicClient, FakeMessage, FakeText, FakeToolUse


# --- High-water marks -------------------------------------------------------

def make_marks(tmp_path):
    return HarvestMarks(str(tmp_path / "marks.sqlite"))


def test_unset_mark_is_none(tmp_path):
    marks = make_marks(tmp_path)
    assert marks.get("s1") is None
    assert marks.last_message_id("s1") is None


def test_advance_sets_mark(tmp_path):
    marks = make_marks(tmp_path)
    marks.advance("s1", "999", "2026-07-08T00:00:00+00:00")
    m = marks.get("s1")
    assert m.last_message_id == "999"
    assert m.harvested_at.startswith("2026-07-08")
    # advancing again moves it (in-place upsert, one row per source)
    marks.advance("s1", "1200", "2026-07-09T00:00:00+00:00")
    assert marks.last_message_id("s1") == "1200"


def test_from_start_reset_clears_mark(tmp_path):
    marks = make_marks(tmp_path)
    marks.advance("s1", "999", "2026-07-08T00:00:00+00:00")
    marks.reset("s1")
    assert marks.get("s1") is None  # next harvest reads from the start


def test_partial_cap_advances_to_last_fetched(tmp_path):
    marks = make_marks(tmp_path)
    # a capped run advances only to the last FETCHED id, not the true channel head
    msgs = [_m(str(i), content="line") for i in range(1, 4)]
    prepared = prepare_harvest(msgs, cap=3)
    assert prepared.partial is True
    marks.advance("s1", prepared.new_mark, "2026-07-08T00:00:00+00:00")
    assert marks.last_message_id("s1") == "3"  # continues from here next run


# --- Transcript formatting --------------------------------------------------

def _m(id, *, author="alice", content="hello", is_bot=False, day=8):
    return {
        "id": id,
        "created_at": datetime(2026, 7, day, 12, 0, tzinfo=timezone.utc),
        "author": author,
        "content": content,
        "is_bot": is_bot,
    }


def test_transcript_skips_bots_and_empties_with_date_prefix():
    msgs = [
        _m("1", author="alice", content="We made landfall at Kell's Hollow."),
        _m("2", author="LoreBot", content="preview…", is_bot=True),  # bot -> skipped
        _m("3", author="bob", content="   "),  # empty/system -> skipped
        _m("4", author="bob", content="The harbormaster is named Vex."),
    ]
    prepared = prepare_harvest(msgs)
    lines = prepared.transcript.split("\n")
    assert lines == [
        "[2026-07-08] alice: We made landfall at Kell's Hollow.",
        "[2026-07-08] bob: The harbormaster is named Vex.",
    ]
    assert prepared.count == 2  # kept messages only
    assert prepared.new_mark == "4"  # newest FETCHED id, incl. skipped ones
    assert prepared.partial is False


def test_transcript_preserves_oldest_first_order_and_date_range():
    msgs = [
        _m("1", content="first", day=6),
        _m("2", content="second", day=7),
        _m("3", content="third", day=9),
    ]
    prepared = prepare_harvest(msgs)
    assert prepared.transcript.splitlines()[0].endswith("first")
    assert prepared.transcript.splitlines()[-1].endswith("third")
    assert prepared.date_range == "2026-07-06 → 2026-07-09"


def test_single_day_range_is_one_date():
    prepared = prepare_harvest([_m("1", content="x")])
    assert prepared.date_range == "2026-07-08"


def test_empty_fetch_yields_no_mark():
    prepared = prepare_harvest([])
    assert prepared.new_mark is None
    assert prepared.transcript == ""
    assert prepared.count == 0


def test_all_bots_advances_mark_but_empty_transcript():
    msgs = [_m("1", is_bot=True), _m("2", is_bot=True)]
    prepared = prepare_harvest(msgs)
    assert prepared.transcript == ""
    assert prepared.new_mark == "2"  # still advances over the bot messages


# --- Engine glue ------------------------------------------------------------

def test_harvest_context_prefixes_instructions_and_transcript():
    ctx = harvest_mod.build_harvest_context("[2026-07-08] alice: hi", "skipper")
    assert ctx.message_text.startswith(harvest_mod.llm.HARVEST_INSTRUCTIONS[:20])
    assert "[2026-07-08] alice: hi" in ctx.message_text
    assert ctx.author == "skipper"
    assert ctx.recent_messages == [] and ctx.history_fetch is None and ctx.pending is None


def test_run_harvest_query_then_batch_yields_proposed_write(index):
    # query_lore (proving ripple updates can flow) then a batch: a ripple append to an
    # existing page plus a brand-new entry.
    client = FakeAnthropicClient([
        FakeMessage("tool_use", [FakeToolUse("query_lore", {"slug": "captain-powderkeg"}, "t1")]),
        FakeMessage("tool_use", [
            FakeToolUse("append_to_entry",
                        {"slug": "captain-powderkeg", "section_heading": "Recent History",
                         "content": "She raided Kell's Hollow."}, "t2"),
            FakeToolUse("create_entry",
                        {"type": "location", "title": "Kell's Hollow", "tags": ["port"],
                         "summary": "A smuggler's cove.",
                         "body_sections": [{"heading": "Description", "content": "A cove."}]}, "t3"),
        ]),
    ])
    out = run_harvest(
        client=client, model="m", index=index,
        transcript="[2026-07-08] alice: Powderkeg raided Kell's Hollow.",
        author="skipper",
    )
    assert isinstance(out, ProposedWrite)
    assert [o["tool"] for o in out.operations] == ["append_to_entry", "create_entry"]
    assert len(client.calls) == 2  # read executed, then the write batch captured


def test_run_harvest_no_action_yields_conversational(index):
    client = FakeAnthropicClient([
        FakeMessage("tool_use", [
            FakeToolUse("no_action", {"reason": "Just idle banter — nothing lore-worthy."}, "t1"),
        ]),
    ])
    out = run_harvest(
        client=client, model="m", index=index,
        transcript="[2026-07-08] alice: lol nice roll",
        author="skipper",
    )
    assert isinstance(out, Conversational)
    assert "lore-worthy" in out.text


# --- Command parsing (on_message) ------------------------------------------

class FakeAuthor:
    def __init__(self, uid=42, name="skipper", bot=False):
        self.id = uid
        self.display_name = name
        self.bot = bot


class FakeChannel:
    def __init__(self):
        self.sent: list[str] = []

    async def send(self, text):
        self.sent.append(text)
        return SimpleNamespace(id=1)


class FakeIncoming:
    _next = 5000

    def __init__(self, content, channel, author=None):
        self.content = content
        self.channel = channel
        self.author = author or FakeAuthor()
        FakeIncoming._next += 1
        self.id = FakeIncoming._next


def make_bot(tmp_path, *, rp_source_ids):
    bot = object.__new__(LoreBot)
    bot.store = PendingStore(str(tmp_path / "p.sqlite"))
    bot.marks = HarvestMarks(str(tmp_path / "p.sqlite"))
    bot.config = SimpleNamespace(rp_source_ids=rp_source_ids)
    bot._seen_messages = main_mod.SeenMessages()
    bot._allowed = lambda m: True
    return bot


async def _drive(bot, content, author=None):
    calls = {}

    async def fake_harvest(message, *, from_start):
        calls["harvest"] = from_start

    async def fake_run(message, **kwargs):
        calls["run"] = message.content

    bot._harvest = fake_harvest
    bot._run = fake_run
    await bot.on_message(FakeIncoming(content, FakeChannel(), author))
    return calls


@pytest.mark.parametrize("text", ["harvest", "HARVEST", "  harvest  ", "Harvest"])
async def test_harvest_command_variants_dispatch_to_harvest(tmp_path, text):
    bot = make_bot(tmp_path, rp_source_ids=[1])
    calls = await _drive(bot, text)
    assert calls == {"harvest": False}


@pytest.mark.parametrize("text", ["harvest from start", "HARVEST FROM START", " Harvest From Start "])
async def test_harvest_from_start_variants(tmp_path, text):
    bot = make_bot(tmp_path, rp_source_ids=[1])
    calls = await _drive(bot, text)
    assert calls == {"harvest": True}


@pytest.mark.parametrize("text", ["harvester", "harvest now", "please harvest", "harvest from"])
async def test_near_misses_flow_to_engine(tmp_path, text):
    bot = make_bot(tmp_path, rp_source_ids=[1])
    calls = await _drive(bot, text)
    assert "harvest" not in calls
    assert calls.get("run") == text


async def test_disabled_config_message(tmp_path):
    bot = make_bot(tmp_path, rp_source_ids=[])
    chan = FakeChannel()
    # real _harvest runs (no stub) so we see the disabled message
    await bot.on_message(FakeIncoming("harvest", chan))
    assert len(chan.sent) == 1 and "disabled" in chan.sent[0].lower()
    assert "RP_SOURCE_IDS" in chan.sent[0]


async def test_refused_while_previews_pending(tmp_path):
    bot = make_bot(tmp_path, rp_source_ids=[1])
    author = FakeAuthor()
    bot.store.set_awaiting_confirmation(
        str(author.id),
        [{"operations": [{"tool": "add_glossary_term",
                          "input": {"term": "Kin", "definition": "d", "link_slug": None}}],
          "preview_message_id": "m1", "context": {"label": "kin"}}],
    )
    chan = FakeChannel()
    calls = {}

    async def fake_harvest(message, *, from_start):
        calls["harvest"] = True

    bot._harvest = fake_harvest
    await bot.on_message(FakeIncoming("harvest", chan, author))
    assert "harvest" not in calls  # refused, did not run
    assert "pending" in chan.sent[0].lower()


# --- Async fetch path -------------------------------------------------------

class FakeHistoryMessage:
    def __init__(self, id, author, content, bot=False, day=8):
        self.id = id
        self.author = SimpleNamespace(display_name=author, bot=bot)
        self.content = content
        self.created_at = datetime(2026, 7, day, 9, 0, tzinfo=timezone.utc)


class FakeHistoryChannel:
    """Mimics discord's channel.history(...) async iterator."""

    def __init__(self, messages, name="rp-thread"):
        self._messages = messages  # already oldest-first
        self.name = name
        self.calls = []

    def history(self, *, limit, after, oldest_first):
        self.calls.append({"limit": limit, "after": after, "oldest_first": oldest_first})
        msgs = self._messages

        class _Aiter:
            def __init__(self, items):
                self._it = iter(items)

            def __aiter__(self):
                return self

            async def __anext__(self):
                try:
                    return next(self._it)
                except StopIteration:
                    raise StopAsyncIteration

        return _Aiter(msgs[:limit])


async def test_fetch_source_maps_messages_to_dicts(tmp_path):
    bot = make_bot(tmp_path, rp_source_ids=[1])
    chan = FakeHistoryChannel([
        FakeHistoryMessage(101, "alice", "landfall", day=6),
        FakeHistoryMessage(102, "LoreBot", "preview", bot=True, day=7),
        FakeHistoryMessage(103, "bob", "the harbormaster is Vex", day=8),
    ])
    raw = await bot._fetch_source(chan, None, 400)
    assert [r["id"] for r in raw] == ["101", "102", "103"]
    assert raw[1]["is_bot"] is True
    assert chan.calls[0] == {"limit": 400, "after": None, "oldest_first": True}
    # feeds prepare_harvest end-to-end
    prepared = prepare_harvest(raw)
    assert prepared.count == 2  # bot skipped
    assert prepared.new_mark == "103"


async def test_fetch_source_passes_after_object(tmp_path):
    bot = make_bot(tmp_path, rp_source_ids=[1])
    chan = FakeHistoryChannel([FakeHistoryMessage(200, "alice", "hi")])
    await bot._fetch_source(chan, "150", 400)
    after = chan.calls[0]["after"]
    assert after is not None and after.id == 150  # discord.Object(id=150)
