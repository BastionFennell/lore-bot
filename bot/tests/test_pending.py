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


def _item(op, mid, label=None):
    ctx = {"message_text": "m"}
    if label:
        ctx["label"] = label
    return {"operations": [op], "preview_message_id": mid, "context": ctx}


def test_confirmation_transition_and_lookup(tmp_path):
    store = make_store(tmp_path, Clock())
    op = {"tool": "update_field", "input": {"slug": "x", "field": "status", "value": "dead"}}
    store.set_awaiting_confirmation(
        "u1", [{"operations": [op], "preview_message_id": "m99",
                "context": {"message_text": "kill x"}}]
    )
    items = store.get("u1")
    assert len(items) == 1
    p = items[0]
    assert p.state == AWAITING_CONFIRMATION
    assert p.operations == [op]
    assert p.batch_id is None  # a single-op proposal is unbatched
    assert p.context["message_text"] == "kill x"
    assert store.get_by_preview_message_id("m99").user_id == "u1"


def test_batch_is_one_row_per_op_sharing_a_batch_id(tmp_path):
    store = make_store(tmp_path, Clock())
    ops = [
        {"tool": "add_glossary_term", "input": {"term": "Kin", "definition": "d", "link_slug": None}},
        {"tool": "add_glossary_term", "input": {"term": "Apex", "definition": "d", "link_slug": None}},
        {"tool": "add_timeline_event",
         "input": {"date_in_fiction": "0849-02-11", "description": "battle", "related_slugs": None}},
    ]
    rows = store.set_awaiting_confirmation(
        "u1", [_item(ops[0], "m1"), _item(ops[1], "m2"), _item(ops[2], "m3")]
    )
    items = store.get("u1")
    assert len(items) == 3
    # each row carries exactly the single op its ✅ applies
    assert [i.operations for i in items] == [[ops[0]], [ops[1]], [ops[2]]]
    # all three share one non-null batch_id
    batch_ids = {i.batch_id for i in items}
    assert len(batch_ids) == 1 and next(iter(batch_ids)) is not None
    # lookups resolve to the right row
    assert store.get_by_preview_message_id("m2").operations == [ops[1]]
    assert [r.id for r in rows] == [i.id for i in items]


def test_new_proposal_replaces_prior_pending(tmp_path):
    store = make_store(tmp_path, Clock())
    op = {"tool": "add_glossary_term", "input": {"term": "Kin", "definition": "d", "link_slug": None}}
    store.set_awaiting_confirmation("u1", [_item(op, "m1"), _item(op, "m2")])
    store.set_awaiting_clarification("u1", question="Q1")
    items = store.get("u1")
    assert len(items) == 1 and items[0].state == AWAITING_CLARIFICATION


def test_clear_item_by_message_id_and_row_id(tmp_path):
    store = make_store(tmp_path, Clock())
    op = {"tool": "add_glossary_term", "input": {"term": "Kin", "definition": "d", "link_slug": None}}
    rows = store.set_awaiting_confirmation("u1", [_item(op, "m1"), _item(op, "m2"), _item(op, "m3")])
    store.clear_item(preview_message_id="m2")
    assert [i.preview_message_id for i in store.get("u1")] == ["m1", "m3"]
    store.clear_item(row_id=rows[0].id)
    assert [i.preview_message_id for i in store.get("u1")] == ["m3"]


def test_clear_all(tmp_path):
    store = make_store(tmp_path, Clock())
    op = {"tool": "add_glossary_term", "input": {"term": "Kin", "definition": "d", "link_slug": None}}
    store.set_awaiting_confirmation("u1", [_item(op, "m1"), _item(op, "m2")])
    store.clear("u1")
    assert store.get("u1") == []


def test_clarification_transition(tmp_path):
    store = make_store(tmp_path, Clock())
    store.set_awaiting_clarification("u1", question="Which captain?",
                                     context={"message_text": "update the captain"})
    items = store.get("u1")
    assert len(items) == 1
    assert items[0].state == AWAITING_CLARIFICATION
    assert items[0].question == "Which captain?"


def test_per_item_expiry(tmp_path):
    clock = Clock(1000.0)
    store = make_store(tmp_path, clock)
    op = {"tool": "add_glossary_term", "input": {"term": "Kin", "definition": "d", "link_slug": None}}
    # two items created 10 minutes apart
    store.set_awaiting_confirmation("u1", [_item(op, "m1")])
    clock.t = 1000.0 + 10 * 60
    # second call would clear u1; use a distinct user to keep both alive
    store.set_awaiting_confirmation("u2", [_item(op, "m2")])
    clock.t = 1000.0 + 31 * 60  # u1 (31m) expired, u2 (21m) not
    expired = store.expire(30 * 60)
    assert [p.user_id for p in expired] == ["u1"]
    assert store.get("u1") == []
    assert len(store.get("u2")) == 1


def test_expiry_returns_each_item(tmp_path):
    clock = Clock(1000.0)
    store = make_store(tmp_path, clock)
    op = {"tool": "add_glossary_term", "input": {"term": "Kin", "definition": "d", "link_slug": None}}
    store.set_awaiting_confirmation("u1", [_item(op, "m1"), _item(op, "m2"), _item(op, "m3")])
    clock.t = 1000.0 + 31 * 60
    expired = store.expire(30 * 60)
    assert len(expired) == 3  # every row of the batch expires independently
    assert store.get("u1") == []


def test_correction_path_roundtrips_context(tmp_path):
    """The stored context is what the transport replays on a correction."""
    store = make_store(tmp_path, Clock())
    op = {"tool": "append_to_entry", "input": {"slug": "x", "section_heading": "History", "content": "y"}}
    store.set_awaiting_confirmation(
        "u1", [{"operations": [op], "preview_message_id": "m1",
                "context": {"message_text": "add history to x"}}]
    )
    p = store.get("u1")[0]
    # transport rebuilds the engine context + appends the correction
    assert p.context["message_text"] == "add history to x"
    assert p.operations[0]["input"]["section_heading"] == "History"
