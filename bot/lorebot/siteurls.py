"""Map applied operations to live site page URLs for post-commit replies.

SITE_BASE_URL (e.g. https://bastionthe.dev/lore-bot) is the deployed site root.
Called AFTER a successful apply, so newly created entries are already on disk
and resolvable through the index.
"""

from __future__ import annotations

from pathlib import Path

from .content.index import ContentIndex, section_for_type
from .content.slugify import slugify


def page_urls(site_base_url: str | None, content_root: Path, operations: list[dict]) -> list[str]:
    """Return one deduped URL per operation target; [] when no site URL is set."""
    if not site_base_url:
        return []
    base = site_base_url.rstrip("/")
    index = ContentIndex(content_root)
    urls: list[str] = []
    for op in operations:
        tool = op.get("tool")
        inp = op.get("input", {}) or {}
        if tool == "add_glossary_term":
            urls.append(f"{base}/glossary/#{slugify(inp.get('term', ''))}")
        elif tool == "add_timeline_event":
            urls.append(f"{base}/timeline/")
        else:  # create_entry / append_to_entry / update_field
            slug = inp.get("slug") or slugify(inp.get("title", ""))
            entry = index.lookup(slug)
            section = section_for_type(entry.type if entry else inp.get("type", "concept"))
            urls.append(f"{base}/{section}/{slug}/")
    seen: set[str] = set()
    return [u for u in urls if not (u in seen or seen.add(u))]
