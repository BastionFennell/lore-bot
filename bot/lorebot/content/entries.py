"""Read, create, and edit lore markdown entries.

Frontmatter is edited **textually** (line based) rather than by re-serializing
the whole YAML block, so hand-authored formatting (flow-style ``tags``, quoting,
key order) survives an edit and diffs stay minimal. New entries are built from a
template that mirrors the Phase 1 frontmatter exactly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import frontmatter

from .index import ContentIndex, section_for_type
from .slugify import slugify

# --- Schemas (mirror README.md / lorebot-spec.md) ---------------------------

VALID_TYPES = {"location", "faction", "npc", "concept", "character", "map"}

# Base frontmatter keys, in the canonical order used by Phase 1 content.
BASE_KEYS = ["title", "slug", "type", "tags", "created", "updated", "summary"]

# Type-specific frontmatter fields.
TYPE_FIELDS: dict[str, list[str]] = {
    "location": ["region", "map"],
    "faction": ["leader", "disposition"],
    "npc": ["status", "affiliation", "first_appearance"],
    "concept": [],
    "character": ["player", "portrait"],
    "map": ["image"],
}

# Enum-constrained fields.
ENUMS: dict[str, set[str]] = {
    "status": {"alive", "dead", "missing", "unknown"},
    "disposition": {"ally", "neutral", "hostile", "unknown"},
}

# Fields that may be changed via update_field (slug/type/created are immutable;
# updated is bumped automatically).
_EDITABLE_BASE = {"title", "tags", "summary"}


class EntryError(ValueError):
    """A user-correctable problem (unknown field, unknown slug, bad enum)."""


class SlugCollisionError(ValueError):
    """A generated slug already exists — blocks the write, not a preview."""


def today_iso() -> str:
    return date.today().isoformat()


# --- Frontmatter text helpers -----------------------------------------------

_FM_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)


def split_document(text: str) -> tuple[str, str]:
    """Return ``(frontmatter_text, body_text)`` for a ``---`` fenced document."""
    m = _FM_RE.match(text)
    if not m:
        raise EntryError("Entry has no YAML frontmatter block.")
    return m.group(1), m.group(2)


def reassemble(fm_text: str, body_text: str) -> str:
    return f"---\n{fm_text}\n---\n{body_text}"


_SPECIAL_START = set("-?:,[]{}#&*!|>'\"%@` ")


def _needs_quote(s: str) -> bool:
    if s == "" or s != s.strip():
        return True
    if s[0] in _SPECIAL_START:
        return True
    if ": " in s or s.endswith(":") or ":" in s or " #" in s:
        return True
    if s.lower() in {"true", "false", "null", "yes", "no", "~", "on", "off"}:
        return True
    try:
        float(s)
        return True
    except ValueError:
        pass
    return False


def format_scalar(value) -> str:
    """Render a Python scalar as a YAML value (matching Phase 1 style)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value)
    if _needs_quote(s):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def format_value(value) -> str:
    """Render either a flow-style list ``[a, b]`` or a scalar."""
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(format_scalar(v) for v in value) + "]"
    return format_scalar(value)


def set_fm_field(fm_text: str, key: str, value) -> str:
    """Replace the ``key:`` line if present, else insert before the block end."""
    rendered = f"{key}: {format_value(value)}"
    lines = fm_text.split("\n")
    pat = re.compile(rf"^{re.escape(key)}\s*:")
    for i, line in enumerate(lines):
        if pat.match(line):
            lines[i] = rendered
            return "\n".join(lines)
    lines.append(rendered)
    return "\n".join(lines)


# --- Reads ------------------------------------------------------------------

def read_entry_text(index: ContentIndex, slug: str) -> str:
    entry = index.lookup(slug)
    if entry is None:
        raise EntryError(f"No entry with slug '{slug}'.")
    return entry.path.read_text(encoding="utf-8")


# --- create_entry -----------------------------------------------------------

