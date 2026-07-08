"""Operation → live-site URL mapping for post-commit replies."""

from __future__ import annotations

from lorebot.siteurls import page_urls

BASE = "https://example.dev/lore-bot"


def _glossary(term):
    return {"tool": "add_glossary_term", "input": {"term": term, "definition": "x"}}


def test_no_site_url_returns_empty(content_root):
    assert page_urls(None, content_root, [_glossary("Kin")]) == []
    assert page_urls("", content_root, [_glossary("Kin")]) == []


def test_glossary_term_anchors(content_root):
    assert page_urls(BASE, content_root, [_glossary("Tidal Schools")]) == [
        f"{BASE}/glossary/#tidal-schools"
    ]


def test_timeline_deduped(content_root):
    ops = [
        {"tool": "add_timeline_event", "input": {"date_in_fiction": "0849-01-01", "description": "a"}},
        {"tool": "add_timeline_event", "input": {"date_in_fiction": "0849-02-02", "description": "b"}},
    ]
    assert page_urls(BASE, content_root, ops) == [f"{BASE}/timeline/"]


def test_entry_ops_use_section_from_index(content_root):
    # captain-powderkeg is an npc in the fixture corpus -> /lore/ section
    op = {"tool": "append_to_entry",
          "input": {"slug": "captain-powderkeg", "section_heading": "Notes", "content": "x"}}
    assert page_urls(BASE, content_root, [op]) == [f"{BASE}/lore/captain-powderkeg/"]


def test_create_entry_derives_slug_from_title(content_root):
    op = {"tool": "create_entry",
          "input": {"type": "character", "title": "Salt-Eyed Morrow", "tags": [],
                    "summary": "s", "body_sections": []}}
    # Entry not on disk in this test, so the type falls back to the op input.
    assert page_urls(BASE, content_root, [op]) == [f"{BASE}/characters/salt-eyed-morrow/"]
