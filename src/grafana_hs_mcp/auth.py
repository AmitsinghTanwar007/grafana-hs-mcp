from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Optional

import requests

from .config import Config, PROFILE_DIR, SESSION_FILE, ensure_app_dir


logger = logging.getLogger(__name__)

NAV_TIMEOUT_MS = 60_000
SETTLE_TIMEOUT_MS = 3_000


def load_saved_cookie() -> str | None:
    if not SESSION_FILE.exists():
        return None
    try:
        import json

        data = json.loads(SESSION_FILE.read_text())
    except Exception as exc:
        logger.warning("Could not read saved Grafana session: %s", exc)
        return None

    expires = data.get("expires")
    if expires and expires <= int(time.time()):
        logger.info("Saved Grafana session expired")
        return None
    return data.get("cookie")


def save_cookie(
    cookie: str, source: str = "unknown", expires: int | None = None
) -> None:
    import json

    ensure_app_dir()
    if expires is not None and expires <= 0:
        expires = None
    SESSION_FILE.write_text(
        json.dumps(
            {
                "cookie": cookie,
                "source": source,
                "expires": expires,
                "saved_at": int(time.time()),
            },
            indent=2,
        )
        + "\n"
    )
    SESSION_FILE.chmod(0o600)


def extract_cookie_from_browsers(
    grafana_url: str,
) -> tuple[str, str, int | None] | None:
    """Try to read an existing Grafana session from the user's real browsers."""
    try:
        import browser_cookie3
    except ImportError:
        logger.info("browser-cookie3 is not installed; skipping browser cookie import")
        return None

    hostname = urllib.parse.urlparse(grafana_url).hostname
    if not hostname:
        return None

    loaders = [
        ("Chrome", getattr(browser_cookie3, "chrome", None)),
        ("Chromium", getattr(browser_cookie3, "chromium", None)),
        ("Brave", getattr(browser_cookie3, "brave", None)),
        ("Edge", getattr(browser_cookie3, "edge", None)),
        ("Firefox", getattr(browser_cookie3, "firefox", None)),
        ("Vivaldi", getattr(browser_cookie3, "vivaldi", None)),
        ("Opera", getattr(browser_cookie3, "opera", None)),
        ("Safari", getattr(browser_cookie3, "safari", None)),
    ]

    for browser_name, loader in loaders:
        if loader is None:
            continue
        try:
            jar = loader(domain_name=hostname)
        except Exception as exc:
            logger.debug("Could not read %s cookies: %s", browser_name, exc)
            continue

        cookies = list(jar)
        session = next((c for c in cookies if c.name == "grafana_session"), None)
        if not session:
            continue
        if session.expires and session.expires <= int(time.time()):
            logger.debug("%s Grafana session cookie is expired", browser_name)
            continue

        parts = [f"grafana_session={session.value}"]
        expiry = next((c for c in cookies if c.name == "grafana_session_expiry"), None)
        if expiry:
            parts.append(f"grafana_session_expiry={expiry.value}")
        return "; ".join(parts), browser_name, session.expires

    return None


