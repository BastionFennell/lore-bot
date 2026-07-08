"""Transport helpers for Discord: message splitting (2000-char limit) and
reaction constants. Kept import-free of discord.py so it is unit-testable.
"""

from __future__ import annotations

DISCORD_LIMIT = 2000
CONFIRM_EMOJI = "✅"
CANCEL_EMOJI = "❌"


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
