"""Anthropic client wrapper + tool definitions (JSON schemas).

Tool sets:
  READ    — executed by the engine, no confirmation (fetch_channel_history,
            query_lore, search_lore).
  WRITE   — captured, never executed by the engine; the confirmation layer
            takes over (create_entry, append_to_entry, update_field,
            add_glossary_term, add_timeline_event).
  CONTROL — terminal, non-write outcomes (request_clarification, no_action).

Every tool is ``strict: True`` with ``additionalProperties: false`` and every
property listed in ``required`` (optionals are nullable via a union type).
``body_sections`` uses the strict-friendly array-of-pairs shape instead of an
open-ended object map.
"""

from __future__ import annotations

import os

DEFAULT_MODEL = "claude-opus-4-8"
MAX_TOKENS = 8192

READ_TOOLS = {"fetch_channel_history", "query_lore", "search_lore"}
WRITE_TOOLS = {
    "create_entry",
    "append_to_entry",
    "update_field",
    "add_glossary_term",
    "add_timeline_event",
}
CONTROL_TOOLS = {"request_clarification", "no_action"}


def _tool(name: str, description: str, schema: dict) -> dict:
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": schema,
    }


def _obj(properties: dict, required: list[str]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


TOOLS: list[dict] = [
    # --- Read tools ---------------------------------------------------------
    _tool(
        "fetch_channel_history",
        "Fetch up to `limit` additional messages from #captains-log, newest-first "
        "from an optional starting point. Use for multi-message lore dumps or "
        "'as we discussed earlier'. Server-clamped to 50 messages, this channel only.",
        _obj(
            {
                "limit": {"type": "integer", "description": "How many messages (clamped to 50)."},
                "before_message_id": {
                    "type": ["string", "null"],
                    "description": "Fetch messages before this message id, or null for the latest.",
                },
            },
            ["limit", "before_message_id"],
        ),
    ),
    _tool(
        "query_lore",
        "Return the full current content (frontmatter + body) of one entry by slug. "
        "MUST be called before proposing an append/update to an existing entry.",
        _obj({"slug": {"type": "string"}}, ["slug"]),
    ),
    _tool(
        "search_lore",
        "Keyword/fuzzy search over titles, tags, summaries, and body text. Returns "
        "matching slugs with summaries and a snippet. Use to resolve vague references.",
        _obj({"query": {"type": "string"}}, ["query"]),
    ),
    # --- Write tools --------------------------------------------------------
    _tool(
        "create_entry",
        "Create a new lore/character/map entry from the type's template. The bot "
        "generates the slug, checks uniqueness, and previews the full rendered entry.",
        _obj(
            {
                "type": {
                    "type": "string",
                    "enum": ["location", "faction", "npc", "concept", "character", "map"],
                },
                "title": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "summary": {"type": "string", "description": "One-sentence description."},
                "body_sections": {
                    "type": "array",
                    "description": "Body sections as (heading, content) pairs, e.g. heading 'Description'.",
                    "items": _obj(
                        {"heading": {"type": "string"}, "content": {"type": "string"}},
                        ["heading", "content"],
                    ),
                },
            },
            ["type", "title", "tags", "summary", "body_sections"],
        ),
    ),
    _tool(
        "append_to_entry",
        "Append content under an existing '## heading' in an entry (or a new heading, "
        "flagged in the preview). Call query_lore first to fit what is already there.",
        _obj(
            {
                "slug": {"type": "string"},
                "section_heading": {"type": "string", "description": "Heading name, e.g. 'Recent History'."},
                "content": {"type": "string"},
            },
            ["slug", "section_heading", "content"],
        ),
    ),
    _tool(
        "update_field",
        "Change one frontmatter field (status, disposition, affiliation, tags, "
        "summary, title, region, leader, etc.). Validated against the type's schema.",
        _obj(
            {
                "slug": {"type": "string"},
                "field": {"type": "string"},
                "value": {
                    "type": ["string", "array"],
                    "items": {"type": "string"},
                    "description": "Scalar string, or a list of strings for tags.",
                },
            },
            ["slug", "field", "value"],
        ),
    ),
    _tool(
        "add_glossary_term",
        "Add or update a term in glossary.yaml. Updating an existing term replaces it.",
        _obj(
            {
                "term": {"type": "string"},
                "definition": {"type": "string"},
                "link_slug": {
                    "type": ["string", "null"],
                    "description": "Optional slug of a lore entry to link the term to.",
                },
            },
            ["term", "definition", "link_slug"],
        ),
    ),
    _tool(
        "add_timeline_event",
        "Append an in-fiction event to timeline/events.yaml.",
        _obj(
            {
                "date_in_fiction": {
                    "type": "string",
                    "description": "Sortable in-fiction date, e.g. '0847-03-12'.",
                },
                "description": {"type": "string"},
                "related_slugs": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                    "description": "Optional related entry slugs, or null.",
                },
            },
            ["date_in_fiction", "description", "related_slugs"],
        ),
    ),
    # --- Control tools ------------------------------------------------------
    _tool(
        "request_clarification",
        "Ask the user a clarifying question when a target cannot be resolved "
        "confidently. The operation becomes pending until they answer.",
        _obj(
            {
                "question": {"type": "string"},
                "options": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                    "description": "Optional list of choices to present, or null.",
                },
            },
            ["question", "options"],
        ),
    ),
    _tool(
        "no_action",
        "The message was not an actionable edit (chit-chat, or an answer already "
        "consumed). Respond conversationally with the given reason.",
        _obj({"reason": {"type": "string"}}, ["reason"]),
    ),
]


STABLE_INSTRUCTIONS = """\
You are LoreBot, the authoring assistant for the "Sundered Isles Chronicle", a \
two-player play-by-post RP campaign (Ironsworn: Starforged). GitHub is the source \
of truth; you translate natural-language requests in the #captains-log channel into \
structured content operations, which are previewed and committed only on the user's \
explicit confirmation. You never write files directly — you call a tool and the \
confirmation layer handles the rest.

Rules you must follow:
1. AMBIGUITY: Before proposing a write, the message's candidate entity names are \
fuzzy-matched against existing slugs/titles and given to you as hints. You may call \
search_lore to investigate further. If a target still cannot be resolved confidently \
(e.g. "update the captain's page" with two captains), you MUST call \
request_clarification rather than guess.
2. READ BEFORE WRITE: You MUST call query_lore(slug) before proposing an \
append_to_entry or update_field to an existing entry, so your proposal fits what is \
already there (no redundant or contradictory additions, correct section targeting).
3. BATCH WHEN ASKED: When the user asks for several additions at once (e.g. \
"add these five terms", a lore dump with multiple items), emit multiple write \
tool calls in a SINGLE response — one call per requested item — and do not drop \
any item. All the calls are previewed together and committed atomically. For a \
single requested change, emit a single write call. Mixed types in one batch are \
fine (e.g. some glossary terms plus a timeline event).
4. WHEN NOT TO WRITE: If the message is chit-chat, a question you answered, or an \
answer already consumed by a pending operation, call no_action. If you cannot \
confidently resolve a target, call request_clarification.
5. QUESTIONS (/ask): Use read tools (search_lore, query_lore) to gather relevant \
entries, then answer conversationally with the slugs you cited. Do not call a write \
tool for a question.
6. Cross-links use {{slug}} syntax in body text. Prefer linking to existing slugs.
"""


def build_client(api_key: str | None = None):
    """Construct a real Anthropic client. Imported lazily so tests never need it."""
    import anthropic

    return anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))


def model_from_env() -> str:
    return os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)
