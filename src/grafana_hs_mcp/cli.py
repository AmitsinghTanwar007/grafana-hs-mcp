from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .auth import ensure_playwright_chromium, setup_profile
from .config import APP_DIR, Config, CONFIG_FILE, PROFILE_DIR, load_config, save_config
from .grafana_client import GrafanaClient


DEFAULT_GRAFANA_URL = "https://grafana.internal.staging.in1.hyperswitch.net"
REPO_URL = "git+https://github.com/AmitsinghTanwar007/grafana-hs-mcp.git"


def _default_claude_config_path() -> Path:
    if sys.platform == "darwin":
        return Path("~/Library/Application Support/Claude/claude_desktop_config.json").expanduser()
    if sys.platform.startswith("win"):
        appdata = os.getenv("APPDATA", "")
        return Path(appdata) / "Claude" / "claude_desktop_config.json"
    return Path("~/.config/Claude/claude_desktop_config.json").expanduser()


OPENCODE_CONFIG_FILE = Path(
    os.getenv("OPENCODE_CONFIG_FILE", "~/.config/opencode/opencode.json")
).expanduser()
CLAUDE_CONFIG_FILE = Path(os.getenv("CLAUDE_CONFIG_FILE", str(_default_claude_config_path()))).expanduser()
CURSOR_CONFIG_FILE = Path(os.getenv("CURSOR_CONFIG_FILE", "~/.cursor/mcp.json")).expanduser()
CODEX_CONFIG_FILE = Path(os.getenv("CODEX_CONFIG_FILE", "~/.codex/config.toml")).expanduser()


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(prog="grafana-hs-mcp")
    subparsers = parser.add_subparsers(dest="command")

    setup_cmd = subparsers.add_parser("setup", help="Configure Grafana URL and create Playwright SSO profile")
    setup_cmd.add_argument("--grafana-url", default=None)
    setup_cmd.add_argument("--loki-datasource-uid", default="loki")
    setup_cmd.add_argument("--profile-dir", default=str(PROFILE_DIR), help="Playwright profile directory")
    setup_cmd.add_argument("--skip-browser", action="store_true", help="Only save config; useful when reusing an existing profile")
    setup_cmd.add_argument("--headless", action="store_true", help="Run browser headless; useful only if profile is already valid")

    env_cmd = subparsers.add_parser("env", help="Show effective config/env values")
    env_cmd.add_argument("--interactive", "-i", action="store_true", help="Show config, offer edits, then optionally run doctor")
    env_cmd.add_argument("--show-secrets", action="store_true", help="Show secret values instead of masking them")

    subparsers.add_parser("doctor", help="Verify config, auth, and Grafana access")
    subparsers.add_parser("configure-opencode", help="Add grafana-hs-mcp to opencode MCP config")
    subparsers.add_parser("configure-claude", help="Add grafana-hs-mcp to Claude Desktop MCP config")
    subparsers.add_parser("configure-cursor", help="Add grafana-hs-mcp to Cursor MCP config")
    subparsers.add_parser("configure-codex", help="Add grafana-hs-mcp to Codex MCP config")
    subparsers.add_parser("configure-all", help="Configure all supported AI clients")
    subparsers.add_parser("update", help="Update grafana-hs-mcp to the latest GitHub version")
    cleanup_cmd = subparsers.add_parser(
        "cleanup",
        help="Remove local files; add --browser-cache to remove Playwright cache",
    )
    cleanup_cmd.add_argument("--yes", "-y", action="store_true", help="Skip confirmation")
    cleanup_cmd.add_argument("--browser-cache", action="store_true", help="Also remove Playwright browser cache")
    subparsers.add_parser("run", help="Run MCP server over stdio")

    args = parser.parse_args(argv)

    if args.command == "setup":
        do_setup(args)
    elif args.command == "env":
        do_env(args)
    elif args.command == "doctor":
        do_doctor()
    elif args.command == "configure-opencode":
        do_configure_opencode()
    elif args.command == "configure-claude":
        do_configure_claude()
    elif args.command == "configure-cursor":
        do_configure_cursor()
    elif args.command == "configure-codex":
        do_configure_codex()
    elif args.command == "configure-all":
        do_configure_all()
    elif args.command == "update":
        do_update()
    elif args.command == "cleanup":
        do_cleanup(args)
    elif args.command == "run":
        from .server import run as run_server
        run_server()
    else:
        from .server import run as run_server
        run_server()


def do_setup(args) -> None:
    grafana_url = args.grafana_url or input(f"Grafana URL [{DEFAULT_GRAFANA_URL}]: ").strip() or DEFAULT_GRAFANA_URL
    profile_dir = Path(args.profile_dir).expanduser()

    cfg = Config(
        grafana_url=grafana_url.rstrip("/"),
        loki_datasource_uid=args.loki_datasource_uid,
        profile_dir=profile_dir,
    )
    save_config(cfg)
    print(f"Saved config: {CONFIG_FILE}")
    ensure_playwright_chromium(interactive=True)
    if args.skip_browser:
        print(f"Using existing Playwright profile: {cfg.profile_dir}")
    else:
        print(f"Creating Playwright profile: {cfg.profile_dir}")
        setup_profile(cfg.grafana_url, cfg.profile_dir, headless=args.headless)
    print("Setup complete. Running doctor...")
    do_doctor()


