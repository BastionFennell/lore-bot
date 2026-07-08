"""Quality search over the whole corpus — the Phase 3 replacement for the
engine's placeholder ``_search_lore``.

The corpus is three kinds of document:

  * ``entry``     — a lore/character/map entry (title, tags, summary, body).
  * ``glossary``  — a glossary term (term, definition).
  * ``timeline``  — an in-fiction timeline event (date, description).

Ranking is field-weighted: a whole-word hit in the *title/term* weighs most,
then *tags*, then *summary/definition*, then *body*. Multi-term queries reward
documents that cover more *distinct* query terms over ones that merely repeat a
single term. ``rapidfuzz`` supplies fuzzy matching so typos ("tidebund") still
find the right document ("Tidebound"); a fuzzy hit always scores below the exact
hit it stands in for.

``search`` returns structured :class:`SearchResult` objects (capped at
:data:`RESULT_CAP`); ``search_lore`` renders them into readable lines the model
can cite from, each carrying the ref name (entry slug or glossary id) to cite.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import frontmatter
import yaml
from rapidfuzz import fuzz

from .content.glossary import GLOSSARY_RELPATH
from .content.index import ContentIndex
from .content.timeline import TIMELINE_RELPATH

RESULT_CAP = 8

# Field weights — title/term dominates, body is the floor.
W_TITLE = 10.0
W_TAGS = 5.0
W_SUMMARY = 3.0
W_BODY = 1.0
FIELD_ORDER = (("title", W_TITLE), ("tags", W_TAGS), ("summary", W_SUMMARY), ("body", W_BODY))

# Match qualities: a whole-word hit is full strength; a fuzzy hit is scaled by
# its similarity and hard-capped below 1.0 so it can never beat an exact hit.
WHOLE = 1.0
FUZZY_MAX = 0.6
FUZZY_THRESHOLD = 82.0  # rapidfuzz ratio (0-100) a token must clear to count
FUZZY_MIN_LEN = 4  # don't fuzzy-match 1-3 char terms (too noisy)

# Reward covering more distinct query terms; only lightly reward repeats.
COVERAGE_BONUS = 0.5
OCC_BONUS = 0.15
OCC_CAP = 3

SNIPPET_TARGET = 200

_WORD_RE = re.compile(r"[a-z0-9]+(?:['-][a-z0-9]+)*")
_SENTENCE_SPLIT_RE = re.compile(r"[.!?]+(?:\s+|$)")


@dataclass
class SearchResult:
    ref: str  # slug (entry) or id (glossary/timeline) — the name to cite
    kind: str  # entry | glossary | timeline
    title: str  # entry title / glossary term / timeline display date
    summary: str  # one-line summary / definition / description
    snippet: str  # sentence-bounded excerpt around the best match (no ellipsis)
    score: float


@dataclass
class _Doc:
    ref: str
    kind: str
    title: str
    summary: str
    fields: dict  # {"title": .., "tags": .., "summary": .., "body": ..}
    snippet_source: str


# --- Corpus assembly --------------------------------------------------------

def _clean(text: str) -> str:
    """Drop ``{{ref}}`` braces and ``## heading`` markers for readable snippets."""
    text = text.replace("{{", "").replace("}}", "")
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    return " ".join(text.split())


def _entry_docs(index: ContentIndex) -> list[_Doc]:
    docs: list[_Doc] = []
    for e in index.all():
        try:
            post = frontmatter.load(e.path)
        except OSError:
            continue
        tags = post.metadata.get("tags") or []
        tags_str = " ".join(str(t) for t in tags)
        body = post.content or ""
        docs.append(
            _Doc(
                ref=e.slug,
                kind="entry",
                title=e.title,
                summary=e.summary,
                fields={"title": e.title, "tags": tags_str, "summary": e.summary, "body": body},
                snippet_source=_clean(body) or e.summary,
            )
        )
    return docs


def _load_yaml_list(path: Path) -> list:
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    except (OSError, yaml.YAMLError):
        return []
    return data if isinstance(data, list) else []


def _glossary_docs(content_root: Path) -> list[_Doc]:
    docs: list[_Doc] = []
    for it in _load_yaml_list(content_root / GLOSSARY_RELPATH):
        if not isinstance(it, dict) or not it.get("id"):
            continue
        term = it.get("term") or it["id"]
        definition = it.get("definition") or ""
        docs.append(
            _Doc(
                ref=it["id"],
                kind="glossary",
                title=term,
                summary=definition,
                fields={"title": term, "tags": "", "summary": definition, "body": definition},
                snippet_source=_clean(definition),
            )
        )
    return docs


def _timeline_docs(content_root: Path) -> list[_Doc]:
    docs: list[_Doc] = []
    for it in _load_yaml_list(content_root / TIMELINE_RELPATH):
        if not isinstance(it, dict) or not it.get("id"):
            continue
        desc = it.get("description") or ""
        display = it.get("display_date") or it.get("date") or it["id"]
        # Timeline events have no meaningful title/tags — match on the description.
        docs.append(
            _Doc(
                ref=it["id"],
                kind="timeline",
                title=str(display),
                summary=desc,
                fields={"title": "", "tags": "", "summary": "", "body": desc},
                snippet_source=_clean(desc),
            )
        )
    return docs


def _corpus(index: ContentIndex) -> list[_Doc]:
    root = index.content_root
    return _entry_docs(index) + _glossary_docs(root) + _timeline_docs(root)


# --- Scoring ----------------------------------------------------------------

