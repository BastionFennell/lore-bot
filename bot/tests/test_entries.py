from __future__ import annotations

import frontmatter
import pytest

from lorebot.content import entries


def test_create_entry_matches_template(content_root, index):
    created = entries.create_entry(
        content_root,
        index,
        entry_type="npc",
        title="Bosun Grim",
        tags=["veldrane", "officer"],
        summary="A grizzled bosun with a grudge.",
        body_sections={"Description": "He keeps the crew in line with a belaying pin."},
        today="2030-01-02",
    )
    assert created.slug == "bosun-grim"
    assert str(created.path).endswith("content/lore/npcs/bosun-grim.md")

    post = frontmatter.loads(created.content)
    assert post["title"] == "Bosun Grim"
    assert post["slug"] == "bosun-grim"
    assert post["type"] == "npc"
    assert post["tags"] == ["veldrane", "officer"]
    assert str(post["created"]) == "2030-01-02"
    assert str(post["updated"]) == "2030-01-02"
    assert post["status"] == "unknown"  # npc enum default
    assert "## Description" in created.content
    assert "belaying pin" in created.content


def test_create_entry_slug_collision(content_root, index):
    with pytest.raises(entries.SlugCollisionError):
        entries.create_entry(
            content_root,
            index,
            entry_type="npc",
            title="Captain Powderkeg",  # already exists
            tags=[],
            summary="dup",
            body_sections={},
        )


def test_append_to_existing_section(index):
    res = entries.append_to_entry(
        index,
        slug="captain-powderkeg",
        section_heading="Recent History",
        content="She has since blockaded the northern lanes.",
        today="2030-05-05",
    )
    assert res.created_heading is False
    assert "blockaded the northern lanes" in res.new_content
    # original section content is preserved
    assert "seizure of {{kells-hollow}}" in res.new_content
    post = frontmatter.loads(res.new_content)
    assert str(post["updated"]) == "2030-05-05"
    # still valid, single occurrence of the heading
    assert res.new_content.count("## Recent History") == 1


def test_append_creates_new_section(index):
    res = entries.append_to_entry(
        index,
        slug="captain-powderkeg",
        section_heading="Secrets",
        content="She fears deep water.",
        today="2030-05-05",
    )
    assert res.created_heading is True
    assert "## Secrets" in res.new_content
    assert "She fears deep water." in res.new_content
    # sanity: document still parses and keeps its other sections
    post = frontmatter.loads(res.new_content)
    assert "Secrets" in post.content


def test_update_field_valid(index):
    res = entries.update_field(
        index, slug="captain-powderkeg", field="status", value="dead", today="2030-06-06"
    )
    post = frontmatter.loads(res.new_content)
    assert post["status"] == "dead"
    assert str(post["updated"]) == "2030-06-06"
    assert res.old_value == "alive"


def test_update_field_bumps_updated(index):
    before = frontmatter.loads(entries.read_entry_text(index, "house-veldrane"))
    assert str(before["updated"]) == "2026-07-08"
    res = entries.update_field(
        index, slug="house-veldrane", field="disposition", value="neutral", today="2031-01-01"
    )
    post = frontmatter.loads(res.new_content)
    assert str(post["updated"]) == "2031-01-01"
    assert post["disposition"] == "neutral"


def test_update_field_unknown_field(index):
    with pytest.raises(entries.EntryError):
        entries.update_field(index, slug="captain-powderkeg", field="banana", value="x")


def test_update_field_invalid_enum(index):
    with pytest.raises(entries.EntryError):
        entries.update_field(index, slug="captain-powderkeg", field="status", value="zombie")


def test_update_field_tags_list(index):
    res = entries.update_field(
        index, slug="house-veldrane", field="tags", value=["nobility", "cursed"], today="2031-02-02"
    )
    post = frontmatter.loads(res.new_content)
    assert post["tags"] == ["nobility", "cursed"]
