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

import requests

from .auth import (
    AuthManager,
    can_use_headed_browser,
    ensure_playwright_chromium,
    load_saved_cookie,
    setup_cookie_from_existing_browser,
    setup_profile,
)
from .config import (
    APP_DIR,
    CONFIG_FILE,
    PROFILE_DIR,
    SESSION_FILE,
    Config,
    load_config,
    load_all_instances,
    save_config,
    save_instance,
    session_file_for,
    _default_profile_dir,
)
from .grafana_client import GrafanaClient


DEFAULT_GRAFANA_URL = ""
REPO_URL = "git+https://github.com/AmitsinghTanwar007/grafana-hs-mcp.git"


def _default_claude_config_path() -> Path:
    if sys.platform == "darwin":
        return Path(
            "~/Library/Application Support/Claude/claude_desktop_config.json"
        ).expanduser()
    if sys.platform.startswith("win"):
        appdata = os.getenv("APPDATA", "")
        return Path(appdata) / "Claude" / "claude_desktop_config.json"
    return Path("~/.config/Claude/claude_desktop_config.json").expanduser()


OPENCODE_CONFIG_FILE = Path(
    os.getenv("OPENCODE_CONFIG_FILE", "~/.config/opencode/opencode.json")
).expanduser()
CLAUDE_CONFIG_FILE = Path(
    os.getenv("CLAUDE_CONFIG_FILE", str(_default_claude_config_path()))
).expanduser()
CURSOR_CONFIG_FILE = Path(
    os.getenv("CURSOR_CONFIG_FILE", "~/.cursor/mcp.json")
).expanduser()
CODEX_CONFIG_FILE = Path(
    os.getenv("CODEX_CONFIG_FILE", "~/.codex/config.toml")
).expanduser()


_DESCRIPTION = (
    "MCP server for Grafana — query Loki logs, PostgreSQL, and ClickHouse via your AI assistant."
)

