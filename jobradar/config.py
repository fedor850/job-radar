"""Configuration: secrets from an .env-style file, everything else from config.yaml.

Lookup order for files (first existing wins):
  secrets:  $JOBRADAR_SECRETS, /etc/jobradar/secrets.env, <project>/secrets.env
  config:   $JOBRADAR_CONFIG,  <project>/config.yaml
  profile:  <project>/profile.md
Data (SQLite, logs, backups) goes to $JOBRADAR_DATA or <project>/data.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PROJECT_DIR = Path(__file__).resolve().parent.parent


def _first_existing(*paths: str | Path | None) -> Path | None:
    for p in paths:
        if p and Path(p).exists():
            return Path(p)
    return None


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines. Ignores comments/blank lines, strips quotes and whitespace."""
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'").strip()
        if key:
            out[key] = val
    return out


@dataclass
class Settings:
    secrets: dict[str, str]
    cfg: dict[str, Any]
    profile_text: str
    data_dir: Path
    project_dir: Path = PROJECT_DIR
    extra: dict[str, Any] = field(default_factory=dict)

    # ---- secrets -------------------------------------------------------
    def secret(self, key: str, default: str = "") -> str:
        return os.environ.get(key) or self.secrets.get(key) or default

    @property
    def telegram_token(self) -> str:
        return self.secret("TELEGRAM_BOT_TOKEN")

    @property
    def anthropic_key(self) -> str:
        return self.secret("ANTHROPIC_API_KEY")

    @property
    def apps_script_url(self) -> str:
        return self.secret("APPS_SCRIPT_URL")

    @property
    def apps_script_secret(self) -> str:
        return self.secret("APPS_SCRIPT_SECRET")

    @property
    def owner_chat_id(self) -> int | None:
        v = self.secret("TELEGRAM_CHAT_ID")
        return int(v) if v and v.lstrip("-").isdigit() else None

    # ---- config helpers ------------------------------------------------
    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.cfg
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    @property
    def db_path(self) -> Path:
        return self.data_dir / "jobradar.sqlite3"

    @property
    def tz(self) -> str:
        return self.get("general.timezone", "Europe/Vienna")


def load_settings() -> Settings:
    secrets_path = _first_existing(
        os.environ.get("JOBRADAR_SECRETS"),
        "/etc/jobradar/secrets.env",
        PROJECT_DIR / "secrets.env",
    )
    secrets = parse_env_file(secrets_path) if secrets_path else {}

    cfg_path = _first_existing(os.environ.get("JOBRADAR_CONFIG"), PROJECT_DIR / "config.yaml")
    if not cfg_path:
        raise FileNotFoundError("config.yaml not found")
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    profile_path = _first_existing(os.environ.get("JOBRADAR_PROFILE"), PROJECT_DIR / "profile.md")
    profile_text = profile_path.read_text(encoding="utf-8") if profile_path else ""

    from .util import set_home_patterns  # the search area is used by every source → set it before they start
    loc = cfg.get("location") or {}
    set_home_patterns(loc.get("home_patterns") or None, loc.get("country_patterns") or None)

    data_dir = Path(os.environ.get("JOBRADAR_DATA", PROJECT_DIR / "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return Settings(secrets=secrets, cfg=cfg, profile_text=profile_text, data_dir=data_dir)
