"""Map content refs to live site page URLs.

Two callers share these builders so URL construction lives in one place:

  * :func:`page_urls` — one page URL per applied operation, for post-commit
    replies.
  * ``refrender.render_refs`` — resolves inline ``{{ref}}`` links in ``/ask``
    answers to the same page URLs.

SITE_BASE_URL (e.g. https://bastionthe.dev/lore-bot) is the deployed site root.
``page_urls`` is called AFTER a successful apply, so newly created entries are
already on disk and resolvable through the index.
"""

from __future__ import annotations

from pathlib import Path

from .content.index import ContentIndex, section_for_type
from .content.slugify import slugify


def entry_page_url(site_base_url: str, entry_type: str, slug: str) -> str:
    """URL of an entry's page: ``<base>/<section>/<slug>/``."""
    return f"{site_base_url.rstrip('/')}/{section_for_type(entry_type)}/{slug}/"


def glossary_anchor_url(site_base_url: str, term_id: str) -> str:
    """URL of a glossary term's anchor: ``<base>/glossary/#<term-id>``."""
    return f"{site_base_url.rstrip('/')}/glossary/#{term_id}"


def timeline_url(site_base_url: str) -> str:
    return f"{site_base_url.rstrip('/')}/timeline/"


def page_urls(site_base_url: str | None, content_root: Path, operations: list[dict]) -> list[str]:
    """Return one deduped URL per operation target; [] when no site URL is set."""
    if not site_base_url:
        return []
    index = ContentIndex(content_root)
    urls: list[str] = []
    for op in operations:
        tool = op.get("tool")
        inp = op.get("input", {}) or {}
        if tool == "add_glossary_term":
            urls.append(glossary_anchor_url(site_base_url, slugify(inp.get("term", ""))))
        elif tool == "add_timeline_event":
            urls.append(timeline_url(site_base_url))
        else:  # create_entry / append_to_entry / update_field
            slug = inp.get("slug") or slugify(inp.get("title", ""))
            entry = index.lookup(slug)
            entry_type = entry.type if entry else inp.get("type", "concept")
            urls.append(entry_page_url(site_base_url, entry_type, slug))
    seen: set[str] = set()
    return [u for u in urls if not (u in seen or seen.add(u))]
