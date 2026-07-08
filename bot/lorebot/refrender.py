"""Render inline ``{{ref}}`` citations in ``/ask`` answers into readable links.

The engine's Conversational answers cite sources inline with ``{{slug}}`` (an
entry) or ``{{glossary-id}}`` (a glossary term). The transport layer runs the
answer through :func:`render_refs` before showing it, turning each ref into
``**Title** (<url>)``:

  * an entry ref  → the entry's page URL;
  * a glossary id → the glossary anchor URL.

On a name collision the entry wins (matching the site's link precedence).
Unknown refs render as just the bare ref name (no braces, no dead link). When
``site_base_url`` is unset there is nowhere to link, so entries render as
``**Title**`` and glossary terms as their plain term name.

Only Conversational answers pass through here — previews/diffs keep raw
``{{refs}}`` because those are the committed content.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import siteurls
from .content.glossary import glossary_terms
from .content.index import ContentIndex

_REF_RE = re.compile(r"\{\{\s*([a-z0-9][a-z0-9-]*)\s*\}\}")


def render_refs(text: str, content_root: Path, site_base_url: str | None) -> str:
    if not text or "{{" not in text:
        return text

    index = ContentIndex(content_root)
    terms = glossary_terms(content_root)

    def _replace(match) -> str:
        ref = match.group(1)
        entry = index.lookup(ref)
        if entry is not None:  # entry wins on a name collision
            if site_base_url:
                url = siteurls.entry_page_url(site_base_url, entry.type, ref)
                return f"**{entry.title}** (<{url}>)"
            return f"**{entry.title}**"
        if ref in terms:
            term = terms[ref]
            if site_base_url:
                url = siteurls.glossary_anchor_url(site_base_url, ref)
                return f"**{term}** (<{url}>)"
            return term  # plain term name when there's nowhere to link
        return ref  # unknown ref: bare name, no braces, no dead link

    return _REF_RE.sub(_replace, text)
