"""Config env handling — notably dotenv's blank-value behavior.

A ``.env`` line like ``REPO_PATH=`` puts an *empty string* into the
environment, which must behave exactly like the variable being unset.
"""

from __future__ import annotations

import pytest

from lorebot.config import _REPO_ROOT, ConfigError, load_config


@pytest.fixture()
def base_env(monkeypatch):
    """Minimal valid non-Discord env, with dotenv loading disabled."""
    for var in (
        "DISCORD_TOKEN",
        "GUILD_ID",
        "CHANNEL_ID",
        "ALLOWED_USER_IDS",
        "REPO_PATH",
        "SITE_BASE_URL",
        "ANTHROPIC_MODEL",
        "RP_SOURCE_IDS",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    return monkeypatch


def test_empty_repo_path_falls_back_to_repo_root(base_env):
    base_env.setenv("REPO_PATH", "")
    cfg = load_config(require_discord=False, dotenv=False)
    assert cfg.repo_path == _REPO_ROOT
    assert cfg.content_root == _REPO_ROOT / "content"


def test_unset_repo_path_falls_back_to_repo_root(base_env):
    cfg = load_config(require_discord=False, dotenv=False)
    assert cfg.repo_path == _REPO_ROOT


def test_empty_site_base_url_is_none(base_env):
    base_env.setenv("SITE_BASE_URL", "")
    cfg = load_config(require_discord=False, dotenv=False)
    assert cfg.site_base_url is None


def test_rp_source_ids_unset_is_empty(base_env):
    cfg = load_config(require_discord=False, dotenv=False)
    assert cfg.rp_source_ids == []


def test_rp_source_ids_blank_is_empty(base_env):
    base_env.setenv("RP_SOURCE_IDS", "")
    cfg = load_config(require_discord=False, dotenv=False)
    assert cfg.rp_source_ids == []


def test_rp_source_ids_ordered_and_deduped(base_env):
    base_env.setenv("RP_SOURCE_IDS", " 30, 10 ,20,10 ")
    cfg = load_config(require_discord=False, dotenv=False)
    assert cfg.rp_source_ids == [30, 10, 20]  # config order preserved, dupes dropped


def test_bogus_repo_path_names_the_missing_content_dir(base_env, tmp_path):
    base_env.setenv("REPO_PATH", str(tmp_path))  # exists, but has no content/
    with pytest.raises(ConfigError, match="REPO_PATH"):
        load_config(require_discord=False, dotenv=False)
