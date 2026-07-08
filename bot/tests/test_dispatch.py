"""Transport-level dispatch logic that doesn't need a live Discord gateway.

We build a ``LoreBot`` via ``object.__new__`` and wire only the attributes the
methods under test touch (``store``, ``config``), then drive ``_dispatch`` /
``_present_proposal`` / ``on_message`` with tiny fakes. The engine/LLM is never
called — we feed pre-built ``Outcome`` objects straight into ``_dispatch``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lorebot import engine as engine_mod
from lorebot import main as main_mod
from lorebot.main import LoreBot
from lorebot.pending import AWAITING_CONFIRMATION, PendingStore


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _fast(_):
        return None

    monkeypatch.setattr(main_mod.asyncio, "sleep", _fast)


class FakeMessage:
    _next_id = 1000

    def __init__(self, content: str):
        self.content = content
        FakeMessage._next_id += 1
        self.id = FakeMessage._next_id
        self.reactions: list[str] = []

    async def add_reaction(self, emoji):
        self.reactions.append(emoji)


class FakeChannel:
    def __init__(self):
        self.sent: list[str] = []
        self.messages: list[FakeMessage] = []

    async def send(self, text):
        self.sent.append(text)
        m = FakeMessage(text)
        self.messages.append(m)
        return m


class FakeAuthor:
    def __init__(self, uid=42, name="skipper"):
        self.id = uid
        self.display_name = name


class FakeIncoming:
    """An inbound Discord message (what on_message / _dispatch receive)."""

    def __init__(self, content, channel, author=None):
        self.content = content
        self.channel = channel
        self.author = author or FakeAuthor()
        FakeMessage._next_id += 1
        self.id = FakeMessage._next_id


def make_bot(tmp_path, content_root, repo_path):
    bot = object.__new__(LoreBot)
    bot.store = PendingStore(str(tmp_path / "p.sqlite"))
    bot.config = SimpleNamespace(content_root=content_root, repo_path=repo_path)
    return bot


GLOSSARY = lambda term: {  # noqa: E731
    "tool": "add_glossary_term",
    "input": {"term": term, "definition": "d.", "link_slug": None},
}


async def test_present_batch_makes_one_row_per_op(tmp_path, content_root, content_repo):
    bot = make_bot(tmp_path, content_root, content_repo)
    chan = FakeChannel()
    msg = FakeIncoming("add kin, fathoms, apex", chan)
    outcome = engine_mod.ProposedWrite([GLOSSARY("Kin"), GLOSSARY("Fathoms"), GLOSSARY("Apex")])
    from lorebot.content.index import ContentIndex

    await bot._dispatch(msg, ContentIndex(content_root), outcome)

    # three separate preview messages, each headed k/N
    assert len(chan.messages) == 3
    assert "1/3 — glossary: kin" in chan.sent[0]
    assert "3/3 — glossary: apex" in chan.sent[2]
    # three independent pending rows, each carrying exactly its own op
    rows = bot.store.get(str(msg.author.id))
    assert len(rows) == 3
    assert [r.operations[0]["input"]["term"] for r in rows] == ["Kin", "Fathoms", "Apex"]
    assert all(r.state == AWAITING_CONFIRMATION for r in rows)
    # one shared batch_id across the batch
    assert len({r.batch_id for r in rows}) == 1 and rows[0].batch_id is not None
    # each preview message got its own ✅/❌
    for m in chan.messages:
        assert m.reactions == [main_mod.CONFIRM_EMOJI, main_mod.CANCEL_EMOJI]
    # lookups resolve each row to its message
    assert bot.store.get_by_preview_message_id(chan.messages[1].id).operations[0]["input"]["term"] == "Fathoms"


async def test_single_op_is_one_preview_one_row(tmp_path, content_root, content_repo):
    bot = make_bot(tmp_path, content_root, content_repo)
    chan = FakeChannel()
    msg = FakeIncoming("add grog", chan)
    from lorebot.content.index import ContentIndex

    await bot._dispatch(msg, ContentIndex(content_root), engine_mod.ProposedWrite([GLOSSARY("Grog")]))
    assert len(chan.messages) == 1
    assert "1/1" not in chan.sent[0]  # single op keeps today's header-less preview
    rows = bot.store.get(str(msg.author.id))
    assert len(rows) == 1 and rows[0].batch_id is None


async def test_cancel_all_clears_everything(tmp_path, content_root, content_repo):
    bot = make_bot(tmp_path, content_root, content_repo)
    bot._seen_messages = main_mod.SeenMessages()
    bot._allowed = lambda m: True
    chan = FakeChannel()
    author = FakeAuthor()
    # seed two pending previews
    bot.store.set_awaiting_confirmation(
        str(author.id),
        [
            {"operations": [GLOSSARY("Kin")], "preview_message_id": "m1", "context": {"label": "kin"}},
            {"operations": [GLOSSARY("Apex")], "preview_message_id": "m2", "context": {"label": "apex"}},
        ],
    )
    await bot.on_message(FakeIncoming("cancel all", chan, author))
    assert bot.store.get(str(author.id)) == []
    assert "Cancelled all 2" in chan.sent[0]


async def test_correction_replaces_outstanding(tmp_path, content_root, content_repo):
    bot = make_bot(tmp_path, content_root, content_repo)
    chan = FakeChannel()
    msg = FakeIncoming("actually make it grog", chan)
    from lorebot.content.index import ContentIndex

    outstanding = bot.store.set_awaiting_confirmation(
        str(msg.author.id),
        [
            {"operations": [GLOSSARY("Kin")], "preview_message_id": "m1", "context": {"label": "kin"}},
            {"operations": [GLOSSARY("Apex")], "preview_message_id": "m2", "context": {"label": "apex"}},
        ],
    )
    await bot._dispatch(
        msg, ContentIndex(content_root),
        engine_mod.ProposedWrite([GLOSSARY("Grog")]), outstanding=outstanding,
    )
    assert "Replacing 2 outstanding previews" in chan.sent[0]
    rows = bot.store.get(str(msg.author.id))
    assert len(rows) == 1  # old two cancelled, one new preview
    assert rows[0].operations[0]["input"]["term"] == "Grog"


async def test_unrelated_intent_keeps_outstanding_and_lists_them(tmp_path, content_root, content_repo):
    bot = make_bot(tmp_path, content_root, content_repo)
    chan = FakeChannel()
    msg = FakeIncoming("who was at the battle?", chan)
    from lorebot.content.index import ContentIndex

    outstanding = bot.store.set_awaiting_confirmation(
        str(msg.author.id),
        [
            {"operations": [GLOSSARY("Kin")], "preview_message_id": "m1", "context": {"label": "kin"}},
            {"operations": [GLOSSARY("Fathoms")], "preview_message_id": "m2", "context": {"label": "fathoms"}},
        ],
    )
    await bot._dispatch(
        msg, ContentIndex(content_root),
        engine_mod.Clarification(question="still waiting?"), outstanding=outstanding,
    )
    assert "Still waiting on 2 pending previews: kin, fathoms" in chan.sent[0]
    # outstanding previews are untouched
    assert len(bot.store.get(str(msg.author.id))) == 2
