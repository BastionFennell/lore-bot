"""Environment loading + validation and allow-lists.

``main`` needs the full Discord config; ``repl`` needs only ANTHROPIC_API_KEY +
REPO_PATH. Missing required values raise :class:`ConfigError` with a message
listing exactly what's absent, so running without env vars exits helpfully.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# repo root = bot/ 's parent (this file is bot/lorebot/config.py).
_REPO_ROOT = Path(__file__).resolve().parents[2]


class ConfigError(RuntimeError):
    pass


@dataclass
class Config:
    discord_token: str | None
    anthropic_api_key: str | None
    anthropic_model: str
    anthropic_effort: str
    guild_id: int | None
    channel_id: int | None
    allowed_user_ids: set[int]
    repo_path: Path
    content_root: Path
    site_base_url: str | None
    sqlite_path: Path


def _int_or_none(value: str | None) -> int | None:
    if value is None or value.strip() == "":
        return None
    return int(value.strip())


def _parse_user_ids(value: str | None) -> set[int]:
    if not value:
        return set()
    return {int(v.strip()) for v in value.split(",") if v.strip()}


def load_config(*, require_discord: bool = True, dotenv: bool = True) -> Config:
    if dotenv:
        load_dotenv()

    # `or` (not a .get default): dotenv loads blank lines like `REPO_PATH=` as
    # empty strings, which must fall back to the default too.
    repo_path = Path(os.environ.get("REPO_PATH") or str(_REPO_ROOT)).expanduser().resolve()
    cfg = Config(
        discord_token=os.environ.get("DISCORD_TOKEN"),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
        anthropic_model=os.environ.get("ANTHROPIC_MODEL") or "claude-opus-4-8",
        anthropic_effort=os.environ.get("ANTHROPIC_EFFORT") or "low",
        guild_id=_int_or_none(os.environ.get("GUILD_ID")),
        channel_id=_int_or_none(os.environ.get("CHANNEL_ID")),
        allowed_user_ids=_parse_user_ids(os.environ.get("ALLOWED_USER_IDS")),
        repo_path=repo_path,
        content_root=repo_path / "content",
        site_base_url=os.environ.get("SITE_BASE_URL") or None,
        sqlite_path=Path(__file__).resolve().parents[1] / "lorebot.sqlite",
    )

    missing = []
    if not cfg.anthropic_api_key:
        missing.append("ANTHROPIC_API_KEY")
    if not cfg.content_root.is_dir():
        missing.append(f"REPO_PATH (content/ not found at {cfg.content_root})")
    if require_discord:
        if not cfg.discord_token:
            missing.append("DISCORD_TOKEN")
        if cfg.guild_id is None:
            missing.append("GUILD_ID")
        if cfg.channel_id is None:
            missing.append("CHANNEL_ID")
        if not cfg.allowed_user_ids:
            missing.append("ALLOWED_USER_IDS")

    if missing:
        raise ConfigError(
            "Missing/invalid configuration: "
            + ", ".join(missing)
            + ".\nCopy bot/.env.example to bot/.env and fill it in."
        )
    return cfg
