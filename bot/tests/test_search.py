"""Search quality pass — ranking, distinctness, fuzzy, kinds, cap, snippets."""

from __future__ import annotations

import frontmatter

from lorebot.search import RESULT_CAP, _clean, make_snippet, search, search_lore


def _by_ref(results):
    return {r.ref: r for r in results}


def test_title_beats_body(index):
    # "sundering" is the concept's title (title weight) and only body/summary
    # material elsewhere, so the-sundering must rank first.
    results = search("sundering", index)
    assert results[0].ref == "the-sundering"
    assert results[0].kind == "entry"


def test_multi_term_distinctness(index):
    # captain-ferocious matches both "black" and "flag" (its body: "a black flag
    # was seen"); house-veldrane matches only "black" ("black-and-brass ensign").
    # Covering more distinct terms must win.
    by = _by_ref(search("black flag", index))
    assert "captain-ferocious" in by and "house-veldrane" in by
    assert by["captain-ferocious"].score > by["house-veldrane"].score


def test_fuzzy_typo_scores_below_exact(index):
    exact = _by_ref(search("tidebound", index))
    fuzzy = _by_ref(search("tidebund", index))  # transposed/typo'd
    assert "tidebound" in exact and "tidebound" in fuzzy
    assert fuzzy["tidebound"].score > 0
    assert fuzzy["tidebound"].score < exact["tidebound"].score


def test_glossary_and_timeline_kinds_appear(index):
    # "sundering" surfaces the concept entry, the iron-vow glossary definition,
    # and the sundering-year timeline event.
    kinds = {r.kind for r in search("sundering", index)}
    assert {"entry", "glossary", "timeline"} <= kinds


def test_glossary_term_is_found_and_tagged(index):
    by = _by_ref(search("tidebound", index))
    assert by["tidebound"].kind == "glossary"
    assert by["tidebound"].title == "Tidebound"


def test_results_capped_at_eight(index):
    # A broad multi-term query matches well over eight documents.
    results = search("reach captain house sea sundering veldrane oath", index)
    assert len(results) == RESULT_CAP == 8


def test_snippet_contains_term_and_no_midword_cut(index):
    by = _by_ref(search("tariff", index))
    r = by["house-veldrane"]
    assert "tariff" in r.snippet.lower()
    # A verbatim, contiguous slice of the normalized source is the guarantee of
    # no mid-word cut (the snippet snaps to sentence/word boundaries).
    source = _clean(frontmatter.load(index.lookup("house-veldrane").path).content)
    assert r.snippet in source
    assert 0 < len(r.snippet) <= 280


def test_make_snippet_word_window_fallback():
    # A single very long sentence with no punctuation falls back to a word window.
    text = "alpha bravo charlie delta echo foxtrot golf hotel india juliet " * 6 + "target here"
    snip = make_snippet(text, ["target"], target=60)
    assert "target" in snip
    assert len(snip) <= 120
    # Clean edges: no partial words, and the snippet is a contiguous source slice.
    assert not snip.startswith(" ") and not snip.endswith(" ")
    assert snip in " ".join(text.split())


def test_empty_query_returns_nothing(index):
    assert search("   ", index) == []
    assert search_lore("", index) == "(no matches for '')"


def test_render_includes_citable_ref(index):
    rendered = search_lore("veldrane", index)
    assert "{{house-veldrane}}" in rendered
    assert "[entry]" in rendered


def test_timeline_ref_rendered_without_link_syntax(index):
    # Timeline events aren't {{ }}-linkable; they render with an explicit label.
    rendered = search_lore("sundering", index)
    assert "timeline event:" in rendered