def do_doctor() -> None:
    cfg = load_config()
    client = GrafanaClient(cfg)
    health = client.health_check()
    print("Grafana health:", health)
    try:
        datasources = client.list_datasources()
        print(f"Datasources: {len(datasources)}")
        for ds in datasources[:10]:
            print(f"  - {ds.get('name')} ({ds.get('type')}) uid={ds.get('uid')}")
    finally:
        client.stop_heartbeat()


def do_update() -> None:
    print("Updating grafana-hs-mcp...")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--force-reinstall",
            "--no-cache-dir",
            REPO_URL,
        ],
        check=True,
    )
    print()
    print("grafana-hs-mcp updated successfully")
    print("Run: grafana-hs-mcp doctor")


def do_cleanup(args) -> None:
    paths = [APP_DIR]
    bin_path = Path("~/.local/bin/grafana-hs-mcp").expanduser()
    if bin_path.exists() or bin_path.is_symlink():
        paths.append(bin_path)
    current_binary = shutil.which("grafana-hs-mcp")
    if current_binary:
        current_binary_path = Path(current_binary)
        if current_binary_path not in paths:
            paths.append(current_binary_path)

    if args.browser_cache:
        paths.extend(_playwright_cache_paths())

    print("The following paths will be removed if they exist:")
    for path in paths:
        print(f"  {path}")

    if not args.yes:
        answer = input("Continue? [y/N]: ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Cancelled.")
            return

    for path in paths:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
            print(f"Removed: {path}")
        elif path.exists():
            shutil.rmtree(path)
            print(f"Removed: {path}")

    print("Cleanup complete.")


def do_configure_opencode() -> None:
    data = _read_json_config(OPENCODE_CONFIG_FILE)
    data.setdefault("$schema", "https://opencode.ai/config.json")
    mcp = data.setdefault("mcp", {})
    mcp["grafana"] = {
        "type": "local",
        "command": ["grafana-hs-mcp"],
        "enabled": True,
    }
    _write_json_config(OPENCODE_CONFIG_FILE, data)
    print(f"Updated opencode config: {OPENCODE_CONFIG_FILE}")
    print()
    print("Restart opencode for the MCP server to load.")
    print("Then ask: List Grafana datasources.")


def do_configure_claude() -> None:
    data = _read_json_config(CLAUDE_CONFIG_FILE)
    mcp = data.setdefault("mcpServers", {})
    mcp["grafana"] = {"command": "grafana-hs-mcp"}
    _write_json_config(CLAUDE_CONFIG_FILE, data)
    print(f"Updated Claude Desktop config: {CLAUDE_CONFIG_FILE}")
    print("Restart Claude Desktop for the MCP server to load.")


def do_configure_cursor() -> None:
    data = _read_json_config(CURSOR_CONFIG_FILE)
    mcp = data.setdefault("mcpServers", {})
    mcp["grafana"] = {"command": "grafana-hs-mcp"}
    _write_json_config(CURSOR_CONFIG_FILE, data)
    print(f"Updated Cursor config: {CURSOR_CONFIG_FILE}")
    print("Restart Cursor for the MCP server to load.")


def do_configure_codex() -> None:
    CODEX_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    text = CODEX_CONFIG_FILE.read_text() if CODEX_CONFIG_FILE.exists() else ""
    text = _upsert_toml_table(
        text,
        "mcp_servers.grafana",
        'command = "grafana-hs-mcp"',
    )
    CODEX_CONFIG_FILE.write_text(text)
    print(f"Updated Codex config: {CODEX_CONFIG_FILE}")
    print("Restart Codex for the MCP server to load.")


def do_configure_all() -> None:
    do_configure_opencode()
    print()
    do_configure_claude()
    print()
    do_configure_cursor()
    print()
    do_configure_codex()
    print()
    print("Done. Restart the AI client you want to use.")


def do_env(args) -> None:
    config_exists = CONFIG_FILE.exists()
    cfg = load_config() if config_exists or os.getenv("GRAFANA_URL") else None

    _print_env(cfg, config_exists, args.show_secrets)

    if args.interactive:
        print()
        answer = input("Update saved config values? [y/N]: ").strip().lower()
        if answer in {"y", "yes"}:
            cfg = _prompt_config(cfg)
            save_config(cfg)
            config_exists = True
            print(f"Saved config: {CONFIG_FILE}")
            print()
            _print_env(cfg, config_exists, args.show_secrets)

        print()
        answer = input("Run doctor now? [y/N]: ").strip().lower()
        if answer in {"y", "yes"}:
            do_doctor()