_EPILOG = """
commands:
  Setup & Verification:
    setup                        Configure a Grafana instance (default or named)
    setup --name sbx             Add a second instance named 'sbx'
    instances                    List all configured Grafana instances
    doctor                       Verify all instances (or --name for one)
    env                          Show current config and environment values
    env --interactive            View and interactively edit config

  AI Client Integration:
    configure-opencode    Add grafana-hs-mcp to opencode MCP config
    configure-claude      Add grafana-hs-mcp to Claude Desktop MCP config
    configure-claude-code Add grafana-hs-mcp to Claude Code (CLI) MCP config
    configure-cursor      Add grafana-hs-mcp to Cursor MCP config
    configure-codex       Add grafana-hs-mcp to Codex MCP config
    configure-all         Configure all supported AI clients at once

  Maintenance:
    update                Update to latest version from GitHub
    cleanup               Remove local data files and browser profiles
    cleanup --browser-cache  Also remove Playwright browser cache
    run                   Start MCP server over stdio (used by AI clients)

examples:
  grafana-hs-mcp setup                         # First-time setup
  grafana-hs-mcp setup --name prod             # Add a 'prod' instance
  grafana-hs-mcp setup --name sbx              # Add a 'sbx' instance (all instances are peers)
  grafana-hs-mcp instances                     # List all configured instances
  grafana-hs-mcp doctor                        # Check all instances
  grafana-hs-mcp doctor --name prod            # Check only 'prod'
  grafana-hs-mcp env --interactive             # View and edit config
  grafana-hs-mcp configure-all                 # Register with all AI clients
  grafana-hs-mcp update                        # Update to latest version
"""


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )

    parser = argparse.ArgumentParser(
        prog="grafana-hs-mcp",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", metavar="command")

    setup_cmd = subparsers.add_parser(
        "setup", help="Configure a Grafana instance (default or named with --name)"
    )
    setup_cmd.add_argument(
        "--name", default="default",
        help="Instance name (default: 'default'). Use e.g. --name prod to add a second instance.",
    )
    setup_cmd.add_argument(
        "--grafana-url", default=None, help="Grafana base URL (prompted if not given)"
    )
    setup_cmd.add_argument(
        "--loki-datasource-uid",
        default="loki",
        help="Loki datasource UID (default: loki)",
    )
    setup_cmd.add_argument(
        "--clickhouse-datasource-uid",
        default=None,
        help="Default ClickHouse datasource UID (optional)",
    )
    setup_cmd.add_argument(
        "--profile-dir",
        default=None,
        help="Playwright browser profile directory (default: ~/.grafana-hs-mcp/profiles/<name>/)",
    )
    setup_cmd.add_argument(
        "--skip-browser",
        action="store_true",
        help="Only save config; skip browser login/import (reuse existing session/profile)",
    )
    setup_cmd.add_argument(
        "--headless",
        action="store_true",
        help="Run browser headless (only works if profile is already valid)",
    )

    env_cmd = subparsers.add_parser(
        "env", help="Show current config and environment values"
    )
    env_cmd.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="Interactively edit config values, then optionally run doctor",
    )
    env_cmd.add_argument(
        "--show-secrets",
        action="store_true",
        help="Show secret values instead of masking them",
    )

    doctor_cmd = subparsers.add_parser(
        "doctor", help="Verify config, auth, and Grafana connectivity"
    )
    doctor_cmd.add_argument(
        "--name", default=None,
        help="Instance name to check (default: check all configured instances)",
    )
    subparsers.add_parser(
        "instances", help="List all configured Grafana instances",
    )
    subparsers.add_parser(
        "configure-opencode", help="Add grafana-hs-mcp to opencode MCP config"
    )
    subparsers.add_parser(
        "configure-claude", help="Add grafana-hs-mcp to Claude Desktop MCP config"
    )
    subparsers.add_parser(
        "configure-claude-code",
        help="Add grafana-hs-mcp to Claude Code (CLI) MCP config",
    )
    subparsers.add_parser(
        "configure-cursor", help="Add grafana-hs-mcp to Cursor MCP config"
    )
    subparsers.add_parser(
        "configure-codex", help="Add grafana-hs-mcp to Codex MCP config"
    )
    subparsers.add_parser(
        "configure-all", help="Configure all supported AI clients at once"
    )
    subparsers.add_parser(
        "update", help="Update grafana-hs-mcp to the latest version from GitHub"
    )
    cleanup_cmd = subparsers.add_parser(
        "cleanup", help="Remove local data files and browser profile"
    )
    cleanup_cmd.add_argument(
        "--yes", "-y", action="store_true", help="Skip confirmation prompt"
    )
    cleanup_cmd.add_argument(
        "--browser-cache",
        action="store_true",
        help="Also remove Playwright browser cache (~/.cache/ms-playwright)",
    )
    subparsers.add_parser(
        "run", help="Start MCP server over stdio (used by AI clients)"
    )

    args = parser.parse_args(argv)

    try:
        if args.command == "setup":
            do_setup(args)
        elif args.command == "instances":
            do_instances()
        elif args.command == "env":
            do_env(args)
        elif args.command == "doctor":
            do_doctor(getattr(args, "name", None))
        elif args.command == "configure-opencode":
            do_configure_opencode()
        elif args.command == "configure-claude":
            do_configure_claude()
        elif args.command == "configure-claude-code":
            do_configure_claude_code()
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
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        raise SystemExit(1)


def do_setup(args) -> None:
    name = args.name or "default"
    default_url = args.grafana_url or os.getenv("GRAFANA_URL", "")
    prompt = f"Grafana URL [{default_url}]: " if default_url else "Grafana URL: "
    grafana_url = args.grafana_url or input(prompt).strip() or default_url
    if not grafana_url:
        raise SystemExit(
            "Grafana URL is required. Example: https://grafana.example.com"
        )

    profile_dir = (
        Path(args.profile_dir).expanduser()
        if args.profile_dir
        else _default_profile_dir(name)
    )
    session_file = session_file_for(name)
    _assert_grafana_reachable(grafana_url)

    cfg = Config(
        grafana_url=grafana_url.rstrip("/"),
        loki_datasource_uid=args.loki_datasource_uid,
        clickhouse_datasource_uid=args.clickhouse_datasource_uid,
        profile_dir=profile_dir,
        name=name,
    )
    save_instance(name, cfg)
    print(f"Saved config: {CONFIG_FILE}  (instance: {name})")

    if args.skip_browser:
        print("Skipped browser login/import. Using existing saved session or profile.")
    else:
        got_browser_cookie = setup_cookie_from_existing_browser(
            cfg.grafana_url, session_file=session_file
        )
        if got_browser_cookie and _saved_cookie_authenticates(cfg):
            print("Setup complete (existing browser session is valid). Running doctor...")
            do_doctor(name)
            return

        if got_browser_cookie:
            print("Existing browser cookie did not authenticate (401) — logging in fresh...")
        else:
            print("No usable browser session found — logging in fresh...")

        if not can_use_headed_browser():
            raise SystemExit(
                "Browser session is invalid and no display is available for an "
                "interactive login. Either set `api_token` in "
                f"{CONFIG_FILE} (a Grafana service-account token), or run "
                f"`grafana-hs-mcp setup --name {name}` on a machine with a browser."
            )

        ensure_playwright_chromium(interactive=True)
        print(f"Opening a browser to log in (profile: {cfg.profile_dir})")
        setup_profile(cfg.grafana_url, cfg.profile_dir, headless=args.headless, session_file=session_file)

    print(f"Setup complete for instance '{name}'. Running doctor...")
    do_doctor(name)


