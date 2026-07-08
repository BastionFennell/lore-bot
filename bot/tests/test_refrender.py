"""Inline {{ref}} rendering for /ask answers."""

from __future__ import annotations

from lorebot.refrender import render_refs

BASE = "https://example.dev/lore-bot"


def test_entry_ref_renders_title_and_url(content_root):
    out = render_refs("See {{house-veldrane}}.", content_root, BASE)
    assert out == f"See **House Veldrane** (<{BASE}/lore/house-veldrane/>)."


def test_character_ref_uses_characters_section(content_root):
    out = render_refs("{{mara-quillon}}", content_root, BASE)
    assert out == f"**Mara Quillon** (<{BASE}/characters/mara-quillon/>)"


def test_glossary_ref_renders_anchor_url(content_root):
    out = render_refs("An {{iron-vow}} binds you.", content_root, BASE)
    assert out == f"An **Iron Vow** (<{BASE}/glossary/#iron-vow>) binds you."


def test_unknown_ref_renders_bare_name(content_root):
    out = render_refs("The {{the-drowned-court}} watches.", content_root, BASE)
    # Forward ref with no page — no braces, no dead link.
    assert out == "The the-drowned-court watches."


def test_no_site_url_falls_back_to_plain_text(content_root):
    out = render_refs("{{house-veldrane}} and {{iron-vow}}.", content_root, None)
    # Entry -> **Title**, glossary -> plain term name.
    assert out == "**House Veldrane** and Iron Vow."


def test_mixed_text_entry_glossary_and_unknown(content_root):
    text = "{{captain-powderkeg}} swore an {{iron-vow}} at {{ghost-slug}}."
    out = render_refs(text, content_root, BASE)
    assert f"**Captain Powderkeg** (<{BASE}/lore/captain-powderkeg/>)" in out
    assert f"**Iron Vow** (<{BASE}/glossary/#iron-vow>)" in out
    assert "ghost-slug" in out and "{{ghost-slug}}" not in out


def test_text_without_refs_is_unchanged(content_root):
    assert render_refs("Plain answer, no refs.", content_root, BASE) == "Plain answer, no refs."
