from __future__ import annotations

import pytest

from lorebot.content import entries
from lorebot.preview import CONFIRM_FOOTER, build_plan


def test_create_plan_renders_full_markdown(content_root, index):
    op = {
        "tool": "create_entry",
        "input": {
            "type": "location",
            "title": "Gull Reef",
            "tags": ["reef"],
            "summary": "A treacherous reef.",
            "body_sections": [{"heading": "Description", "content": "Sharp coral everywhere."}],
        },
    }
    plan = build_plan(content_root, index, op)
    assert plan.kind == "create_entry"
    assert plan.target == "gull-reef"
    assert "```markdown" in plan.preview
    assert "Sharp coral everywhere." in plan.preview
    assert CONFIRM_FOOTER in plan.preview
    assert len(plan.files) == 1


def test_append_plan_shows_diff(content_root, index):
    op = {
        "tool": "append_to_entry",
        "input": {
            "slug": "captain-powderkeg",
            "section_heading": "Recent History",
            "content": "She burned a fourth barge.",
        },
    }
    plan = build_plan(content_root, index, op)
    assert "```diff" in plan.preview
    assert "+" in plan.preview
    assert "She burned a fourth barge." in plan.preview


def test_new_heading_warning(content_root, index):
    op = {
        "tool": "append_to_entry",
        "input": {
            "slug": "captain-powderkeg",
            "section_heading": "Rumours",
            "content": "They say she never sleeps.",
        },
    }
    plan = build_plan(content_root, index, op)
    assert any("new section heading" in w.lower() for w in plan.warnings)


def test_unknown_slug_warning(content_root, index):
    op = {
        "tool": "append_to_entry",
        "input": {
            "slug": "captain-powderkeg",
            "section_heading": "Recent History",
            "content": "She allied with {{the-kraken-court}}.",
        },
    }
    plan = build_plan(content_root, index, op)
    assert any("the-kraken-court" in w for w in plan.warnings)


def test_slug_collision_blocks(content_root, index):
    op = {
        "tool": "create_entry",
        "input": {
            "type": "npc",
            "title": "Captain Powderkeg",
            "tags": [],
            "summary": "dup",
            "body_sections": [],
        },
    }
    with pytest.raises(entries.SlugCollisionError):
        build_plan(content_root, index, op)


def test_glossary_plan_renders_item(content_root, index):
    op = {
        "tool": "add_glossary_term",
        "input": {"term": "Grog", "definition": "Watered rum.", "link_slug": None},
    }
    plan = build_plan(content_root, index, op)
    assert plan.kind == "add_glossary_term"
    assert not plan.is_batch
    assert "```yaml" in plan.preview
    assert "grog" in plan.target


def test_single_op_as_list_matches_dict(content_root, index):
    op = {
        "tool": "add_glossary_term",
        "input": {"term": "Grog", "definition": "Watered rum.", "link_slug": None},
    }
    assert build_plan(content_root, index, [op]).preview == build_plan(
        content_root, index, op
    ).preview


def test_batch_renders_numbered_sections_single_confirm(content_root, index):
    ops = [
        {"tool": "add_glossary_term",
         "input": {"term": "Kin", "definition": "Bound crew.", "link_slug": None}},
        {"tool": "add_glossary_term",
         "input": {"term": "Fathoms", "definition": "A depth measure.", "link_slug": None}},
        {"tool": "add_timeline_event",
         "input": {"date_in_fiction": "0849-02-11", "description": "A battle at sea",
                   "related_slugs": None}},
    ]
    plan = build_plan(content_root, index, ops)
    assert plan.is_batch and len(plan.ops) == 3
    # numbered per-op headers
    assert "1/3 — glossary: kin" in plan.preview
    assert "2/3 — glossary: fathoms" in plan.preview
    assert "3/3 — timeline:" in plan.preview
    # exactly one confirm line for the whole batch
    assert plan.preview.count(CONFIRM_FOOTER) == 1
    # both glossary terms land in the SAME file — chaining, not clobbering
    gcontent = next(v for k, v in plan.files.items() if k.endswith("glossary.yaml"))
    assert "id: kin" in gcontent and "id: fathoms" in gcontent


def test_batch_collision_blocks_whole_thing_naming_op(content_root, index):
    ops = [
        {"tool": "add_glossary_term",
         "input": {"term": "Kin", "definition": "Bound crew.", "link_slug": None}},
        {"tool": "create_entry",
         "input": {"type": "npc", "title": "Captain Powderkeg", "tags": [],
                   "summary": "dup", "body_sections": []}},
    ]
    with pytest.raises(entries.SlugCollisionError) as exc:
        build_plan(content_root, index, ops)
    assert "2/2" in str(exc.value)  # names the offending op
