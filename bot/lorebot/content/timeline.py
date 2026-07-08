"""Append in-fiction events to ``content/timeline/events.yaml``.

Each item carries a generated ``id`` (required by Astro's file loader), a
sortable ``date``, a ``description``, and an optional ``related`` slug list.
The file is re-dumped with ``sort_keys=False`` / ``allow_unicode=True``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .slugify import slugify

TIMELINE_RELPATH = Path("timeline") / "events.yaml"


@dataclass
class TimelineResult:
    path: Path
    item: dict
    old_content: str
    new_content: str
    rendered_item: str


def _dump(items: list[dict]) -> str:
    return yaml.dump(items, sort_keys=False, allow_unicode=True, default_flow_style=False)


def _generate_id(description: str, existing_ids: set[str]) -> str:
    words = description.split()
    base = slugify(" ".join(words[:6])) or "event"
    candidate = base
    n = 2
    while candidate in existing_ids:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def add_timeline_event(
    content_root: Path,
    *,
    date_in_fiction: str,
    description: str,
    related_slugs: list[str] | None = None,
    display_date: str | None = None,
) -> TimelineResult:
    path = Path(content_root) / TIMELINE_RELPATH
    old_content = path.read_text(encoding="utf-8") if path.exists() else ""
    items = yaml.safe_load(old_content) or [] if old_content else []
    if not isinstance(items, list):
        raise ValueError("events.yaml is not a YAML list.")

    existing_ids = {e.get("id") for e in items if isinstance(e, dict)}
    event_id = _generate_id(description, existing_ids)

    item: dict = {"id": event_id, "date": date_in_fiction}
    if display_date:
        item["display_date"] = display_date
    item["description"] = description
    if related_slugs:
        item["related"] = list(related_slugs)

    items.append(item)
    new_content = _dump(items)
    rendered_item = _dump([item])
    return TimelineResult(
        path=path,
        item=item,
        old_content=old_content,
        new_content=new_content,
        rendered_item=rendered_item,
    )
