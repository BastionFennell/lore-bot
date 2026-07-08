"""The LLM agentic loop — transport-agnostic (no discord imports).

Takes an :class:`EngineContext` (message text, author, recent messages, a
history-fetch callback, optional pending state + correction) and returns exactly
one :class:`Outcome` dataclass:

  * ProposedWrite  — Claude requested one or more write tools; the calls are
                     captured (in content order), NOT executed. The confirmation
                     layer takes over.
  * Clarification  — Claude called request_clarification.
  * Conversational — no_action, or plain end_turn text (an /ask answer, chit-chat).
  * Error          — refusal, API error, iteration cap, or a batch over the cap.

Read tools (fetch_channel_history, query_lore, search_lore) are executed inline;
their results are fed back and the loop continues. If Claude requests write
tools alongside other calls in one response, ALL the write calls are captured
and the rest are ignored (logged) — writes win over control tools. A single
turn may batch several writes (e.g. "add these five glossary terms"); the batch
is capped at :data:`MAX_WRITE_OPS`. ``fetch_channel_history`` is server-clamped
to 50 regardless of what the model asks for.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import llm
from . import search as search_mod
from .content import entries as entries_mod
from .content.index import ContentIndex
from .fuzzy import hints_block

log = logging.getLogger("lorebot.engine")

HISTORY_HARD_CAP = 50
MAX_ITERATIONS = 8
# A single proposal may batch several write ops; beyond this we refuse and ask
# the user to split the request (keeps previews/commits sane).
MAX_WRITE_OPS = 20


# --- Context & outcomes -----------------------------------------------------

@dataclass
class EngineContext:
    message_text: str
    author: str  # discord username, for attribution
    recent_messages: list[dict] = field(default_factory=list)  # [{author, content}]
    history_fetch: Callable[[int, str | None], list[dict]] | None = None
    pending: dict | None = None  # {question|preview, operation?} for a correction
    correction: str | None = None  # the user's follow-up while pending


@dataclass
class ProposedWrite:
    operations: list[dict]  # [{"tool": ..., "input": ...}, ...], in content order


@dataclass
class Clarification:
    question: str
    options: list[str] | None = None


@dataclass
class Conversational:
    text: str


@dataclass
class Error:
    message: str


Outcome = ProposedWrite | Clarification | Conversational | Error


# --- Prompt building --------------------------------------------------------

def _build_system(index: ContentIndex, ctx: EngineContext) -> list[dict]:
    stable = {
        "type": "text",
        "text": llm.STABLE_INSTRUCTIONS,
        "cache_control": {"type": "ephemeral"},
    }
    dynamic_parts = [
        "# Existing content index (slug | title | type | summary)",
        index.context_lines() or "(empty)",
        "",
        "# Fuzzy-match hints for names in the current message",
        hints_block(ctx.message_text, index),
    ]
    if ctx.pending:
        dynamic_parts += [
            "",
            "# Pending operation",
            "There is already a pending operation awaiting the user's answer:",
            str(ctx.pending.get("question") or ctx.pending.get("summary") or ctx.pending),
            "The user's message may answer/correct it, or be a new unrelated request. "
            "If it clearly starts something unrelated, call request_clarification to "
            "restate that you are still waiting on the pending item.",
        ]
    dynamic = {"type": "text", "text": "\n".join(dynamic_parts)}
    return [stable, dynamic]


def _build_user_message(ctx: EngineContext) -> str:
    parts = []
    if ctx.recent_messages:
        parts.append("# Recent channel messages (oldest first)")
        for m in ctx.recent_messages:
            parts.append(f"{m.get('author', '?')}: {m.get('content', '')}")
        parts.append("")
    parts.append("# Current message")
    parts.append(f"{ctx.author}: {ctx.message_text}")
    if ctx.correction:
        parts.append("")
        parts.append("# The user's follow-up (a correction/answer to the pending op)")
        parts.append(ctx.correction)
    return "\n".join(parts)


# --- Read-tool execution ----------------------------------------------------

def _execute_read(tool_name: str, tool_input: dict, ctx: EngineContext,
                  index: ContentIndex) -> str:
    try:
        if tool_name == "fetch_channel_history":
            limit = min(int(tool_input.get("limit", 10) or 10), HISTORY_HARD_CAP)
            before = tool_input.get("before_message_id")
            if ctx.history_fetch is None:
                return "(no channel history available)"
            msgs = ctx.history_fetch(limit, before) or []
            if not msgs:
                return "(no additional messages)"
            return "\n".join(f"{m.get('author', '?')}: {m.get('content', '')}" for m in msgs)
        if tool_name == "query_lore":
            slug = tool_input["slug"]
            try:
                return entries_mod.read_entry_text(index, slug)
            except entries_mod.EntryError as e:
                return f"ERROR: {e}"
        if tool_name == "search_lore":
            return search_mod.search_lore(tool_input.get("query", ""), index)
    except Exception as e:  # never let a read tool crash the loop
        log.exception("read tool %s failed", tool_name)
        return f"ERROR: {e}"
    return f"ERROR: unknown read tool {tool_name}"


# --- Content-block helpers (work with real SDK blocks and test fakes) -------

def _block_type(b) -> str:
    return getattr(b, "type", None) or (b.get("type") if isinstance(b, dict) else "")


def _block_attr(b, name):
    if isinstance(b, dict):
        return b.get(name)
    return getattr(b, name, None)


def _collect_tool_uses(content) -> list[dict]:
    out = []
    for b in content:
        if _block_type(b) == "tool_use":
            out.append(
                {
                    "id": _block_attr(b, "id"),
                    "name": _block_attr(b, "name"),
                    "input": _block_attr(b, "input") or {},
                }
            )
    return out


def _collect_text(content) -> str:
    return "".join(
        _block_attr(b, "text") or "" for b in content if _block_type(b) == "text"
    ).strip()


# --- The loop ---------------------------------------------------------------

def run_engine(
    *,
    client,
    model: str,
    context: EngineContext,
    index: ContentIndex,
    max_iterations: int = MAX_ITERATIONS,
    effort: str = "low",
) -> Outcome:
    system = _build_system(index, context)
    messages: list[dict] = [{"role": "user", "content": _build_user_message(context)}]

    for _ in range(max_iterations):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=llm.MAX_TOKENS,
                thinking={"type": "adaptive"},
                # Intent parsing is simple work; the default "high" effort
                # deliberates for minutes. "low" keeps replies snappy.
                output_config={"effort": effort},
                system=system,
                tools=llm.TOOLS,
                messages=messages,
            )
        except Exception as e:  # noqa: BLE001 — surface a readable error
            handled = _handle_api_exception(e)
            if handled is not None:
                return handled
            raise

        stop = getattr(resp, "stop_reason", None)
        content = getattr(resp, "content", []) or []

        if stop == "refusal":
            return Error("Sorry — I couldn't process that request.")

        if stop == "tool_use":
            tool_uses = _collect_tool_uses(content)

            # Write tools take priority over control; capture ALL of them in
            # content order (a batch), ignoring any non-write calls in the turn.
            writes = [t for t in tool_uses if t["name"] in llm.WRITE_TOOLS]
            if writes:
                ignored = len(tool_uses) - len(writes)
                if ignored:
                    log.info("taking %d write call(s), ignoring %d other call(s)",
                             len(writes), ignored)
                if len(writes) > MAX_WRITE_OPS:
                    return Error(
                        f"That's {len(writes)} changes in one go — I cap batches at "
                        f"{MAX_WRITE_OPS}. Please split the request into smaller messages."
                    )
                operations = [{"tool": w["name"], "input": w["input"]} for w in writes]
                return ProposedWrite(operations)

            control = next((t for t in tool_uses if t["name"] in llm.CONTROL_TOOLS), None)
            if control is not None:
                if control["name"] == "request_clarification":
                    return Clarification(
                        question=control["input"].get("question", "Could you clarify?"),
                        options=control["input"].get("options"),
                    )
                return Conversational(control["input"].get("reason", "Okay."))

            # Only read tools: execute all, feed results back, continue.
            reads = [t for t in tool_uses if t["name"] in llm.READ_TOOLS]
            if not reads:
                return Conversational(_collect_text(content) or "Okay.")
            messages.append({"role": "assistant", "content": content})
            results = []
            for t in reads:
                result = _execute_read(t["name"], t["input"], context, index)
                results.append(
                    {"type": "tool_result", "tool_use_id": t["id"], "content": result}
                )
            messages.append({"role": "user", "content": results})
            continue

        # end_turn (or anything else) with no tool call: conversational reply.
        return Conversational(_collect_text(content) or "Okay.")

    return Error("I got stuck working on that (too many steps). Please rephrase or try again.")


def _handle_api_exception(exc: Exception) -> Outcome | None:
    """Map typed anthropic exceptions to a readable Error, most-specific first."""
    try:
        import anthropic
    except Exception:
        return None
    if isinstance(exc, anthropic.RateLimitError):
        return Error("The AI service is rate-limited right now — please try again shortly.")
    if isinstance(exc, anthropic.APIConnectionError):
        return Error("I couldn't reach the AI service (network error). Please try again.")
    if isinstance(exc, anthropic.APIStatusError):
        return Error(f"The AI service returned an error ({exc.status_code}). Please try again.")
    return None
