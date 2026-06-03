from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .auth import can_use_headed_browser, setup_profile
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

    subparsers.add_parser("doctor", help="Verify config, auth, and Grafana access")
    subparsers.add_parser("run", help="Run MCP server over stdio")

    args = parser.parse_args(argv)

    if args.command == "setup":
        do_setup(args)
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


if __name__ == "__main__":
    main()
