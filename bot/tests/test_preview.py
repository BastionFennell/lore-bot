from __future__ import annotations

import pytest

from lorebot.content import entries
from lorebot.preview import CONFIRM_FOOTER, build_plan, build_plans


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


def test_glossary_id_ref_not_warned(content_root, index):
    # {{iron-vow}} is a glossary term id (not an entry slug) — it is KNOWN and
    # must not be flagged as an unknown/stub reference.
    op = {
        "tool": "append_to_entry",
        "input": {
            "slug": "captain-powderkeg",
            "section_heading": "Recent History",
            "content": "She swore an {{iron-vow}} before the crew.",
        },
    }
    plan = build_plan(content_root, index, op)
    assert not any("iron-vow" in w for w in plan.warnings)


def test_create_body_glossary_known_unknown_still_warned(content_root, index):
    # A glossary id resolves (no warning); a genuinely unknown ref still warns.
    op = {
        "tool": "create_entry",
        "input": {
            "type": "concept",
            "title": "On Oaths",
            "tags": [],
            "summary": "Oath lore.",
            "body_sections": [
                {"heading": "Description", "content": "An {{iron-vow}} is not a {{blood-pact}}."}
            ],
        },
    }
    plan = build_plan(content_root, index, op)
    assert not any("iron-vow" in w for w in plan.warnings)  # glossary id: known
    assert any("blood-pact" in w for w in plan.warnings)  # unknown: warned


def test_entry_slug_ref_not_warned(content_root, index):
    # An entry slug is known on its own namespace (entry-precedence: even if a
    # glossary id shared the name, the entry would resolve). house-veldrane is an
    # existing entry, so referencing it never warns.
    op = {
        "tool": "append_to_entry",
        "input": {
            "slug": "captain-powderkeg",
            "section_heading": "Recent History",
            "content": "She defied {{house-veldrane}} openly.",
        },
    }
    plan = build_plan(content_root, index, op)
    assert not any("house-veldrane" in w for w in plan.warnings)


def test_same_batch_glossary_ref_not_warned(content_root, index):
    # op 1 adds a glossary term; op 2 references it. The overlay threads op 1's
    # not-yet-written term through, so op 2 must not warn about it.
    ops = [
        {"tool": "add_glossary_term",
         "input": {"term": "Blood Pact", "definition": "A grim oath.", "link_slug": None}},
        {"tool": "append_to_entry",
         "input": {"slug": "captain-powderkeg", "section_heading": "Recent History",
                   "content": "She sealed it with a {{blood-pact}}."}},
    ]
    plans = build_plans(content_root, index, ops)
    assert not any("blood-pact" in w for w in plans[1].warnings)
    # Sanity: the same ref WITHOUT the batching context would warn.
    solo = build_plan(content_root, index, ops[1])
    assert any("blood-pact" in w for w in solo.warnings)


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


def test_build_plans_single_op_matches_build_plan(content_root, index):
    op = {
        "tool": "add_glossary_term",
        "input": {"term": "Grog", "definition": "Watered rum.", "link_slug": None},
    }
    plans = build_plans(content_root, index, [op])
    assert len(plans) == 1
    # a single-op proposal renders exactly like build_plan (no k/N header)
    assert plans[0].preview == build_plan(content_root, index, op).preview
    assert "1/1" not in plans[0].preview


def test_build_plans_one_message_per_op_with_headers(content_root, index):
    ops = [
        {"tool": "add_glossary_term",
         "input": {"term": "Kin", "definition": "Bound crew.", "link_slug": None}},
        {"tool": "add_glossary_term",
         "input": {"term": "Fathoms", "definition": "A depth measure.", "link_slug": None}},
        {"tool": "add_timeline_event",
         "input": {"date_in_fiction": "0849-02-11", "description": "A battle at sea",
                   "related_slugs": None}},
    ]
    plans = build_plans(content_root, index, ops)
    assert len(plans) == 3  # one plan (=> one preview message) per op
    assert "1/3 — glossary: kin" in plans[0].preview
    assert "2/3 — glossary: fathoms" in plans[1].preview
    assert "3/3 — timeline:" in plans[2].preview
    # each preview is independently confirmable => its own confirm line
    for p in plans:
        assert p.preview.count(CONFIRM_FOOTER) == 1
        assert len(p.ops) == 1


def test_build_plans_cumulative_overlay(content_root, index):
    # two appends to the SAME section: op 2's diff must reflect op 1's line.
    ops = [
        {"tool": "append_to_entry",
         "input": {"slug": "captain-powderkeg", "section_heading": "Recent History",
                   "content": "First new line."}},
        {"tool": "append_to_entry",
         "input": {"slug": "captain-powderkeg", "section_heading": "Recent History",
                   "content": "Second new line."}},
    ]
    plans = build_plans(content_root, index, ops)
    # op 2's preview reflects op 1 already applied (its context/base includes it)
    assert "First new line." in plans[1].preview
    assert "Second new line." in plans[1].preview
    # op 1's preview does NOT yet mention op 2
    assert "Second new line." not in plans[0].preview
    # and the two glossary-style stacking: both land, threaded via the overlay
    g = [
        {"tool": "add_glossary_term",
         "input": {"term": "Kin", "definition": "d", "link_slug": None}},
        {"tool": "add_glossary_term",
         "input": {"term": "Fathoms", "definition": "d", "link_slug": None}},
    ]
    gplans = build_plans(content_root, index, g)
    gfile = next(v for k, v in gplans[1].files.items() if k.endswith("glossary.yaml"))
    assert "id: kin" in gfile and "id: fathoms" in gfile


def test_build_plans_batch_collision_blocks_naming_op(content_root, index):
    ops = [
        {"tool": "add_glossary_term",
         "input": {"term": "Kin", "definition": "Bound crew.", "link_slug": None}},
        {"tool": "create_entry",
         "input": {"type": "npc", "title": "Captain Powderkeg", "tags": [],
                   "summary": "dup", "body_sections": []}},
    ]
    with pytest.raises(entries.SlugCollisionError) as exc:
        build_plans(content_root, index, ops)
    assert "2/2" in str(exc.value)


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