def _saved_cookie_authenticates(cfg) -> bool:
    """True only if the just-saved browser cookie actually authenticates."""
    try:
        cookie = load_saved_cookie(session_file=session_file_for(cfg.name))
        if not cookie:
            return False
        am = AuthManager(cfg)
        am.seed_session_cookies(cookie)
        return am._session_is_valid()
    except Exception:
        return False


def _do_doctor_one(name: str) -> bool:
    """Run doctor for a single named instance. Returns True on success."""
    try:
        cfg = load_config(name)
    except RuntimeError as exc:
        print(f"  [{name}] ERROR: {exc}")
        return False
    try:
        _assert_grafana_reachable(cfg.grafana_url)
        client = GrafanaClient(cfg)
        health = client.health_check()
        datasources = client.list_datasources()
        client.stop_heartbeat()
        print(f"  [{name}] {cfg.grafana_url}  health={health.get('database','?')}  datasources={len(datasources)}")
        for ds in datasources[:5]:
            print(f"           - {ds.get('name')} ({ds.get('type')}) uid={ds.get('uid')}")
        return True
    except Exception as exc:
        print(f"  [{name}] FAILED: {exc}")
        return False


def do_doctor(name: str | None = None) -> None:
    if name:
        ok = _do_doctor_one(name)
        if not ok:
            raise SystemExit(1)
        return

    instances = load_all_instances()
    if not instances:
        raise SystemExit(
            "No Grafana instances configured. Run `grafana-hs-mcp setup` first."
        )
    print(f"Checking {len(instances)} instance(s)...")
    failed = [n for n in instances if not _do_doctor_one(n)]
    if failed:
        print(f"\n{len(failed)} instance(s) failed: {failed}")
        raise SystemExit(1)


def do_instances() -> None:
    instances = load_all_instances()
    if not instances:
        print("No Grafana instances configured. Run `grafana-hs-mcp setup` first.")
        return
    print(f"{'NAME':<16} {'URL'}")
    print("-" * 60)
    for name, cfg in instances.items():
        print(f"{name:<16} {cfg.grafana_url}")


def do_update() -> None:
    print("Updating grafana-hs-mcp...")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
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


