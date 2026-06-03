from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from .auth import can_use_headed_browser, ensure_playwright_chromium, setup_profile
from .config import Config, CONFIG_FILE, PROFILE_DIR, load_config, save_config
from .grafana_client import GrafanaClient
from .server import run as run_server


DEFAULT_GRAFANA_URL = "https://grafana.internal.staging.in1.hyperswitch.net"


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
    env_cmd.add_argument("--interactive", "-i", action="store_true", help="Pause and offer to run doctor")
    env_cmd.add_argument("--show-secrets", action="store_true", help="Show secret values instead of masking them")

    subparsers.add_parser("doctor", help="Verify config, auth, and Grafana access")
    subparsers.add_parser("run", help="Run MCP server over stdio")

    args = parser.parse_args(argv)

    if args.command == "setup":
        do_setup(args)
    elif args.command == "env":
        do_env(args)
    elif args.command == "doctor":
        do_doctor()
    elif args.command == "run":
        run_server()
    else:
        run_server()


def do_setup(args) -> None:
    grafana_url = args.grafana_url or input(f"Grafana URL [{DEFAULT_GRAFANA_URL}]: ").strip() or DEFAULT_GRAFANA_URL
    profile_dir = Path(args.profile_dir).expanduser()

    if not args.skip_browser and not args.headless and not can_use_headed_browser():
        print("No DISPLAY/WAYLAND_DISPLAY found, so a headed browser cannot open here.", file=sys.stderr)
        print("Run this command on a desktop machine, or start an Xvfb display first.", file=sys.stderr)
        print("Example: DISPLAY=:200 grafana-hs-mcp setup", file=sys.stderr)
        sys.exit(1)

    cfg = Config(
        grafana_url=grafana_url.rstrip("/"),
        loki_datasource_uid=args.loki_datasource_uid,
        profile_dir=profile_dir,
    )
    save_config(cfg)
    print(f"Saved config: {CONFIG_FILE}")
    ensure_playwright_chromium()
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


def do_env(args) -> None:
    config_exists = CONFIG_FILE.exists()
    cfg = load_config() if config_exists or os.getenv("GRAFANA_URL") else None

    rows = [
        ("Config file", str(CONFIG_FILE), "exists" if config_exists else "missing"),
        ("GRAFANA_HS_MCP_HOME", os.getenv("GRAFANA_HS_MCP_HOME", ""), "env" if os.getenv("GRAFANA_HS_MCP_HOME") else "default"),
        ("GRAFANA_URL", cfg.grafana_url if cfg else "", _source("GRAFANA_URL", config_exists)),
        ("GRAFANA_LOKI_DATASOURCE_UID", cfg.loki_datasource_uid if cfg else "", _source("GRAFANA_LOKI_DATASOURCE_UID", config_exists)),
        ("GRAFANA_HS_MCP_PROFILE_DIR", str(cfg.profile_dir) if cfg else str(PROFILE_DIR), _source("GRAFANA_HS_MCP_PROFILE_DIR", config_exists)),
        ("GRAFANA_API_TOKEN", _secret(os.getenv("GRAFANA_API_TOKEN"), args.show_secrets), "env" if os.getenv("GRAFANA_API_TOKEN") else "not set"),
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
    print("  grafana-hs-mcp run")

    if args.interactive:
        print()
        answer = input("Run doctor now? [y/N]: ").strip().lower()
        if answer in {"y", "yes"}:
            do_doctor()


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
