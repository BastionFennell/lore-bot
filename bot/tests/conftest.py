"""Shared fixtures. Every content/git test runs against a *tmp copy* of the
committed test corpus (``tests/fixtures/content``) inside a throwaway git repo —
nothing touches the real repo's ``/content`` or git state.

``REAL_REPO`` still points at the real repository root; it is used only to locate
*code* that is not content — e.g. ``site/src/lib/urls.mjs`` for the slug parity
test.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from lorebot.content.index import ContentIndex

REAL_REPO = Path(__file__).resolve().parents[2]
# The permanent test corpus — a snapshot of /content, decoupled from live lore.
FIXTURE_CONTENT = Path(__file__).resolve().parent / "fixtures" / "content"


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(repo), check=True, capture_output=True, text=True
    )


def _init_repo(repo: Path) -> None:
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")


@pytest.fixture
def content_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copytree(FIXTURE_CONTENT, repo / "content")
    _init_repo(repo)
    return repo


@pytest.fixture
def content_root(content_repo: Path) -> Path:
    return content_repo / "content"


@pytest.fixture
def index(content_root: Path) -> ContentIndex:
    return ContentIndex(content_root)
