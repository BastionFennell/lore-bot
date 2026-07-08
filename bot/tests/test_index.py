from __future__ import annotations

import pytest

from lorebot.content.index import ContentIndex, DuplicateSlugError


def test_index_loads_real_content(index):
    slugs = {e.slug for e in index.all()}
    assert "captain-powderkeg" in slugs
    assert "kells-hollow" in slugs
    assert index.lookup("house-veldrane").type == "faction"
    assert index.lookup("shattered-reach-map").type == "map"
    # summary is captured for the prompt context
    assert index.lookup("captain-powderkeg").summary


def test_context_lines_shape(index):
    lines = index.context_lines().splitlines()
    row = next(l for l in lines if l.startswith("house-veldrane "))
    assert row.count(" | ") == 3  # slug | title | type | summary


def test_duplicate_slug_names_both_files(tmp_path):
    root = tmp_path / "content"
    npcs = root / "lore" / "npcs"
    npcs.mkdir(parents=True)
    a = npcs / "a.md"
    b = npcs / "b.md"
    a.write_text("---\nslug: dup\ntitle: A\ntype: npc\n---\n\nbody\n")
    b.write_text("---\nslug: dup\ntitle: B\ntype: npc\n---\n\nbody\n")

    with pytest.raises(DuplicateSlugError) as exc:
        ContentIndex(root)
    msg = str(exc.value)
    assert "dup" in msg
    assert str(a) in msg and str(b) in msg
