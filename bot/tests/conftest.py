"""Shared fixtures. Every content/git test runs against a *tmp copy* of the real
``/content`` tree inside a throwaway git repo — nothing touches the real repo.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from lorebot.content.index import ContentIndex

REAL_REPO = Path(__file__).resolve().parents[2]
REAL_CONTENT = REAL_REPO / "content"


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
    shutil.copytree(REAL_CONTENT, repo / "content")
    _init_repo(repo)
    return repo


@pytest.fixture
def content_root(content_repo: Path) -> Path:
    return content_repo / "content"


@pytest.fixture
def index(content_root: Path) -> ContentIndex:
    return ContentIndex(content_root)