def _print_env(cfg: Config | None, config_exists: bool, show_secrets: bool) -> None:

    rows = [
        ("Config file", str(CONFIG_FILE), "exists" if config_exists else "missing"),
        ("GRAFANA_HS_MCP_HOME", os.getenv("GRAFANA_HS_MCP_HOME", ""), "env" if os.getenv("GRAFANA_HS_MCP_HOME") else "default"),
        ("GRAFANA_URL", cfg.grafana_url if cfg else "", _source("GRAFANA_URL", config_exists)),
        ("GRAFANA_LOKI_DATASOURCE_UID", cfg.loki_datasource_uid if cfg else "", _source("GRAFANA_LOKI_DATASOURCE_UID", config_exists)),
        ("GRAFANA_HS_MCP_PROFILE_DIR", str(cfg.profile_dir) if cfg else str(PROFILE_DIR), _source("GRAFANA_HS_MCP_PROFILE_DIR", config_exists)),
        ("GRAFANA_API_TOKEN", _secret(os.getenv("GRAFANA_API_TOKEN"), show_secrets), "env" if os.getenv("GRAFANA_API_TOKEN") else "not set"),
        ("DISPLAY", os.getenv("DISPLAY", ""), "env" if os.getenv("DISPLAY") else "not set"),
        ("WAYLAND_DISPLAY", os.getenv("WAYLAND_DISPLAY", ""), "env" if os.getenv("WAYLAND_DISPLAY") else "not set"),
        ("Profile exists", "yes" if cfg and cfg.profile_dir.exists() else "no", str(cfg.profile_dir if cfg else PROFILE_DIR)),
    ]

    print("Grafana HS MCP environment")
    print("=" * 28)
    width = max(len(k) for k, _, _ in rows)
    for key, value, source in rows:
        print(f"{key:<{width}} : {value or '-'}  ({source})")

    print()
    print("Useful commands:")
    print("  grafana-hs-mcp setup")
    print("  grafana-hs-mcp doctor")
    print("  grafana-hs-mcp configure-opencode")
    print("  grafana-hs-mcp configure-claude")
    print("  grafana-hs-mcp configure-cursor")
    print("  grafana-hs-mcp configure-codex")
    print("  grafana-hs-mcp configure-all")
    print("  grafana-hs-mcp update")
    print("  grafana-hs-mcp cleanup")
    print("  grafana-hs-mcp run")



def _prompt_config(cfg: Config | None) -> Config:
    current_url = cfg.grafana_url if cfg else DEFAULT_GRAFANA_URL
    current_loki_uid = cfg.loki_datasource_uid if cfg else "loki"
    current_profile_dir = cfg.profile_dir if cfg else PROFILE_DIR

    print()
    print("Update config values. Press Enter to keep the current value.")

    grafana_url = input(f"Grafana URL [{current_url}]: ").strip() or current_url
    loki_uid = input(f"Loki datasource UID [{current_loki_uid}]: ").strip() or current_loki_uid
    profile_dir_raw = input(f"Playwright profile dir [{current_profile_dir}]: ").strip()
    profile_dir = Path(profile_dir_raw).expanduser() if profile_dir_raw else current_profile_dir

    print()
    print("Note: GRAFANA_API_TOKEN is not saved here. Set it as an environment variable if needed.")

    return Config(
        grafana_url=grafana_url.rstrip("/"),
        loki_datasource_uid=loki_uid,
        profile_dir=profile_dir,
    )


def _read_json_config(path: Path) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Could not parse {path}. Please fix the JSON manually and run this command again."
        ) from exc


def _write_json_config(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _upsert_toml_table(text: str, table: str, body: str) -> str:
    lines = text.splitlines()
    output: list[str] = []
    skipping = False
    table_header = f"[{table}]"

    for line in lines:
        stripped = line.strip()
        if stripped == table_header:
            skipping = True
            continue
        if skipping and re.match(r"^\[[^]]+\]$", stripped):
            skipping = False
        if not skipping:
            output.append(line)

    base = "\n".join(output).rstrip()
    if base:
        base += "\n\n"
    return f"{base}{table_header}\n{body}\n"


def _playwright_cache_paths() -> list[Path]:
    if sys.platform == "darwin":
        return [Path("~/Library/Caches/ms-playwright").expanduser()]
    if sys.platform.startswith("win"):
        local_appdata = os.getenv("LOCALAPPDATA", "")
        return [Path(local_appdata) / "ms-playwright"] if local_appdata else []
    return [Path("~/.cache/ms-playwright").expanduser()]


def _source(env_name: str, has_config: bool) -> str:
    if os.getenv(env_name):
        return "env"
    return "config" if has_config else "default/missing"


def _secret(value: str | None, show: bool) -> str:
    if not value:
        return ""
    if show:
        return value
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


if __name__ == "__main__":
    main()
