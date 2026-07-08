"""Add or update terms in ``content/glossary/glossary.yaml``.

Each item carries an ``id`` (required by Astro's file loader) equal to the
slugified term. Updating an existing term replaces it in place rather than
appending a duplicate. The file is re-dumped with ``sort_keys=False`` and
``allow_unicode=True`` to keep it readable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .slugify import slugify

GLOSSARY_RELPATH = Path("glossary") / "glossary.yaml"


@dataclass
class GlossaryResult:
    path: Path
    item: dict
    is_update: bool
    old_content: str
    new_content: str
    rendered_item: str


def _dump(items: list[dict]) -> str:
    return yaml.dump(items, sort_keys=False, allow_unicode=True, default_flow_style=False)


def glossary_ids(content_root: Path, *, current_content: str | None = None) -> set[str]:
    """Return the set of glossary term ids currently defined.

    A ``{{ref}}`` may point at a glossary term id (a secondary namespace beside
    entry slugs), so callers use this to treat such refs as *known* rather than
    warning on them. Pass ``current_content`` to read a batch overlay (a term
    added earlier in the same proposal, not yet written to disk) instead of the
    file on disk.
    """
    path = Path(content_root) / GLOSSARY_RELPATH
    if current_content is not None:
        text = current_content
    else:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
    if not text:
        return set()
    items = yaml.safe_load(text) or []
    if not isinstance(items, list):
        return set()
    return {i["id"] for i in items if isinstance(i, dict) and i.get("id")}


def glossary_terms(content_root: Path, *, current_content: str | None = None) -> dict[str, str]:
    """Return an ``id -> display term`` map of the glossary.

    Used to resolve a ``{{glossary-id}}`` ref to the term's display text when
    rendering inline citations (see ``refrender``).
    """
    path = Path(content_root) / GLOSSARY_RELPATH
    if current_content is not None:
        text = current_content
    else:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
    if not text:
        return {}
    items = yaml.safe_load(text) or []
    if not isinstance(items, list):
        return {}
    return {
        i["id"]: (i.get("term") or i["id"])
        for i in items
        if isinstance(i, dict) and i.get("id")
    }


def add_glossary_term(
    content_root: Path,
    *,
    term: str,
    definition: str,
    link_slug: str | None = None,
    current_content: str | None = None,
) -> GlossaryResult:
    path = Path(content_root) / GLOSSARY_RELPATH
    if current_content is not None:
        # Batch planning: build against an earlier op's not-yet-written result
        # rather than stale disk state.
        old_content = current_content
    else:
        old_content = path.read_text(encoding="utf-8") if path.exists() else ""
    items = yaml.safe_load(old_content) or [] if old_content else []
    if not isinstance(items, list):
        raise ValueError("glossary.yaml is not a YAML list.")

    term_id = slugify(term)
    item: dict = {"id": term_id, "term": term, "definition": definition}
    if link_slug:
        item["link_slug"] = link_slug

    is_update = False
    for i, existing in enumerate(items):
        if existing.get("id") == term_id:
            items[i] = item
            is_update = True
            break
    if not is_update:
        items.append(item)

    new_content = _dump(items)
    rendered_item = _dump([item])
    return GlossaryResult(
        path=path,
        item=item,
        is_update=is_update,
        old_content=old_content,
        new_content=new_content,
        rendered_item=rendered_item,
    )
