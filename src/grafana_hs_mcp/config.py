from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


APP_DIR = Path(os.getenv("GRAFANA_HS_MCP_HOME", "~/.grafana-hs-mcp")).expanduser()
CONFIG_FILE = APP_DIR / "config.json"
PROFILE_DIR = APP_DIR / "profile"
SESSION_FILE = APP_DIR / "session.json"


@dataclass(frozen=True)
class Config:
    grafana_url: str
    loki_datasource_uid: str = "loki"
    profile_dir: Path = PROFILE_DIR
    api_token: str | None = None


def ensure_app_dir() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> Config:
    cfg: dict = {}
    if CONFIG_FILE.exists():
        cfg = json.loads(CONFIG_FILE.read_text())

    grafana_url = os.getenv("GRAFANA_URL") or cfg.get("grafana_url")
    if not grafana_url:
        raise RuntimeError(
            "Grafana URL not configured. Run `grafana-hs-mcp setup` first "
            "or set GRAFANA_URL."
        )

    profile_dir = os.getenv("GRAFANA_HS_MCP_PROFILE_DIR") or cfg.get(
        "profile_dir", str(PROFILE_DIR)
    )

    return Config(
        grafana_url=grafana_url.rstrip("/"),
        loki_datasource_uid=os.getenv("GRAFANA_LOKI_DATASOURCE_UID")
        or cfg.get("loki_datasource_uid", "loki"),
        profile_dir=Path(profile_dir).expanduser(),
        api_token=os.getenv("GRAFANA_API_TOKEN") or cfg.get("api_token"),
    )


def save_config(config: Config) -> None:
    ensure_app_dir()
    data = {
        "grafana_url": config.grafana_url,
        "loki_datasource_uid": config.loki_datasource_uid,
        "profile_dir": str(config.profile_dir),
    }
    CONFIG_FILE.write_text(json.dumps(data, indent=2) + "\n")
    CONFIG_FILE.chmod(0o600)
