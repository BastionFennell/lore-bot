from __future__ import annotations

import yaml

from lorebot.content import glossary, timeline


def test_add_glossary_term_new(content_root):
    res = glossary.add_glossary_term(
        content_root,
        term="Keelhaul",
        definition="A brutal punishment: dragging a soul under the hull.",
        link_slug="house-veldrane",
    )
    assert res.is_update is False
    items = yaml.safe_load(res.new_content)
    match = next(i for i in items if i["id"] == "keelhaul")
    assert match["term"] == "Keelhaul"
    assert match["link_slug"] == "house-veldrane"
    # existing terms survive
    assert any(i["id"] == "iron-vow" for i in items)


def test_update_glossary_term_replaces_not_duplicates(content_root):
    res = glossary.add_glossary_term(
        content_root,
        term="Iron Vow",  # already present with id iron-vow
        definition="An updated definition of the binding oath.",
    )
    assert res.is_update is True
    items = yaml.safe_load(res.new_content)
    matches = [i for i in items if i["id"] == "iron-vow"]
    assert len(matches) == 1
    assert matches[0]["definition"] == "An updated definition of the binding oath."


def test_add_timeline_event(content_root):
    res = timeline.add_timeline_event(
        content_root,
        date_in_fiction="0848-01-01",
        description="Kell's Hollow rebels against the Veldrane tariff.",
        related_slugs=["kells-hollow", "house-veldrane"],
    )
    items = yaml.safe_load(res.new_content)
    new = items[-1]
    assert new["id"]  # generated id present
    assert new["date"] == "0848-01-01"
    assert new["related"] == ["kells-hollow", "house-veldrane"]
    # loadable and existing events retained
    assert any(i["id"] == "mutiny-at-kells-hollow" for i in items)


def test_timeline_ids_are_unique(content_root):
    # add two events with identical descriptions -> ids must differ
    first = timeline.add_timeline_event(
        content_root, date_in_fiction="0849-01-01", description="A storm rolls in."
    )
    (content_root / "timeline" / "events.yaml").write_text(first.new_content)
    second = timeline.add_timeline_event(
        content_root, date_in_fiction="0849-02-01", description="A storm rolls in."
    )
    ids = [i["id"] for i in yaml.safe_load(second.new_content)]
    assert len(ids) == len(set(ids))
