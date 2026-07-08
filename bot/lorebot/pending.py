"""SQLite-backed pending-operation state machine.

One row per pending ITEM (not per user). A single-op proposal is one row
(``batch_id`` null). A multi-op batch is N rows sharing a ``batch_id`` (uuid),
each row carrying exactly the op(s) its ``✅`` applies (a single-op list). An
``AWAITING_CLARIFICATION`` question is a single row. A user can therefore have
several independently-confirmable pending items at once.

Each row records the operation(s) (JSON list), the clarifying question (if any),
the preview message id (for the reaction handler), the batch id (if part of a
batch), and a snapshot of the conversation context used to build it (so a
correction can re-run the engine with the original context).

The clock is injectable (``now`` callable) so expiry is testable without sleeps.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass

AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
AWAITING_CLARIFICATION = "AWAITING_CLARIFICATION"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pending (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id            TEXT NOT NULL,
    state              TEXT NOT NULL,
    operations         TEXT,
    question           TEXT,
    preview_message_id TEXT,
    batch_id           TEXT,
    context            TEXT,
    created_at         REAL NOT NULL
);
"""


@dataclass
class Pending:
    user_id: str
    state: str
    operations: list[dict] | None  # the op(s) this item's ✅ applies, or None
    question: str | None
    preview_message_id: str | None
    context: dict | None
    created_at: float
    id: int | None = None
    batch_id: str | None = None


class PendingStore:
    def __init__(self, db_path: str, now=time.time):
        self._now = now
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._migrate()
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    # --- internal ----------------------------------------------------------
    def _migrate(self) -> None:
        """The multi-item model renamed/added columns. A pre-existing table from
        the single-row model is incompatible; drop it (pending state is ephemeral
        — it expires in 30 minutes anyway)."""
        cur = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pending'"
        )
        if cur.fetchone() is None:
            return
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(pending)")}
        if "batch_id" not in cols or "operations" not in cols:
            self._conn.execute("DROP TABLE pending")
            self._conn.commit()

    def _row_to_pending(self, row: sqlite3.Row | None) -> Pending | None:
        if row is None:
            return None
        return Pending(
            id=row["id"],
            user_id=row["user_id"],
            state=row["state"],
            operations=json.loads(row["operations"]) if row["operations"] else None,
            question=row["question"],
            preview_message_id=row["preview_message_id"],
            batch_id=row["batch_id"],
            context=json.loads(row["context"]) if row["context"] else None,
            created_at=row["created_at"],
        )

    def _insert(self, p: Pending) -> Pending:
        cur = self._conn.execute(
            "INSERT INTO pending "
            "(user_id, state, operations, question, preview_message_id, batch_id, context, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                p.user_id,
                p.state,
                json.dumps(p.operations) if p.operations is not None else None,
                p.question,
                p.preview_message_id,
                p.batch_id,
                json.dumps(p.context) if p.context is not None else None,
                p.created_at,
            ),
        )
        self._conn.commit()
        p.id = cur.lastrowid
        return p

    # --- API ---------------------------------------------------------------
    def get(self, user_id: str) -> list[Pending]:
        """All pending items for a user, oldest first."""
        cur = self._conn.execute(
            "SELECT * FROM pending WHERE user_id = ? ORDER BY id", (str(user_id),)
        )
        return [self._row_to_pending(r) for r in cur.fetchall()]

    def get_by_preview_message_id(self, message_id: str) -> Pending | None:
        cur = self._conn.execute(
            "SELECT * FROM pending WHERE preview_message_id = ?", (str(message_id),)
        )
        return self._row_to_pending(cur.fetchone())

    def set_awaiting_confirmation(
        self,
        user_id: str,
        items: list[dict],
        *,
        context: dict | None = None,
        batch_id: str | None = None,
    ) -> list[Pending]:
        """Replace the user's pending state with one confirmation row per item.

        ``items`` is a list of dicts, each ``{"operations": [...],
        "preview_message_id": ..., "context": {...}?}``. A batch (len > 1) gets a
        shared ``batch_id`` (generated if not supplied); a single item stays
        unbatched (``batch_id`` null).
        """
        user_id = str(user_id)
        self.clear(user_id)
        if batch_id is None and len(items) > 1:
            batch_id = uuid.uuid4().hex
        out: list[Pending] = []
        for it in items:
            pmid = it.get("preview_message_id")
            out.append(
                self._insert(
                    Pending(
                        user_id=user_id,
                        state=AWAITING_CONFIRMATION,
                        operations=it["operations"],
                        question=None,
                        preview_message_id=str(pmid) if pmid is not None else None,
                        batch_id=batch_id,
                        context=it.get("context", context),
                        created_at=self._now(),
                    )
                )
            )
        return out

    def set_awaiting_clarification(
        self,
        user_id: str,
        question: str,
        context: dict | None = None,
        operations: list[dict] | None = None,
    ) -> Pending:
        user_id = str(user_id)
        self.clear(user_id)
        return self._insert(
            Pending(
                user_id=user_id,
                state=AWAITING_CLARIFICATION,
                operations=operations,
                question=question,
                preview_message_id=None,
                batch_id=None,
                context=context,
                created_at=self._now(),
            )
        )

    def clear(self, user_id: str) -> None:
        """Clear every pending item for a user."""
        self._conn.execute("DELETE FROM pending WHERE user_id = ?", (str(user_id),))
        self._conn.commit()

    def clear_item(self, *, preview_message_id: str | None = None, row_id: int | None = None) -> None:
        """Clear a single pending row, by preview message id or by row id."""
        if row_id is not None:
            self._conn.execute("DELETE FROM pending WHERE id = ?", (int(row_id),))
        elif preview_message_id is not None:
            self._conn.execute(
                "DELETE FROM pending WHERE preview_message_id = ?", (str(preview_message_id),)
            )
        self._conn.commit()

    def expire(self, ttl_seconds: float) -> list[Pending]:
        """Delete and return all pending rows older than ``ttl_seconds`` (per item)."""
        cutoff = self._now() - ttl_seconds
        cur = self._conn.execute("SELECT * FROM pending WHERE created_at < ?", (cutoff,))
        expired = [self._row_to_pending(r) for r in cur.fetchall()]
        self._conn.execute("DELETE FROM pending WHERE created_at < ?", (cutoff,))
        self._conn.commit()
        return expired

    def close(self) -> None:
        self._conn.close()
