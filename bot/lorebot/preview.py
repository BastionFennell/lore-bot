"""Turn a proposed write operation into a preview/diff + warnings, and into a
concrete set of files to write (a :class:`Plan`) that gitops can apply.

A *proposed write* is a JSON-serialisable ``{"tool": ..., "input": ...}`` dict —
exactly what the engine captures from the LLM and what pending.py persists. The
plan is (re)built from that dict against the *current* content tree, so applying
after a ``git pull`` re-reads the latest state.

Preview rules (per spec):
  * create_entry        -> full rendered markdown of the would-be file
  * append/update       -> unified diff of the affected section/field
  * glossary/timeline   -> the new/changed item rendered
Warnings appended: unknown ``{{slug}}`` refs, new section-heading creation.
Blocking (raises instead of previewing): slug collisions, unknown fields/slugs.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path

from .content import entries, glossary, timeline
from .content.index import ContentIndex

CONFIRM_FOOTER = "React ✅ to commit or ❌ to cancel."


@dataclass
class Plan:
    kind: str
    verb: str  # commit-message verb: create | update | append | add
    target: str  # slug / term / event id
    files: dict[str, str]  # absolute path -> full new content
    preview: str
    warnings: list[str] = field(default_factory=list)


def _diff(old: str, new: str, label: str) -> str:
    lines = difflib.unified_diff(
        old.splitlines(),
        new.splitlines(),
        fromfile=f"{label} (before)",
        tofile=f"{label} (after)",
        lineterm="",
    )
    return "\n".join(lines)


def build_plan(content_root: Path, index: ContentIndex, operation: dict) -> Plan:
    """Build a :class:`Plan` from ``{"tool", "input"}``. May raise
    ``entries.SlugCollisionError`` / ``entries.EntryError`` (both block)."""
    content_root = Path(content_root)
    tool = operation["tool"]
    data = operation.get("input", {}) or {}

    if tool == "create_entry":
        return _plan_create(content_root, index, data)
    if tool == "append_to_entry":
        return _plan_append(index, data)
    if tool == "update_field":
        return _plan_update(index, data)
    if tool == "add_glossary_term":
        return _plan_glossary(content_root, index, data)
    if tool == "add_timeline_event":
        return _plan_timeline(content_root, index, data)
    raise entries.EntryError(f"Not a write operation: {tool!r}")


def _sections_to_dict(body_sections) -> dict[str, str]:
    """Accept either the strict array-of-pairs form or a plain dict."""
    if isinstance(body_sections, dict):
        return body_sections
    out: dict[str, str] = {}
    for pair in body_sections or []:
        out[pair["heading"]] = pair["content"]
    return out


def _plan_create(content_root: Path, index: ContentIndex, data: dict) -> Plan:
    sections = _sections_to_dict(data.get("body_sections"))
    created = entries.create_entry(
        content_root,
        index,
        entry_type=data["type"],
        title=data["title"],
        tags=data.get("tags") or [],
        summary=data.get("summary", ""),
        body_sections=sections,
    )
    warnings = _slug_warnings(index, created.content, ignore={created.slug})
    preview = (
        f"**New {data['type']} — `{created.slug}`**\n\n"
        f"```markdown\n{created.content}```\n\n" + _footer(warnings)
    )
    return Plan(
        kind="create_entry",
        verb="create",
        target=created.slug,
        files={str(created.path): created.content},
        preview=preview,
        warnings=warnings,
    )


def _plan_append(index: ContentIndex, data: dict) -> Plan:
    res = entries.append_to_entry(
        index,
        slug=data["slug"],
        section_heading=data["section_heading"],
        content=data["content"],
    )
    warnings = _slug_warnings(index, data["content"])
    if res.created_heading:
        warnings.append(f'New section heading "## {res.heading}" will be created.')
    diff = _diff(res.old_section, res.new_section, f"{data['slug']} · ## {res.heading}")
    preview = (
        f"**Append to `{data['slug']}` under `## {res.heading}`**\n\n"
        f"```diff\n{diff}\n```\n\n" + _footer(warnings)
    )
    return Plan(
        kind="append_to_entry",
        verb="append to",
        target=data["slug"],
        files={str(res.path): res.new_content},
        preview=preview,
        warnings=warnings,
    )


def _plan_update(index: ContentIndex, data: dict) -> Plan:
    res = entries.update_field(
        index, slug=data["slug"], field=data["field"], value=data["value"]
    )
    diff = _diff(
        f"{res.field}: {res.old_value}",
        f"{res.field}: {res.new_value}",
        f"{data['slug']} · {res.field}",
    )
    preview = (
        f"**Update `{data['slug']}` — `{res.field}`**\n\n"
        f"```diff\n{diff}\n```\n\n" + _footer([])
    )
    return Plan(
        kind="update_field",
        verb="update",
        target=data["slug"],
        files={str(res.path): res.new_content},
        preview=preview,
        warnings=[],
    )


def _plan_glossary(content_root: Path, index: ContentIndex, data: dict) -> Plan:
    res = glossary.add_glossary_term(
        content_root,
        term=data["term"],
        definition=data["definition"],
        link_slug=data.get("link_slug"),
    )
    warnings = []
    link = data.get("link_slug")
    if link and link not in index:
        warnings.append(f"link_slug '{link}' does not resolve to a known entry.")
    verb_word = "Update" if res.is_update else "Add"
    preview = (
        f"**{verb_word} glossary term `{res.item['id']}`**\n\n"
        f"```yaml\n{res.rendered_item}```\n\n" + _footer(warnings)
    )
    return Plan(
        kind="add_glossary_term",
        verb="update" if res.is_update else "add",
        target=res.item["id"],
        files={str(res.path): res.new_content},
        preview=preview,
        warnings=warnings,
    )


def _plan_timeline(content_root: Path, index: ContentIndex, data: dict) -> Plan:
    res = timeline.add_timeline_event(
        content_root,
        date_in_fiction=data["date_in_fiction"],
        description=data["description"],
        related_slugs=data.get("related_slugs"),
    )
    warnings = []
    for slug in data.get("related_slugs") or []:
        if slug not in index:
            warnings.append(f"related slug '{slug}' does not resolve to a known entry.")
    preview = (
        f"**Add timeline event `{res.item['id']}`**\n\n"
        f"```yaml\n{res.rendered_item}```\n\n" + _footer(warnings)
    )
    return Plan(
        kind="add_timeline_event",
        verb="add",
        target=res.item["id"],
        files={str(res.path): res.new_content},
        preview=preview,
        warnings=warnings,
    )


def _slug_warnings(index: ContentIndex, text: str, *, ignore: set[str] | None = None) -> list[str]:
    unknown = entries.unknown_slug_refs(index, text, ignore=ignore)
    return [f"Unknown reference `{{{{{s}}}}}` (renders as a stub link)." for s in unknown]


def _footer(warnings: list[str]) -> str:
    parts = []
    if warnings:
        parts.append("⚠️ " + "\n⚠️ ".join(warnings))
    parts.append(CONFIRM_FOOTER)
    return "\n\n".join(parts)
