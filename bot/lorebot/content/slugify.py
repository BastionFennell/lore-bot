"""Kebab-case slug generation — a byte-for-byte port of ``slugify`` in
``site/src/lib/urls.mjs``.

The JavaScript original is::

    String(s).toLowerCase().trim()
      .replace(/['’]/g, '')
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')

The bot MUST produce identical output to the site so that a slug it generates
resolves to the same page the site would build. The parity is verified against
the real ``urls.mjs`` in ``tests/test_slugify.py`` (which shells out to Node).

Note: ``[^a-z0-9]`` is an *ASCII* character class in both engines, so any
non-ASCII letter (``é``) is dropped rather than transliterated. Apostrophes
(straight and typographic) are removed, not hyphenated — per the spec,
``"Kell's Hollow" -> "kells-hollow"``.
"""

import re

_APOSTROPHES = re.compile(r"['’]")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_EDGE_DASHES = re.compile(r"^-+|-+$")


def slugify(value) -> str:
    """Return the kebab-case slug for ``value`` (matches ``urls.mjs``)."""
    s = str(value).lower().strip()
    s = _APOSTROPHES.sub("", s)
    s = _NON_ALNUM.sub("-", s)
    s = _EDGE_DASHES.sub("", s)
    return s