@dataclass
class CreatedEntry:
    slug: str
    path: Path
    content: str


def _path_for(content_root: Path, entry_type: str, slug: str) -> Path:
    section = section_for_type(entry_type)
    if section == "lore":
        # locations/factions/npcs/concepts -> pluralised folder
        folder = {
            "location": "locations",
            "faction": "factions",
            "npc": "npcs",
            "concept": "concepts",
        }[entry_type]
        return content_root / "lore" / folder / f"{slug}.md"
    return content_root / section / f"{slug}.md"


def build_new_entry(
    entry_type: str,
    title: str,
    tags: list[str],
    summary: str,
    body_sections: dict[str, str],
    *,
    today: str | None = None,
) -> tuple[str, str]:
    """Return ``(slug, rendered_markdown)`` for a new entry from the template."""
    if entry_type not in VALID_TYPES:
        raise EntryError(
            f"Unknown type '{entry_type}'. Must be one of: {', '.join(sorted(VALID_TYPES))}."
        )
    slug = slugify(title)
    if not slug:
        raise EntryError(f"Title '{title}' produces an empty slug.")
    day = today or today_iso()

    fm_pairs: list[tuple[str, object]] = [
        ("title", title),
        ("slug", slug),
        ("type", entry_type),
        ("tags", list(tags or [])),
        ("created", day),
        ("updated", day),
        ("summary", summary),
    ]
    # Sensible enum defaults for type-specific fields with a natural "unknown".
    if entry_type == "npc":
        fm_pairs.append(("status", "unknown"))
    elif entry_type == "faction":
        fm_pairs.append(("disposition", "unknown"))

    fm_lines = []
    for key, value in fm_pairs:
        if key in ("created", "updated"):
            fm_lines.append(f"{key}: {value}")  # ISO date, unquoted
        else:
            fm_lines.append(f"{key}: {format_value(value)}")
    fm_text = "\n".join(fm_lines)

    body_parts = []
    for heading, content in (body_sections or {}).items():
        body_parts.append(f"## {heading}\n\n{content.strip()}\n")
    body = "\n" + "\n".join(body_parts) if body_parts else "\n"

    return slug, reassemble(fm_text, body)


def create_entry(
    content_root: Path,
    index: ContentIndex,
    *,
    entry_type: str,
    title: str,
    tags: list[str],
    summary: str,
    body_sections: dict[str, str],
    today: str | None = None,
    extra_slugs: set[str] | None = None,
) -> CreatedEntry:
    slug, content = build_new_entry(
        entry_type, title, tags, summary, body_sections, today=today
    )
    if slug in index:
        existing = index.lookup(slug)
        raise SlugCollisionError(
            f"Slug '{slug}' already exists (used by '{existing.title}')."
        )
    # Within a batch, an earlier create in the same proposal claims its slug too.
    if extra_slugs and slug in extra_slugs:
        raise SlugCollisionError(
            f"Slug '{slug}' is already created earlier in this batch."
        )
    path = _path_for(Path(content_root), entry_type, slug)
    return CreatedEntry(slug=slug, path=path, content=content)


# --- append_to_entry --------------------------------------------------------

_HEADING_RE = re.compile(r"^##[ \t]+(.+?)[ \t]*$", re.MULTILINE)


@dataclass
class AppendResult:
    path: Path
    old_content: str
    new_content: str
    old_section: str
    new_section: str
    created_heading: bool
    heading: str


def _find_section_bounds(body: str, heading: str) -> tuple[int, int] | None:
    """Return ``(start, end)`` char offsets of the section body (text between the
    heading line and the next ``##`` heading), case-insensitive on the title."""
    target = heading.strip().lower()
    matches = list(_HEADING_RE.finditer(body))
    for i, m in enumerate(matches):
        if m.group(1).strip().lower() == target:
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
            return start, end
    return None


