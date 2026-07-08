"""SQLite-backed pending-operation state machine.

One pending operation per user at a time. A pending row records the operation(s)
(as a JSON list — a proposal may batch several write ops), the clarifying
question (if any), the preview message id (for the reaction handler), and a
snapshot of the conversation context used to build it (so a correction can
re-run the engine with the original context).

The clock is injectable (``now`` callable) so expiry is testable without sleeps.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass

AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
AWAITING_CLARIFICATION = "AWAITING_CLARIFICATION"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pending (
    user_id            TEXT PRIMARY KEY,
    state              TEXT NOT NULL,
    operation          TEXT,
    question           TEXT,
    preview_message_id TEXT,
    context            TEXT,
    created_at         REAL NOT NULL
);
"""


@dataclass
class Pending:
    user_id: str
    state: str
    operations: list[dict] | None  # a proposal's write ops (batch), or None
    question: str | None
    preview_message_id: str | None
    context: dict | None
    created_at: float


class PendingStore:
    def __init__(self, db_path: str, now=time.time):
        self._now = now
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    # --- internal ----------------------------------------------------------
    def _row_to_pending(self, row: sqlite3.Row | None) -> Pending | None:
        if row is None:
            return None
        return Pending(
            user_id=row["user_id"],
            state=row["state"],
            operations=json.loads(row["operation"]) if row["operation"] else None,
            question=row["question"],
            preview_message_id=row["preview_message_id"],
            context=json.loads(row["context"]) if row["context"] else None,
            created_at=row["created_at"],
        )

    def _upsert(self, p: Pending) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO pending "
            "(user_id, state, operation, question, preview_message_id, context, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                p.user_id,
                p.state,
                json.dumps(p.operations) if p.operations is not None else None,
                p.question,
                p.preview_message_id,
                json.dumps(p.context) if p.context is not None else None,
                p.created_at,
            ),
        )
        self._conn.commit()

    # --- API ---------------------------------------------------------------
    def get(self, user_id: str) -> Pending | None:
        cur = self._conn.execute("SELECT * FROM pending WHERE user_id = ?", (user_id,))
        return self._row_to_pending(cur.fetchone())

    def get_by_preview_message_id(self, message_id: str) -> Pending | None:
        cur = self._conn.execute(
            "SELECT * FROM pending WHERE preview_message_id = ?", (str(message_id),)
        )
        return self._row_to_pending(cur.fetchone())

    def set_awaiting_confirmation(
        self,
        user_id: str,
        operations: list[dict],
        preview_message_id: str | None = None,
        context: dict | None = None,
    ) -> Pending:
        p = Pending(
            user_id=str(user_id),
            state=AWAITING_CONFIRMATION,
            operations=operations,
            question=None,
            preview_message_id=str(preview_message_id) if preview_message_id is not None else None,
            context=context,
            created_at=self._now(),
        )
        self._upsert(p)
        return p

    def set_awaiting_clarification(
        self,
        user_id: str,
        question: str,
        context: dict | None = None,
        operations: list[dict] | None = None,
    ) -> Pending:
        p = Pending(
            user_id=str(user_id),
            state=AWAITING_CLARIFICATION,
            operations=operations,
            question=question,
            preview_message_id=None,
            context=context,
            created_at=self._now(),
        )
        self._upsert(p)
        return p

    def set_preview_message_id(self, user_id: str, message_id: str) -> None:
        self._conn.execute(
            "UPDATE pending SET preview_message_id = ? WHERE user_id = ?",
            (str(message_id), str(user_id)),
        )
        self._conn.commit()

    def clear(self, user_id: str) -> None:
        self._conn.execute("DELETE FROM pending WHERE user_id = ?", (str(user_id),))
        self._conn.commit()

    def expire(self, ttl_seconds: float) -> list[Pending]:
        """Delete and return all pending rows older than ``ttl_seconds``."""
        cutoff = self._now() - ttl_seconds
        cur = self._conn.execute("SELECT * FROM pending WHERE created_at < ?", (cutoff,))
        expired = [self._row_to_pending(r) for r in cur.fetchall()]
        self._conn.execute("DELETE FROM pending WHERE created_at < ?", (cutoff,))
        self._conn.commit()
        return expired

    def close(self) -> None:
        self._conn.close()