def do_configure_claude_code() -> None:
    if not shutil.which("claude"):
        raise SystemExit(
            "Claude Code CLI not found. Install Claude Code first, then rerun this command."
        )

    subprocess.run(
        ["claude", "mcp", "remove", "--scope", "user", "grafana"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    subprocess.run(
        ["claude", "mcp", "add", "--scope", "user", "grafana", "--", "grafana-hs-mcp"],
        check=True,
    )
    print("Updated Claude Code MCP config")
    print("Restart Claude Code for the MCP server to load.")


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
    if shutil.which("claude"):
        do_configure_claude_code()
        print()
    else:
        print("Skipped Claude Code config: claude CLI not found.")
        print()
    do_configure_cursor()
    print()
    do_configure_codex()
    print()
    print("Done. Restart the AI client you want to use.")


def _assert_grafana_reachable(grafana_url: str) -> None:
    url = grafana_url.rstrip("/")
    try:
        requests.get(url, timeout=8, allow_redirects=False)
    except requests.RequestException as exc:
        print()
        print("Grafana is not reachable.")
        print("Turn on VPN and try again.")
        print(f"URL: {url}")
        print(f"Error: {exc}")
        raise SystemExit(1) from exc


def do_env(args) -> None:
    config_exists = CONFIG_FILE.exists()
    instances = load_all_instances() if config_exists or os.getenv("GRAFANA_URL") else {}

    _print_env(instances, config_exists, args.show_secrets)

    if args.interactive:
        print()
        answer = input("Update saved config values? [y/N]: ").strip().lower()
        if answer in {"y", "yes"}:
            cfg = _prompt_config(next(iter(instances.values()), None))
            save_instance(cfg.name, cfg)
            config_exists = True
            print(f"Saved config: {CONFIG_FILE}")
            print()
            _print_env({cfg.name: cfg}, config_exists, args.show_secrets)

        print()
        answer = input("Run doctor now? [y/N]: ").strip().lower()
        if answer in {"y", "yes"}:
            do_doctor()


def _print_env(instances: dict, config_exists: bool, show_secrets: bool) -> None:
    print("Grafana HS MCP environment")
    print("=" * 28)
    print(f"Config file : {CONFIG_FILE}  ({'exists' if config_exists else 'missing'})")
    print(f"App dir     : {APP_DIR}")
    home_env = os.getenv("GRAFANA_HS_MCP_HOME", "")
    if home_env:
        print(f"GRAFANA_HS_MCP_HOME : {home_env}  (env)")
    print()

    if instances:
        print(f"Configured instances ({len(instances)}):")
        for name, cfg in instances.items():
            sf = session_file_for(name)
            print(f"  {name}")
            print(f"    URL        : {cfg.grafana_url}")
            print(f"    Loki UID   : {cfg.loki_datasource_uid}")
            if cfg.clickhouse_datasource_uid:
                print(f"    CH UID     : {cfg.clickhouse_datasource_uid}")
            print(f"    Profile    : {cfg.profile_dir}  ({'exists' if cfg.profile_dir.exists() else 'missing'})")
            print(f"    Session    : {sf}  ({'exists' if sf.exists() else 'missing'})")
    else:
        print("  No instances configured. Run `grafana-hs-mcp setup` first.")

    print()
    env_token = os.getenv("GRAFANA_API_TOKEN")
    if env_token:
        print(f"GRAFANA_API_TOKEN : {_secret(env_token, show_secrets)}  (env)")
    for disp_var in ("DISPLAY", "WAYLAND_DISPLAY"):
        val = os.getenv(disp_var, "")
        if val:
            print(f"{disp_var} : {val}  (env)")

    print()
    print("Useful commands:")
    print("  grafana-hs-mcp setup [--name <n>]   # add or reconfigure an instance")
    print("  grafana-hs-mcp instances             # list all instances")
    print("  grafana-hs-mcp doctor [--name <n>]  # check connectivity")
    print("  grafana-hs-mcp configure-all         # register with all AI clients")
    print("  grafana-hs-mcp update")
    print("  grafana-hs-mcp cleanup")
    print("  grafana-hs-mcp run")


def _prompt_config(cfg: Config | None) -> Config:
    current_url = cfg.grafana_url if cfg else os.getenv("GRAFANA_URL", "")
    current_loki_uid = cfg.loki_datasource_uid if cfg else "loki"
    current_clickhouse_uid = cfg.clickhouse_datasource_uid if cfg else ""
    current_profile_dir = cfg.profile_dir if cfg else PROFILE_DIR

    print()
    print("Update config values. Press Enter to keep the current value.")

    url_prompt = f"Grafana URL [{current_url}]: " if current_url else "Grafana URL: "
    grafana_url = input(url_prompt).strip() or current_url
    if not grafana_url:
        raise SystemExit(
            "Grafana URL is required. Example: https://grafana.example.com"
        )
    loki_uid = (
        input(f"Loki datasource UID [{current_loki_uid}]: ").strip() or current_loki_uid
    )
    clickhouse_prompt = (
        f"ClickHouse datasource UID [{current_clickhouse_uid}]: "
        if current_clickhouse_uid else
        "ClickHouse datasource UID [optional]: "
    )
    clickhouse_uid_raw = input(clickhouse_prompt).strip()
    clickhouse_uid = clickhouse_uid_raw or current_clickhouse_uid or None
    profile_dir_raw = input(f"Playwright profile dir [{current_profile_dir}]: ").strip()
    profile_dir = (
        Path(profile_dir_raw).expanduser() if profile_dir_raw else current_profile_dir
    )

    print()
    print(
        "Note: GRAFANA_API_TOKEN is not saved here. Set it as an environment variable if needed."
    )

    return Config(
        grafana_url=grafana_url.rstrip("/"),
        loki_datasource_uid=loki_uid,
        clickhouse_datasource_uid=clickhouse_uid,
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
