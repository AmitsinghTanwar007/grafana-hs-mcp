from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


APP_DIR = Path(os.getenv("GRAFANA_HS_MCP_HOME", "~/.grafana-hs-mcp")).expanduser()
CONFIG_FILE = APP_DIR / "config.json"
# Legacy single-instance paths (kept for backward compat)
PROFILE_DIR = APP_DIR / "profile"
SESSION_FILE = APP_DIR / "session.json"
# Per-instance paths
SESSIONS_DIR = APP_DIR / "sessions"
PROFILES_DIR = APP_DIR / "profiles"


@dataclass(frozen=True)
class Config:
    grafana_url: str
    loki_datasource_uid: str = "loki"
    clickhouse_datasource_uid: str | None = None
    profile_dir: Path = PROFILE_DIR
    api_token: str | None = None
    name: str = "default"


def session_file_for(name: str) -> Path:
    """Return the session-cookie file for a named instance.

    The legacy 'default' instance keeps the old path so existing setups
    don't need to re-authenticate.
    """
    if name == "default":
        return SESSION_FILE
    return SESSIONS_DIR / f"{name}.json"


def ensure_app_dir() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)


def _default_profile_dir(name: str) -> Path:
    return PROFILE_DIR if name == "default" else PROFILES_DIR / name


def _instance_from_dict(name: str, data: dict) -> Config:
    profile_dir_str = data.get("profile_dir")
    profile_dir = Path(profile_dir_str).expanduser() if profile_dir_str else _default_profile_dir(name)
    # Per-instance token env var takes precedence, then generic one, then config
    api_token = (
        os.getenv(f"GRAFANA_API_TOKEN_{name.upper()}")
        or os.getenv("GRAFANA_API_TOKEN")
        or data.get("api_token")
    )
    return Config(
        grafana_url=data["grafana_url"].rstrip("/"),
        loki_datasource_uid=data.get("loki_datasource_uid", "loki"),
        clickhouse_datasource_uid=data.get("clickhouse_datasource_uid"),
        profile_dir=profile_dir,
        api_token=api_token,
        name=name,
    )


def _read_config_file() -> dict:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {}


def load_config(name: str | None = None) -> Config:
    """Load a named instance's Config, or the default instance if name is None."""
    cfg = _read_config_file()

    # ── Multi-instance format ──────────────────────────────────────────────────
    if "instances" in cfg:
        instances: dict = cfg["instances"]
        default_name = cfg.get("default_instance") or next(iter(instances), "default")
        instance_name = name or default_name
        if instance_name not in instances:
            available = list(instances.keys())
            raise RuntimeError(
                f"Grafana instance '{instance_name}' not found. "
                f"Available: {available}. "
                "Run `grafana-hs-mcp instances` to list all, or "
                "`grafana-hs-mcp setup --name <name>` to add one."
            )
        return _instance_from_dict(instance_name, instances[instance_name])

    # ── Legacy single-instance format ─────────────────────────────────────────
    if name and name != "default":
        raise RuntimeError(
            f"Instance '{name}' not found. "
            "Only a single unnamed instance is configured. "
            "Run `grafana-hs-mcp setup --name <name>` to add another."
        )
    grafana_url = os.getenv("GRAFANA_URL") or cfg.get("grafana_url")
    if not grafana_url:
        raise RuntimeError(
            "Grafana URL not configured. Run `grafana-hs-mcp setup` first "
            "or set GRAFANA_URL."
        )
    profile_dir_str = (
        os.getenv("GRAFANA_HS_MCP_PROFILE_DIR")
        or cfg.get("profile_dir", str(PROFILE_DIR))
    )
    return Config(
        grafana_url=grafana_url.rstrip("/"),
        loki_datasource_uid=os.getenv("GRAFANA_LOKI_DATASOURCE_UID") or cfg.get("loki_datasource_uid", "loki"),
        clickhouse_datasource_uid=os.getenv("GRAFANA_CLICKHOUSE_DATASOURCE_UID") or cfg.get("clickhouse_datasource_uid"),
        profile_dir=Path(profile_dir_str).expanduser(),
        api_token=os.getenv("GRAFANA_API_TOKEN") or cfg.get("api_token"),
        name="default",
    )


def load_all_instances() -> dict[str, Config]:
    """Return all configured instances as {name: Config}."""
    cfg = _read_config_file()
    if "instances" in cfg:
        return {n: _instance_from_dict(n, d) for n, d in cfg["instances"].items()}
    try:
        single = load_config()
        return {single.name: single}
    except RuntimeError:
        return {}


def get_default_instance_name() -> str | None:
    """Return the name of the default instance, or None if nothing is configured."""
    cfg = _read_config_file()
    if "instances" in cfg:
        return cfg.get("default_instance") or next(iter(cfg["instances"]), None)
    if cfg.get("grafana_url") or os.getenv("GRAFANA_URL"):
        return "default"
    return None


def save_instance(name: str, config: Config, set_default: bool = False) -> None:
    """Save or update a named instance.  Migrates legacy single-instance format."""
    ensure_app_dir()
    cfg = _read_config_file()

    # Migrate legacy format → multi-instance
    if "grafana_url" in cfg and "instances" not in cfg:
        legacy_data: dict = {"grafana_url": cfg.pop("grafana_url")}
        for k in ("loki_datasource_uid", "clickhouse_datasource_uid", "profile_dir"):
            if k in cfg:
                legacy_data[k] = cfg.pop(k)
        cfg.pop("api_token", None)
        cfg["instances"] = {"default": legacy_data}
        cfg.setdefault("default_instance", "default")

    cfg.setdefault("instances", {})

    # First instance ever → becomes default automatically
    if not cfg.get("default_instance") or not cfg["instances"]:
        cfg["default_instance"] = name
    elif set_default:
        cfg["default_instance"] = name

    instance_data: dict = {
        "grafana_url": config.grafana_url,
        "loki_datasource_uid": config.loki_datasource_uid,
    }
    if config.clickhouse_datasource_uid:
        instance_data["clickhouse_datasource_uid"] = config.clickhouse_datasource_uid
    # Only persist profile_dir when it differs from the computed default
    if config.profile_dir != _default_profile_dir(name):
        instance_data["profile_dir"] = str(config.profile_dir)

    cfg["instances"][name] = instance_data
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2) + "\n")
    CONFIG_FILE.chmod(0o600)


def save_config(config: Config) -> None:
    """Backward-compatible: save the Config as its named instance."""
    save_instance(config.name or "default", config)