def _terms(query: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for t in _WORD_RE.findall((query or "").lower()):
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _fuzzy_quality(term: str, tokens: list[str]) -> float:
    if len(term) < FUZZY_MIN_LEN or not tokens:
        return 0.0
    best = max((fuzz.ratio(term, w) for w in tokens), default=0.0)
    if best < FUZZY_THRESHOLD:
        return 0.0
    return (best / 100.0) * FUZZY_MAX


def _term_field_score(term: str, field_text: str, weight: float) -> tuple[float, int]:
    """Return (weighted_score, whole_word_occurrences) for one term in one field."""
    if not field_text:
        return 0.0, 0
    low = field_text.lower()
    whole = re.findall(r"\b" + re.escape(term) + r"\b", low)
    if whole:
        return weight * WHOLE, len(whole)
    q = _fuzzy_quality(term, _WORD_RE.findall(low))
    return weight * q, 0


def _score(doc: _Doc, terms: list[str]) -> float:
    total = 0.0
    matched = 0
    for term in terms:
        best = 0.0
        occ = 0
        for name, weight in FIELD_ORDER:
            fs, wc = _term_field_score(term, doc.fields.get(name, ""), weight)
            occ += wc
            if fs > best:
                best = fs
        if best > 0:
            matched += 1
            extra = min(occ - 1, OCC_CAP) if occ > 1 else 0
            total += best + OCC_BONUS * extra
    if matched == 0:
        return 0.0
    return total * (1 + COVERAGE_BONUS * (matched - 1))


# --- Snippets ---------------------------------------------------------------

def _match_pos(text_low: str, terms: list[str]) -> int:
    for term in terms:  # prefer the first whole-word hit
        m = re.search(r"\b" + re.escape(term) + r"\b", text_low)
        if m:
            return m.start()
    for term in terms:  # then any substring
        i = text_low.find(term)
        if i != -1:
            return i
    return 0


def _sentences(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    for m in _SENTENCE_SPLIT_RE.finditer(text):
        spans.append((start, m.end()))
        start = m.end()
    if start < len(text):
        spans.append((start, len(text)))
    return spans


def _word_window(text: str, pos: int, target: int) -> str:
    half = target // 2
    start = max(0, pos - half)
    end = min(len(text), pos + half)
    if start > 0:  # snap forward off a partial leading word
        while start < len(text) and not text[start].isspace():
            start += 1
        while start < len(text) and text[start].isspace():
            start += 1
    if end < len(text):  # snap back off a partial trailing word
        while end > 0 and not text[end - 1].isspace():
            end -= 1
    return text[start:end].strip()


def make_snippet(text: str, terms: list[str], target: int = SNIPPET_TARGET) -> str:
    """A ~``target``-char excerpt around the best match, cut at sentence
    boundaries (falling back to word boundaries), never mid-word."""
    text = " ".join((text or "").split())
    if not text:
        return ""
    pos = _match_pos(text.lower(), terms)
    spans = _sentences(text)
    # Locate the sentence containing the match.
    hit = next((i for i, (s, e) in enumerate(spans) if s <= pos < e), 0)
    hs, he = spans[hit]
    if he - hs > target * 1.4:
        # One long sentence — fall back to a word-bounded window inside it.
        return _word_window(text, pos, target)
    lo = hi = hit
    length = he - hs
    # Grow outward, sentence by sentence, until we're near the target length.
    while length < target and (lo > 0 or hi < len(spans) - 1):
        grew = False
        if hi < len(spans) - 1:
            ns, ne = spans[hi + 1]
            if length + (ne - ns) <= target * 1.4:
                hi += 1
                length += ne - ns
                grew = True
        if lo > 0:
            ps, pe = spans[lo - 1]
            if length + (pe - ps) <= target * 1.4:
                lo -= 1
                length += pe - ps
                grew = True
        if not grew:
            break
    return text[spans[lo][0] : spans[hi][1]].strip()


# --- Public API -------------------------------------------------------------

def search(query: str, index: ContentIndex, *, limit: int = RESULT_CAP) -> list[SearchResult]:
    terms = _terms(query)
    if not terms:
        return []
    scored: list[tuple[float, _Doc]] = []
    for doc in _corpus(index):
        s = _score(doc, terms)
        if s > 0:
            scored.append((s, doc))
    # Stable, deterministic order: score desc, then kind, then ref.
    scored.sort(key=lambda sd: (-sd[0], sd[1].kind, sd[1].ref))
    results: list[SearchResult] = []
    for s, doc in scored[:limit]:
        results.append(
            SearchResult(
                ref=doc.ref,
                kind=doc.kind,
                title=doc.title,
                summary=doc.summary,
                snippet=make_snippet(doc.snippet_source, terms),
                score=s,
            )
        )
    return results


def _cite(result: SearchResult) -> str:
    if result.kind in ("entry", "glossary"):
        return "{{" + result.ref + "}}"
    return f"(timeline event: {result.ref})"


def render_results(results: list[SearchResult]) -> str:
    lines: list[str] = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. [{r.kind}] {r.title} — cite as {_cite(r)}")
        if r.summary:
            lines.append(f"   {r.summary}")
        if r.snippet:
            lines.append(f"   …{r.snippet}…")
    return "\n".join(lines)


def search_lore(query: str, index: ContentIndex, *, limit: int = RESULT_CAP) -> str:
    """Engine entry point: rendered, citable lines for the ``search_lore`` tool."""
    results = search(query, index, limit=limit)
    if not results:
        return f"(no matches for {query!r})"
    return render_results(results)
