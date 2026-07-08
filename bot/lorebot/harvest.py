"""RP-harvest core: high-water marks, transcript formatting, and the engine glue.

Deliberately transport-free (no discord imports) so every piece is unit-testable:

  * :class:`HarvestMarks` — a tiny SQLite table (same db as pending state) recording,
    per RP source, the newest message id already harvested and when. The mark advances
    when a harvest RUNS (not on confirm), so ``harvest`` is predictably incremental and
    ``harvest from start`` is the redo lever.
  * :func:`prepare_harvest` — turn a batch of already-fetched messages (oldest-first,
    capped by the caller) into a formatted transcript, the new mark, a count, a date
    range, and a partial flag.
  * :func:`run_harvest` — build a synthetic :class:`~lorebot.engine.EngineContext` around
    the transcript and drive the existing engine, so the outcome flows through the normal
    preview/✅/❌ dispatch.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from . import engine as engine_mod
from . import llm

# Play-by-post is low-volume, but a first ``harvest from start`` on a busy thread could
# be large; cap each source per run so previews/commits and the API call stay bounded.
# If we hit the cap the run is "partial" and re-running ``harvest`` continues from the
# mark we advanced to.
MAX_MESSAGES_PER_RUN = 400


# --- High-water marks -------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS harvest_marks (
    source_id       TEXT PRIMARY KEY,
    last_message_id TEXT,
    harvested_at    TEXT
);
"""


@dataclass
class Mark:
    source_id: str
    last_message_id: str | None
    harvested_at: str | None


class HarvestMarks:
    """Per-source high-water marks, in the same SQLite db as pending state."""

    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def get(self, source_id: str) -> Mark | None:
        cur = self._conn.execute(
            "SELECT * FROM harvest_marks WHERE source_id = ?", (str(source_id),)
        )
        row = cur.fetchone()
        if row is None:
            return None
        return Mark(
            source_id=row["source_id"],
            last_message_id=row["last_message_id"],
            harvested_at=row["harvested_at"],
        )

    def last_message_id(self, source_id: str) -> str | None:
        mark = self.get(source_id)
        return mark.last_message_id if mark else None

    def advance(self, source_id: str, last_message_id: str, harvested_at: str) -> None:
        """Set (or move) the mark to ``last_message_id`` — the newest FETCHED id."""
        self._conn.execute(
            "INSERT INTO harvest_marks (source_id, last_message_id, harvested_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(source_id) DO UPDATE SET "
            "last_message_id = excluded.last_message_id, harvested_at = excluded.harvested_at",
            (str(source_id), str(last_message_id), str(harvested_at)),
        )
        self._conn.commit()

    def reset(self, source_id: str) -> None:
        """Drop the mark so the next harvest reads the source from the start."""
        self._conn.execute("DELETE FROM harvest_marks WHERE source_id = ?", (str(source_id),))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


# --- Transcript formatting --------------------------------------------------

def _date_str(created_at) -> str:
    """`YYYY-MM-DD` from a datetime (Discord) or an ISO-ish string (tests)."""
    if created_at is None:
        return "????-??-??"
    if hasattr(created_at, "strftime"):
        return created_at.strftime("%Y-%m-%d")
    return str(created_at)[:10]


def format_line(message: dict) -> str:
    """One transcript line: ``[YYYY-MM-DD] author: content``."""
    date = _date_str(message.get("created_at"))
    author = message.get("author") or "?"
    content = (message.get("content") or "").strip()
    return f"[{date}] {author}: {content}"


@dataclass
class PreparedHarvest:
    transcript: str  # formatted, kept lines joined by newlines ("" if nothing kept)
    new_mark: str | None  # newest FETCHED message id, or None if nothing was fetched
    count: int  # number of kept (non-bot, non-empty) messages
    partial: bool  # True if the fetch hit the per-run cap (more may remain)
    date_range: str  # "YYYY-MM-DD" or "YYYY-MM-DD → YYYY-MM-DD" over kept messages


def prepare_harvest(messages: list[dict], cap: int = MAX_MESSAGES_PER_RUN) -> PreparedHarvest:
    """Format a fetched batch (oldest-first) into a :class:`PreparedHarvest`.

    The mark advances to the newest FETCHED id regardless of what's kept, so bot/empty
    messages don't get re-fetched forever. The transcript skips bot and empty/system
    messages. ``partial`` is set when the batch filled the cap (there may be more).
    """
    new_mark = str(messages[-1]["id"]) if messages else None
    kept = [
        m for m in messages
        if not m.get("is_bot") and (m.get("content") or "").strip()
    ]
    lines = [format_line(m) for m in kept]
    if kept:
        first, last = _date_str(kept[0].get("created_at")), _date_str(kept[-1].get("created_at"))
        date_range = first if first == last else f"{first} → {last}"
    else:
        date_range = ""
    return PreparedHarvest(
        transcript="\n".join(lines),
        new_mark=new_mark,
        count=len(kept),
        partial=len(messages) >= cap,
        date_range=date_range,
    )


# --- Engine glue ------------------------------------------------------------

def build_harvest_context(transcript: str, author: str) -> engine_mod.EngineContext:
    """A synthetic context that feeds the harvest transcript through the engine as if
    the invoking user had pasted it, prefixed with the harvest instructions."""
    return engine_mod.EngineContext(
        message_text=f"{llm.HARVEST_INSTRUCTIONS}\n\n{transcript}",
        author=author,
        recent_messages=[],
        history_fetch=None,
        pending=None,
        correction=None,
    )


def run_harvest(
    *,
    client,
    model: str,
    index,
    transcript: str,
    author: str,
    effort: str = "low",
) -> engine_mod.Outcome:
    """Drive the existing engine over a harvest transcript; returns a normal Outcome."""
    ctx = build_harvest_context(transcript, author)
    return engine_mod.run_engine(
        client=client, model=model, context=ctx, index=index, effort=effort
    )
