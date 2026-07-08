from __future__ import annotations

import json

from lorebot.engine import (
    Clarification,
    Conversational,
    EngineContext,
    Error,
    ProposedWrite,
    run_engine,
)
from tests.conftest import git
from tests.fakes import FakeAnthropicClient, FakeMessage, FakeText, FakeToolUse


def _ctx(text="do a thing"):
    return EngineContext(message_text=text, author="you", recent_messages=[])


def test_read_then_write_returns_proposed_write_repo_untouched(content_repo, index):
    client = FakeAnthropicClient([
        FakeMessage("tool_use", [FakeToolUse("query_lore", {"slug": "captain-powderkeg"}, "t1")]),
        FakeMessage("tool_use", [FakeToolUse(
            "append_to_entry",
            {"slug": "captain-powderkeg", "section_heading": "Recent History",
             "content": "She struck again."}, "t2")]),
    ])
    out = run_engine(client=client, model="m", context=_ctx("add to powderkeg"), index=index)
    assert isinstance(out, ProposedWrite)
    assert len(out.operations) == 1
    assert out.operations[0]["tool"] == "append_to_entry"
    assert len(client.calls) == 2  # read executed, then write captured
    # repo is untouched — the engine never writes
    assert git(content_repo, "status", "--porcelain").stdout.strip() == ""


def test_append_proposal_built_after_reading_existing_content(index):
    client = FakeAnthropicClient([
        FakeMessage("tool_use", [FakeToolUse("query_lore", {"slug": "captain-powderkeg"}, "t1")]),
        FakeMessage("tool_use", [FakeToolUse(
            "append_to_entry",
            {"slug": "captain-powderkeg", "section_heading": "Recent History",
             "content": "X"}, "t2")]),
    ])
    out = run_engine(client=client, model="m", context=_ctx(), index=index)
    assert isinstance(out, ProposedWrite)
    # the second API call's messages include the tool_result carrying the entry text
    second = client.calls[1]["messages"]
    flat = json.dumps(second, default=str)
    assert "Powderkeg" in flat  # the queried entry content was fed back


def test_request_clarification_returns_clarification(index):
    client = FakeAnthropicClient([
        FakeMessage("tool_use", [FakeToolUse(
            "request_clarification",
            {"question": "Which captain — Ferocious or Powderkeg?",
             "options": ["Ferocious", "Powderkeg"]}, "t1")]),
    ])
    out = run_engine(client=client, model="m", context=_ctx("update the captain"), index=index)
    assert isinstance(out, Clarification)
    assert "captain" in out.question.lower()
    assert out.options == ["Ferocious", "Powderkeg"]


def test_plain_end_turn_returns_conversational(index):
    client = FakeAnthropicClient([
        FakeMessage("end_turn", [FakeText("House Veldrane is a ruthless naval house.")]),
    ])
    out = run_engine(client=client, model="m", context=_ctx("what is house veldrane?"), index=index)
    assert isinstance(out, Conversational)
    assert "Veldrane" in out.text


def test_no_action_returns_conversational(index):
    client = FakeAnthropicClient([
        FakeMessage("tool_use", [FakeToolUse("no_action", {"reason": "Just chit-chat."}, "t1")]),
    ])
    out = run_engine(client=client, model="m", context=_ctx("lol nice"), index=index)
    assert isinstance(out, Conversational)
    assert out.text == "Just chit-chat."


def test_write_takes_priority_over_other_calls(index):
    client = FakeAnthropicClient([
        FakeMessage("tool_use", [
            FakeToolUse("search_lore", {"query": "x"}, "t1"),
            FakeToolUse("update_field",
                        {"slug": "house-veldrane", "field": "disposition", "value": "neutral"}, "t2"),
        ]),
    ])
    out = run_engine(client=client, model="m", context=_ctx(), index=index)
    assert isinstance(out, ProposedWrite)
    assert len(out.operations) == 1
    assert out.operations[0]["tool"] == "update_field"
    assert len(client.calls) == 1  # no read was executed; write short-circuits


def test_batch_of_five_glossary_terms_captured_in_order(index):
    terms = ["Kin", "Fathoms", "Apex", "Tidebound", "Tidal Schools"]
    client = FakeAnthropicClient([
        FakeMessage("tool_use", [
            FakeToolUse("add_glossary_term",
                        {"term": t, "definition": f"def {t}", "link_slug": None}, f"t{i}")
            for i, t in enumerate(terms)
        ]),
    ])
    out = run_engine(client=client, model="m", context=_ctx("add these five"), index=index)
    assert isinstance(out, ProposedWrite)
    assert [o["input"]["term"] for o in out.operations] == terms  # all, in order
    assert all(o["tool"] == "add_glossary_term" for o in out.operations)


def test_mixed_batch_glossary_and_timeline(index):
    client = FakeAnthropicClient([
        FakeMessage("tool_use", [
            FakeToolUse("add_glossary_term",
                        {"term": "Kin", "definition": "d", "link_slug": None}, "t1"),
            FakeToolUse("add_timeline_event",
                        {"date_in_fiction": "0849-02-11", "description": "A battle",
                         "related_slugs": None}, "t2"),
        ]),
    ])
    out = run_engine(client=client, model="m", context=_ctx(), index=index)
    assert isinstance(out, ProposedWrite)
    assert [o["tool"] for o in out.operations] == ["add_glossary_term", "add_timeline_event"]


def test_writes_win_when_mixed_with_control_and_reads(index):
    client = FakeAnthropicClient([
        FakeMessage("tool_use", [
            FakeToolUse("search_lore", {"query": "x"}, "t0"),
            FakeToolUse("add_glossary_term",
                        {"term": "Kin", "definition": "d", "link_slug": None}, "t1"),
            FakeToolUse("no_action", {"reason": "nvm"}, "t2"),
            FakeToolUse("add_glossary_term",
                        {"term": "Apex", "definition": "d", "link_slug": None}, "t3"),
        ]),
    ])
    out = run_engine(client=client, model="m", context=_ctx(), index=index)
    assert isinstance(out, ProposedWrite)
    # both writes captured, control + read ignored, no read executed (short-circuit)
    assert [o["input"]["term"] for o in out.operations] == ["Kin", "Apex"]
    assert len(client.calls) == 1


def test_batch_over_cap_returns_error(index):
    client = FakeAnthropicClient([
        FakeMessage("tool_use", [
            FakeToolUse("add_glossary_term",
                        {"term": f"T{i}", "definition": "d", "link_slug": None}, f"t{i}")
            for i in range(21)
        ]),
    ])
    out = run_engine(client=client, model="m", context=_ctx(), index=index)
    assert isinstance(out, Error)
    assert "split" in out.message.lower()


def test_iteration_cap_returns_error(index):
    client = FakeAnthropicClient([
        FakeMessage("tool_use", [FakeToolUse("search_lore", {"query": "loop"}, f"t{i}")])
        for i in range(3)
    ])
    out = run_engine(client=client, model="m", context=_ctx(), index=index, max_iterations=3)
    assert isinstance(out, Error)
    assert len(client.calls) == 3


def test_effort_reaches_the_api_call(index):
    client = FakeAnthropicClient([FakeMessage("end_turn", [FakeText("hi")])])
    run_engine(client=client, model="m", context=_ctx(), index=index, effort="medium")
    assert client.calls[0]["output_config"] == {"effort": "medium"}


def test_effort_defaults_to_low(index):
    client = FakeAnthropicClient([FakeMessage("end_turn", [FakeText("hi")])])
    run_engine(client=client, model="m", context=_ctx(), index=index)
    assert client.calls[0]["output_config"] == {"effort": "low"}
