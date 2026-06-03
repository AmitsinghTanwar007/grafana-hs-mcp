from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path
from typing import Optional

import requests

from .config import Config, PROFILE_DIR, ensure_app_dir


logger = logging.getLogger(__name__)

NAV_TIMEOUT_MS = 60_000
SETTLE_TIMEOUT_MS = 3_000


def find_system_chrome() -> Path | None:
    env_path = os.getenv("GRAFANA_HS_MCP_CHROME")
    if env_path and Path(env_path).exists():
        return Path(env_path)

    candidates: list[str | Path] = []
    if sys.platform == "darwin":
        candidates.extend(
            [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Chromium.app/Contents/MacOS/Chromium",
                Path.home()
                / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            ]
        )
    elif sys.platform.startswith("linux"):
        candidates.extend(
            ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]
        )
    elif sys.platform.startswith("win"):
        local_appdata = os.getenv("LOCALAPPDATA", "")
        program_files = os.getenv("PROGRAMFILES", "")
        candidates.extend(
            [
                Path(program_files) / "Google/Chrome/Application/chrome.exe",
                Path(local_appdata) / "Google/Chrome/Application/chrome.exe",
            ]
        )

    for candidate in candidates:
        path = (
            shutil.which(str(candidate))
            if not str(candidate).startswith("/")
            else str(candidate)
        )
        if path and Path(path).exists():
            return Path(path)
    return None


def ensure_playwright_chromium(interactive: bool = True) -> None:
    """
    Install Playwright's Chromium browser if needed.

    Python package dependencies are installed by pip/uv before this command can
    run, but Playwright's browser binary is a separate download. Keep this in
    setup so users do not need to remember `playwright install chromium`.
    """
    if os.getenv("GRAFANA_HS_MCP_SKIP_BROWSER_INSTALL"):
        logger.info(
            "Skipping Playwright browser install due to GRAFANA_HS_MCP_SKIP_BROWSER_INSTALL"
        )
        return

    if find_system_chrome():
        logger.info(
            "Using system Chrome/Chromium; Playwright browser download not needed"
        )
        return

    if interactive:
        answer = (
            input(
                "No system Chrome/Chromium found. Download Playwright Chromium (~300-450 MB)? [Y/n]: "
            )
            .strip()
            .lower()
        )
        if answer in {"n", "no"}:
            raise RuntimeError(
                "No browser available. Install Google Chrome/Chromium or rerun setup and allow the download."
            )

    logger.info("Ensuring Playwright Chromium browser is installed")
    subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        check=True,
    )


class AuthManager:
    def __init__(self, config: Config):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        if config.api_token:
            self.session.headers.update({"Authorization": f"Bearer {config.api_token}"})

    def ensure_authenticated(self) -> requests.Session:
        if self.config.api_token:
            return self.session

        cookie = self.get_fresh_cookie(headless=True)
        if not cookie:
            raise RuntimeError(
                "Could not get Grafana cookie from Playwright profile. "
                "Run `grafana-hs-mcp setup` again."
            )
        self.seed_session_cookies(cookie)
        return self.session

    def refresh_after_401(self) -> bool:
        if self.config.api_token:
            return False
        cookie = self.get_fresh_cookie(headless=True)
        if not cookie:
            return False
        self.seed_session_cookies(cookie)
        return True

    def seed_session_cookies(self, cookie_str: str) -> None:
        domain = urllib.parse.urlparse(self.config.grafana_url).hostname or ""
        for part in cookie_str.split(";"):
            part = part.strip()
            if "=" not in part:
                continue
            name, _, value = part.partition("=")
            self.session.cookies.set(name.strip(), value.strip(), domain=domain)
        logger.debug(
            "Seeded Grafana session with %d cookies", len(self.session.cookies)
        )

    def get_fresh_cookie(self, headless: bool = True) -> Optional[str]:
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is not installed. Run `pip install playwright` "
                "and `playwright install chromium`."
            ) from exc

        profile_dir = self.config.profile_dir
        if not profile_dir.exists():
            logger.error("Profile does not exist: %s", profile_dir)
            return None

        with sync_playwright() as pw:
            launch_kwargs = _launch_kwargs(profile_dir, headless)
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                **launch_kwargs,
            )
            page = context.new_page()
            try:
                page.goto(
                    self.config.grafana_url,
                    timeout=NAV_TIMEOUT_MS,
                    wait_until="domcontentloaded",
                )
                page.wait_for_timeout(2_000)

                if "/login" in page.url:
                    google_btn = page.locator(
                        "a[href*='google'], "
                        "button:has-text('Google'), "
                        "a:has-text('Sign in with Google')"
                    )
                    if google_btn.count() > 0:
                        google_btn.first.click()
                        page.wait_for_timeout(3_000)

                if "accounts.google.com" in page.url:
                    try:
                        page.wait_for_url(
                            lambda url: self.config.grafana_url in url,
                            timeout=NAV_TIMEOUT_MS,
                        )
                    except PlaywrightTimeoutError:
                        self._save_debug_screenshot(page)
                        logger.error(
                            "Google session expired. Re-run `grafana-hs-mcp setup`."
                        )
                        return None

                page.wait_for_timeout(SETTLE_TIMEOUT_MS)
                cookies = context.cookies()
                session = next(
                    (c for c in cookies if c["name"] == "grafana_session"), None
                )
                expiry = next(
                    (c for c in cookies if c["name"] == "grafana_session_expiry"), None
                )
                if not session:
                    self._save_debug_screenshot(page)
                    logger.error(
                        "grafana_session cookie not found. Current URL: %s", page.url
                    )
                    return None

                cookie = f"grafana_session={session['value']}"
                if expiry:
                    cookie += f"; grafana_session_expiry={expiry['value']}"
                return cookie
            finally:
                context.close()

    @staticmethod
    def _save_debug_screenshot(page) -> None:
        try:
            path = "/tmp/grafana_hs_mcp_debug.png"
            page.screenshot(path=path)
            logger.info("Debug screenshot saved: %s", path)
        except Exception:
            pass


def setup_profile(
    grafana_url: str, profile_dir: Path = PROFILE_DIR, headless: bool = False
) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Run `pip install playwright` "
            "and `playwright install chromium`."
        ) from exc

    ensure_app_dir()
    profile_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        launch_kwargs = _launch_kwargs(profile_dir, headless)
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            **launch_kwargs,
        )
        page = context.new_page()
        page.goto(
            grafana_url.rstrip("/"),
            timeout=NAV_TIMEOUT_MS,
            wait_until="domcontentloaded",
        )

        print("\nComplete Grafana Google SSO login in the browser window.")
        print("After Grafana dashboard loads, return here and press Enter.\n")
        input("Press Enter after login is complete: ")

        cookies = context.cookies()
        has_session = any(c["name"] == "grafana_session" for c in cookies)
        context.close()

    if not has_session:
        raise RuntimeError(
            "Grafana session cookie not found. Login may not have completed."
        )


def can_use_headed_browser() -> bool:
    if sys.platform == "darwin" or sys.platform.startswith("win"):
        return True
    return bool(os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY"))


def _launch_kwargs(profile_dir: Path, headless: bool) -> dict:
    chrome_path = find_system_chrome()
    args = [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--password-store=basic",
        "--use-mock-keychain",
    ]
    kwargs = {"headless": headless, "args": args}
    if chrome_path:
        kwargs["executable_path"] = str(chrome_path)
        logger.info("Using browser: %s", chrome_path)
    return kwargs