def append_to_entry(
    index: ContentIndex,
    *,
    slug: str,
    section_heading: str,
    content: str,
    today: str | None = None,
    current_content: str | None = None,
) -> AppendResult:
    entry = index.lookup(slug)
    if entry is None:
        raise EntryError(f"No entry with slug '{slug}'.")
    original = current_content if current_content is not None else entry.path.read_text(encoding="utf-8")
    fm_text, body = split_document(original)
    addition = content.strip()

    bounds = _find_section_bounds(body, section_heading)
    if bounds is None:
        created = True
        old_section = ""
        new_section = addition
        new_body = body.rstrip("\n") + f"\n\n## {section_heading}\n\n{addition}\n"
    else:
        created = False
        start, end = bounds
        old_section = body[start:end].strip("\n")
        new_section = f"{old_section}\n\n{addition}".strip("\n")
        tail = body[end:]
        new_body = (
            body[:start]
            + "\n\n"
            + new_section
            + "\n\n"
            + tail.lstrip("\n")
        )

    # Collapse any runs of 3+ blank lines and normalise the trailing newline.
    new_body = re.sub(r"\n{3,}", "\n\n", new_body).rstrip("\n") + "\n"
    fm_text = set_fm_field(fm_text, "updated", today or today_iso())
    new_content = reassemble(fm_text, new_body)
    return AppendResult(
        path=entry.path,
        old_content=original,
        new_content=new_content,
        old_section=old_section,
        new_section=new_section,
        created_heading=created,
        heading=section_heading,
    )


# --- update_field -----------------------------------------------------------

@dataclass
class UpdateResult:
    path: Path
    field: str
    old_value: object
    new_value: object
    old_content: str
    new_content: str


def _allowed_fields(entry_type: str) -> set[str]:
    return set(_EDITABLE_BASE) | set(TYPE_FIELDS.get(entry_type, []))


def update_field(
    index: ContentIndex,
    *,
    slug: str,
    field: str,
    value,
    today: str | None = None,
    current_content: str | None = None,
) -> UpdateResult:
    entry = index.lookup(slug)
    if entry is None:
        raise EntryError(f"No entry with slug '{slug}'.")

    allowed = _allowed_fields(entry.type)
    if field not in allowed:
        raise EntryError(
            f"'{field}' is not an editable field on a {entry.type} entry. "
            f"Editable fields: {', '.join(sorted(allowed))}."
        )
    if field in ENUMS:
        if str(value) not in ENUMS[field]:
            raise EntryError(
                f"'{value}' is not a valid {field}. "
                f"Must be one of: {', '.join(sorted(ENUMS[field]))}."
            )

    original = current_content if current_content is not None else entry.path.read_text(encoding="utf-8")
    post = frontmatter.loads(original)
    old_value = post.metadata.get(field)

    fm_text, body = split_document(original)
    # Coerce tags string -> list if a comma-separated string was passed.
    if field == "tags" and isinstance(value, str):
        value = [t.strip() for t in value.split(",") if t.strip()]
    fm_text = set_fm_field(fm_text, field, value)
    fm_text = set_fm_field(fm_text, "updated", today or today_iso())
    new_content = reassemble(fm_text, body)

    return UpdateResult(
        path=entry.path,
        field=field,
        old_value=old_value,
        new_value=value,
        old_content=original,
        new_content=new_content,
    )


# --- Cross-link scanning -----------------------------------------------------

_SLUG_REF_RE = re.compile(r"\{\{([a-z0-9-]+)\}\}")


def unknown_slug_refs(index: ContentIndex, text: str, *, ignore: set[str] | None = None) -> list[str]:
    """Return ``{{slug}}`` references in ``text`` that don't resolve (typo guard)."""
    ignore = ignore or set()
    found = []
    for slug in _SLUG_REF_RE.findall(text):
        if slug in ignore or slug in index:
            continue
        if slug not in found:
            found.append(slug)
    return found