def setup_cookie_from_existing_browser(grafana_url: str) -> bool:
    """Use the user's normal browser session instead of creating a new profile."""
    extracted = extract_cookie_from_browsers(grafana_url)
    if extracted:
        cookie, browser_name, expires = extracted
        save_cookie(cookie, source=browser_name, expires=expires)
        print(
            f"Found existing Grafana session in {browser_name}; saved session cookie."
        )
        return True

    print("No existing Grafana session cookie found in your installed browsers.")
    answer = (
        input(
            "Open Grafana in your default browser, log in there, then retry cookie import? [Y/n]: "
        )
        .strip()
        .lower()
    )
    if answer in {"n", "no"}:
        return False

    webbrowser.open(grafana_url)
    input("After Grafana loads in your normal browser, press Enter to continue: ")
    extracted = extract_cookie_from_browsers(grafana_url)
    if extracted:
        cookie, browser_name, expires = extracted
        save_cookie(cookie, source=browser_name, expires=expires)
        print(f"Imported Grafana session from {browser_name}; saved session cookie.")
        return True

    print(
        "Could not import a Grafana session from your browser. Falling back to isolated Playwright profile."
    )
    return False


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

    def _session_is_valid(self) -> bool:
        """Probe an *authenticated* endpoint.

        `/api/health` is public, so a seeded cookie can look fine there yet be
        rejected on real calls. `/api/user` requires a valid session, so it tells
        us whether the cookie actually authenticates (Grafana rotates
        `grafana_session` server-side, so a cookie copied from the browser is
        frequently already stale).
        """
        try:
            url = self.config.grafana_url.rstrip("/") + "/api/user"
            resp = self.session.get(url, timeout=15)
            return resp.status_code == 200
        except requests.RequestException:
            return False

    def _try_cookie(
        self, cookie: str, source: str, expires: Optional[int] = None
    ) -> bool:
        """Seed a cookie, validate it against an authenticated endpoint, and only
        persist it if it really authenticates. Returns True on success."""
        self.seed_session_cookies(cookie)
        if self._session_is_valid():
            save_cookie(cookie, source=source, expires=expires)
            logger.info("Authenticated to Grafana via %s cookie", source)
            return True
        logger.warning("Cookie from %s did not authenticate (401); will escalate", source)
        return False

    def ensure_authenticated(self) -> requests.Session:
        if self.config.api_token:
            return self.session

        # 1. Previously-saved cookie (only trust it if it still authenticates).
        cookie = load_saved_cookie()
        if cookie and self._try_cookie(cookie, source="saved"):
            return self.session

        # 2. Live cookie from the user's browser.
        extracted = extract_cookie_from_browsers(self.config.grafana_url)
        if extracted:
            cookie, browser_name, expires = extracted
            if self._try_cookie(cookie, source=browser_name, expires=expires):
                return self.session

        # 3. Browser cookie missing or stale -> real fresh login via Playwright SSO.
        cookie = self.get_fresh_cookie(headless=True)
        if cookie and self._try_cookie(cookie, source="playwright-profile"):
            return self.session

        raise RuntimeError(
            "Could not obtain a valid Grafana session from a saved cookie, your "
            "browser, or the Playwright profile. Log in to Grafana in your browser "
            "(or run `grafana-hs-mcp setup`) and try again."
        )

    def refresh_after_401(self) -> bool:
        """Recover from a 401/403 on an authenticated request.

        The current `grafana_session` is stale (Grafana rotates it server-side).
        Re-read the browser in case it rotated to a fresh cookie, but ONLY trust a
        cookie that actually validates; otherwise escalate to a real Playwright
        fresh login instead of re-seeding the same dead cookie (which is what made
        this loop forever).
        """
        if self.config.api_token:
            return False

        # 1. Browser may now hold a freshly-rotated cookie — validate before trusting.
        extracted = extract_cookie_from_browsers(self.config.grafana_url)
        if extracted:
            cookie, browser_name, expires = extracted
            if self._try_cookie(cookie, source=browser_name, expires=expires):
                return True

        # 2. Escalate: real fresh login via the Playwright SSO profile.
        cookie = self.get_fresh_cookie(headless=True)
        if cookie and self._try_cookie(cookie, source="playwright-profile"):
            return True
        return False

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
        session = next((c for c in cookies if c["name"] == "grafana_session"), None)
        expiry = next(
            (c for c in cookies if c["name"] == "grafana_session_expiry"), None
        )
        context.close()

    if not session:
        raise RuntimeError(
            "Grafana session cookie not found. Login may not have completed."
        )

    cookie = f"grafana_session={session['value']}"
    if expiry:
        cookie += f"; grafana_session_expiry={expiry['value']}"
    save_cookie(cookie, source="playwright-profile", expires=session.get("expires"))


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
