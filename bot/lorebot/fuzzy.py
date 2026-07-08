"""Fuzzy pre-matching of candidate entity names in a message against the slug
index, producing hint lines for the LLM prompt (the "ambiguity rule").

This is a *hint generator*, not a resolver — the LLM decides what to do with the
hints (and may call search_lore to dig further). We surface, per candidate, the
best-scoring slugs so the model can spot "which captain?" ambiguity itself.
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz, process

from .content.index import ContentIndex

# Capitalised runs ("Captain Powderkeg"), quoted phrases, or explicit {{slugs}}.
_CANDIDATE_RE = re.compile(
    r"\{\{([a-z0-9-]+)\}\}"          # explicit slug ref
    r"|\"([^\"]{2,60})\""            # double-quoted phrase
    r"|'([^']{2,60})'"              # single-quoted phrase
    r"|((?:[A-Z][\w'’-]+)(?:\s+(?:[A-Z][\w'’-]+|of|the|and))*)"  # Capitalised run
)


def _candidates(message: str) -> list[str]:
    seen: list[str] = []
    for m in _CANDIDATE_RE.finditer(message):
        phrase = next((g for g in m.groups() if g), None)
        if not phrase:
            continue
        phrase = phrase.strip()
        if len(phrase) < 3:
            continue
        if phrase not in seen:
            seen.append(phrase)
    return seen


def fuzzy_hints(message: str, index: ContentIndex, *, threshold: int = 72, top_k: int = 3) -> list[str]:
    """Return human-readable hint lines: ``"<phrase>" ~ slug (title) [score]``."""
    entries = index.all()
    if not entries:
        return []
    # Build a lookup keyed by the searchable text -> entry.
    choices: dict[str, object] = {}
    for e in entries:
        choices[e.title] = e
        choices[e.slug.replace("-", " ")] = e

    hints: list[str] = []
    for phrase in _candidates(message):
        matches = process.extract(
            phrase, list(choices.keys()), scorer=fuzz.WRatio, limit=top_k
        )
        good = [(text, score) for text, score, _ in matches if score >= threshold]
        if not good:
            continue
        rendered = ", ".join(
            f"{choices[text].slug} ({choices[text].title}) [{int(score)}]" for text, score in good
        )
        hints.append(f'"{phrase}" ~ {rendered}')
    return hints


def hints_block(message: str, index: ContentIndex) -> str:
    hints = fuzzy_hints(message, index)
    if not hints:
        return "(no confident entity matches)"
    return "\n".join(hints)
