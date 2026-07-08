from __future__ import annotations

from lorebot.pending import (
    AWAITING_CLARIFICATION,
    AWAITING_CONFIRMATION,
    PendingStore,
)


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def make_store(tmp_path, clock):
    return PendingStore(str(tmp_path / "pending.sqlite"), now=clock)


def test_confirmation_transition_and_lookup(tmp_path):
    store = make_store(tmp_path, Clock())
    op = {"tool": "update_field", "input": {"slug": "x", "field": "status", "value": "dead"}}
    store.set_awaiting_confirmation("u1", operation=op, preview_message_id="m99",
                                    context={"message_text": "kill x"})
    p = store.get("u1")
    assert p.state == AWAITING_CONFIRMATION
    assert p.operation == op
    assert p.context["message_text"] == "kill x"
    assert store.get_by_preview_message_id("m99").user_id == "u1"


def test_clarification_transition(tmp_path):
    store = make_store(tmp_path, Clock())
    store.set_awaiting_clarification("u1", question="Which captain?",
                                     context={"message_text": "update the captain"})
    p = store.get("u1")
    assert p.state == AWAITING_CLARIFICATION
    assert p.question == "Which captain?"


def test_one_pending_per_user(tmp_path):
    store = make_store(tmp_path, Clock())
    store.set_awaiting_clarification("u1", question="Q1")
    store.set_awaiting_confirmation("u1", operation={"tool": "no_action", "input": {}})
    p = store.get("u1")
    assert p.state == AWAITING_CONFIRMATION  # replaced, not duplicated
    assert p.question is None


def test_clear(tmp_path):
    store = make_store(tmp_path, Clock())
    store.set_awaiting_clarification("u1", question="Q")
    store.clear("u1")
    assert store.get("u1") is None


def test_expiry_with_injected_clock(tmp_path):
    clock = Clock(1000.0)
    store = make_store(tmp_path, clock)
    store.set_awaiting_clarification("u1", question="old")
    clock.t = 1000.0 + 29 * 60  # 29 minutes later — not expired
    assert store.expire(30 * 60) == []
    assert store.get("u1") is not None
    clock.t = 1000.0 + 31 * 60  # 31 minutes — expired
    expired = store.expire(30 * 60)
    assert [p.user_id for p in expired] == ["u1"]
    assert store.get("u1") is None


def test_correction_path_roundtrips_context(tmp_path):
    """The stored context is what the transport replays on a correction."""
    store = make_store(tmp_path, Clock())
    op = {"tool": "append_to_entry", "input": {"slug": "x", "section_heading": "History", "content": "y"}}
    store.set_awaiting_confirmation("u1", operation=op,
                                    context={"message_text": "add history to x"})
    p = store.get("u1")
    # transport rebuilds the engine context + appends the correction
    assert p.context["message_text"] == "add history to x"
    assert p.operation["input"]["section_heading"] == "History"
