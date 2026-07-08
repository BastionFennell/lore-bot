"""Slug parity with site/src/lib/urls.mjs.

The binding requirement is byte-identical output to urls.mjs. We verify that
directly by shelling out to Node running the *real* urls.mjs (skipped if Node is
absent), then pin the documented edge-case outputs.

Note: two existing slugs — ``kells-hollow`` (title "Kell's Hollow") and
``shattered-reach-map`` (title "Chart of the Shattered Reach") — were
hand-authored and do NOT derive from ``slugify(title)``; urls.mjs itself would
produce ``kell-s-hollow`` / ``chart-of-the-shattered-reach``. That is the actual
behavior, so the bot reproduces it.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from lorebot.content.slugify import slugify
from tests.conftest import REAL_REPO

URLS_MJS = REAL_REPO / "site" / "src" / "lib" / "urls.mjs"

HAND_AUTHORED_EXCEPTIONS = {"kells-hollow", "shattered-reach-map"}

EDGE_INPUTS = [
    "Kell's Hollow",
    "Café Málaga",
    "multiple   spaces",
    "--edgy--",
    "  Trim Me  ",
    "House Veldrane",
    "The Sundering",
    "Chart of the Shattered Reach",
    "007",
    "Über Café",
    "Snake_case and CAPS",
]


def _node_slugify(inputs: list[str]) -> list[str]:
    script = (
        f"import {{ slugify }} from {json.dumps(str(URLS_MJS))};"
        "const inp = JSON.parse(process.argv[1]);"
        "console.log(JSON.stringify(inp.map(slugify)));"
    )
    out = subprocess.run(
        ["node", "--input-type=module", "-e", script, json.dumps(inputs)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout.strip())


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_parity_with_urls_mjs(index):
    inputs = EDGE_INPUTS + [e.title for e in index.all()]
    js = _node_slugify(inputs)
    py = [slugify(s) for s in inputs]
    assert py == js


def test_documented_edge_cases():
    assert slugify("Kell's Hollow") == "kell-s-hollow"
    assert slugify("Café Málaga") == "caf-m-laga"
    assert slugify("multiple   spaces") == "multiple-spaces"
    assert slugify("--edgy--") == "edgy"
    assert slugify("  Trim Me  ") == "trim-me"
    assert slugify("Chart of the Shattered Reach") == "chart-of-the-shattered-reach"


def test_existing_slugs_derive_from_title_except_hand_authored(index):
    mismatches = set()
    for e in index.all():
        if slugify(e.title) != e.slug:
            mismatches.add(e.slug)
    # The only entries whose slug isn't slugify(title) are the two hand-authored ones.
    assert mismatches == HAND_AUTHORED_EXCEPTIONS
