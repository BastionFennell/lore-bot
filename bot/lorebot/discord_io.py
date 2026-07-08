"""Transport helpers for Discord: message splitting (2000-char limit),
reaction constants, and an in-memory dedup guard. Kept import-free of discord.py
so it is unit-testable.
"""

from __future__ import annotations

from collections import deque

DISCORD_LIMIT = 2000
CONFIRM_EMOJI = "✅"
CANCEL_EMOJI = "❌"


class SeenMessages:
    """A bounded, ordered set of message ids for idempotent message handling.

    The Discord gateway can redeliver an event (reconnects, resumes), which would
    otherwise make the bot process the same message twice. We keep the most
    recent ``maxlen`` ids; :meth:`add` records an id and returns ``True`` the
    first time it sees it, ``False`` on any redelivery. Intentionally in-memory
    only — redelivery is a live-gateway phenomenon, so nothing needs to persist
    across restarts.
    """

    def __init__(self, maxlen: int = 500):
        self._order: deque = deque(maxlen=maxlen)
        self._seen: set = set()

    def add(self, message_id) -> bool:
        """Record ``message_id``; return True if it's new, False if already seen."""
        key = str(message_id)
        if key in self._seen:
            return False
        if len(self._order) == self._order.maxlen:
            # Evict the oldest id to keep the set bounded in lockstep with the deque.
            self._seen.discard(self._order[0])
        self._order.append(key)
        self._seen.add(key)
        return True

    def __contains__(self, message_id) -> bool:
        return str(message_id) in self._seen

    def __len__(self) -> int:
        return len(self._seen)


def split_message(text: str, limit: int = DISCORD_LIMIT) -> list[str]:
    """Split ``text`` into chunks under ``limit`` chars, preferring newline and
    (inside overly long lines) whitespace boundaries; never splits mid-word when
    avoidable and preserves fenced-code readability by cutting on line breaks."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        piece = line if not current else current + "\n" + line
        if len(piece) <= limit:
            current = piece
            continue
        if current:
            chunks.append(current)
            current = ""
        # The line itself may exceed the limit; hard-wrap it.
        while len(line) > limit:
            cut = line.rfind(" ", 0, limit)
            if cut <= 0:
                cut = limit
            chunks.append(line[:cut])
            line = line[cut:].lstrip(" ")
        current = line
    if current:
        chunks.append(current)
    return chunks
