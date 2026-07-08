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
    assert out.operation["tool"] == "append_to_entry"
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
    assert out.operation["tool"] == "update_field"
    assert len(client.calls) == 1  # no read was executed; write short-circuits


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
