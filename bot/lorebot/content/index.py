"""Slug index over ``/content`` — a Python port of ``site/src/lib/lore-index.mjs``.

Scans the frontmatter of every linkable entry under ``content/lore`` (recursive),
``content/characters`` (flat), and ``content/maps`` (top-level ``*.md`` only —
``/images`` is skipped). Enforces the same single-namespace slug-uniqueness
invariant, raising with **both** offending file paths named on a collision.

URL helpers mirror ``urls.mjs``: the four lore types live under ``/lore``,
characters under ``/characters``, maps under ``/maps``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import frontmatter


class DuplicateSlugError(ValueError):
    """Raised when two entries claim the same slug (mirrors lore-index.mjs)."""


@dataclass(frozen=True)
class Entry:
    slug: str
    title: str
    type: str
    summary: str
    url: str
    path: Path


def base_path() -> str:
    """Mirror ``basePath()`` in urls.mjs (reads ``BASE_PATH`` env, default ``/``)."""
    b = os.environ.get("BASE_PATH", "/")
    if not b.startswith("/"):
        b = "/" + b
    if not b.endswith("/"):
        b += "/"
    return b


def section_for_type(entry_type: str) -> str:
    """The four lore types all live under /lore; characters/maps have their own."""
    if entry_type == "character":
        return "characters"
    if entry_type == "map":
        return "maps"
    return "lore"  # location | faction | npc | concept


def url_for_type(entry_type: str, slug: str) -> str:
    return f"{base_path()}{section_for_type(entry_type)}/{slug}/"


def _walk_markdown(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(root.rglob("*.md"))


def _collect_files(content_root: Path) -> list[Path]:
    lore = _walk_markdown(content_root / "lore")
    characters = _walk_markdown(content_root / "characters")
    maps_dir = content_root / "maps"
    maps: list[Path] = []
    if maps_dir.is_dir():
        # maps: only top-level *.md (skip /images)
        maps = sorted(p for p in maps_dir.glob("*.md"))
    return lore + characters + maps


class ContentIndex:
    """Authoritative ``slug -> Entry`` map over a content tree."""

    def __init__(self, content_root: Path):
        self.content_root = Path(content_root)
        self._by_slug: dict[str, Entry] = {}
        self._build()

    def _build(self) -> None:
        seen: dict[str, Path] = {}
        for file in _collect_files(self.content_root):
            post = frontmatter.load(file)
            data = post.metadata
            slug = data.get("slug")
            if not slug:
                raise ValueError(f"[lore-index] Missing 'slug' in frontmatter: {file}")
            if slug in seen:
                raise DuplicateSlugError(
                    f"[lore-index] Duplicate slug '{slug}' found in:\n"
                    f"  - {seen[slug]}\n"
                    f"  - {file}\n"
                    f"Slugs must be unique across /lore, /characters and /maps."
                )
            seen[slug] = file
            entry_type = data.get("type", "")
            self._by_slug[slug] = Entry(
                slug=slug,
                title=data.get("title") or slug,
                type=entry_type,
                summary=(data.get("summary") or "").strip(),
                url=url_for_type(entry_type, slug),
                path=file,
            )

    def lookup(self, slug: str) -> Entry | None:
        return self._by_slug.get(slug)

    def __contains__(self, slug: str) -> bool:
        return slug in self._by_slug

    def all(self) -> list[Entry]:
        return sorted(self._by_slug.values(), key=lambda e: e.slug)

    def context_lines(self) -> str:
        """One ``slug | title | type | summary`` line per entry, for the prompt."""
        rows = []
        for e in self.all():
            rows.append(f"{e.slug} | {e.title} | {e.type} | {e.summary}")
        return "\n".join(rows)


def build_index(content_root: Path) -> ContentIndex:
    return ContentIndex(content_root)
