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
from typing import Callable, Optional

import requests

from .config import Config, PROFILE_DIR, SESSION_FILE, ensure_app_dir, session_file_for


logger = logging.getLogger(__name__)

NAV_TIMEOUT_MS = 60_000
SETTLE_TIMEOUT_MS = 3_000


def load_saved_cookie(session_file: Path | None = None) -> str | None:
    sf = session_file or SESSION_FILE
    if not sf.exists():
        return None
    try:
        import json

        data = json.loads(sf.read_text())
    except Exception as exc:
        logger.warning("Could not read saved Grafana session: %s", exc)
        return None

    expires = data.get("expires")
    if expires and expires <= int(time.time()):
        logger.info("Saved Grafana session expired")
        return None
    return data.get("cookie")


def save_cookie(
    cookie: str,
    source: str = "unknown",
    expires: int | None = None,
    session_file: Path | None = None,
) -> None:
    import json

    ensure_app_dir()
    sf = session_file or SESSION_FILE
    sf.parent.mkdir(parents=True, exist_ok=True)
    if expires is not None and expires <= 0:
        expires = None
    sf.write_text(
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
    sf.chmod(0o600)


CookieCandidate = tuple[str, str, int | None]
BROWSER_COOKIE_RETRY_DELAYS = (0, 2, 5, 10)


def _browser_loaders() -> list[tuple[str, Callable]]:
    import browser_cookie3

    return [
        ("Chrome", browser_cookie3.chrome),
        ("Chromium", browser_cookie3.chromium),
        ("Brave", browser_cookie3.brave),
        ("Edge", browser_cookie3.edge),
        ("Firefox", browser_cookie3.firefox),
        ("Vivaldi", browser_cookie3.vivaldi),
        ("Opera", browser_cookie3.opera),
        ("Safari", browser_cookie3.safari),
    ]


def _chromium_user_data_dirs(browser_name: str) -> tuple[Path, ...]:
    """Return profile roots whose cookie DBs browser-cookie3 may not enumerate."""
    home = Path.home()
    if sys.platform == "darwin":
        app_support = home / "Library/Application Support"
        roots = {
            "Chrome": tuple(
                app_support / "Google" / name
                for name in ("Chrome", "Chrome Beta", "Chrome Dev")
            ),
            "Chromium": (app_support / "Chromium",),
            "Brave": tuple(
                app_support / "BraveSoftware" / f"Brave-Browser{suffix}"
                for suffix in ("", "-Beta", "-Dev", "-Nightly")
            ),
            "Edge": tuple(
                app_support / f"Microsoft Edge{suffix}"
                for suffix in ("", " Beta", " Dev", " Canary")
            ),
            "Vivaldi": (app_support / "Vivaldi",),
        }
        return roots.get(browser_name, ())

    if sys.platform.startswith("linux"):
        config = Path(os.getenv("XDG_CONFIG_HOME") or home / ".config")
        roots = {
            "Chrome": (
                config / "google-chrome",
                config / "google-chrome-beta",
                config / "google-chrome-unstable",
                home / ".var/app/com.google.Chrome/config/google-chrome",
            ),
            "Chromium": (
                config / "chromium",
                home / ".var/app/org.chromium.Chromium/config/chromium",
            ),
            "Brave": tuple(
                config / "BraveSoftware" / f"Brave-Browser{suffix}"
                for suffix in ("", "-Beta", "-Dev", "-Nightly")
            )
            + (home / ".var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser",),
            "Edge": (
                config / "microsoft-edge",
                config / "microsoft-edge-beta",
                config / "microsoft-edge-dev",
                home / ".var/app/com.microsoft.Edge/config/microsoft-edge",
            ),
            "Vivaldi": (
                config / "vivaldi",
                config / "vivaldi-snapshot",
                home / ".var/app/com.vivaldi.Vivaldi/config/vivaldi",
            ),
        }
        return roots.get(browser_name, ())

    if sys.platform == "win32":
        local = Path(os.getenv("LOCALAPPDATA") or home / "AppData/Local")
        roots = {
            "Chrome": tuple(
                local / "Google" / name / "User Data"
                for name in ("Chrome", "Chrome Beta", "Chrome Dev")
            ),
            "Chromium": (local / "Chromium/User Data",),
            "Brave": tuple(
                local / "BraveSoftware" / f"Brave-Browser{suffix}" / "User Data"
                for suffix in ("", "-Beta", "-Dev", "-Nightly")
            ),
            "Edge": tuple(
                local / "Microsoft" / f"Edge{suffix}" / "User Data"
                for suffix in ("", " Beta", " Dev", " SxS")
            ),
            "Vivaldi": (local / "Vivaldi/User Data",),
        }
        return roots.get(browser_name, ())

    return ()


def _chromium_cookie_files(browser_name: str) -> list[Path]:
    """Find modern and legacy cookie DBs in every discovered browser profile."""
    cookie_files: list[Path] = []
    for user_data_dir in _chromium_user_data_dirs(browser_name):
        profiles = [user_data_dir / "Default"]
        profiles.extend(sorted(user_data_dir.glob("Profile *")))
        for profile in profiles:
            if not profile.is_dir():
                continue
            for relative_path in ("Network/Cookies", "Cookies"):
                cookie_file = profile / relative_path
                if cookie_file.is_file():
                    cookie_files.append(cookie_file)
    return cookie_files


def _cookie_from_jar(jar, browser_name: str) -> tuple[str, int | None] | None:
    cookies = list(jar)
    session = next((c for c in cookies if c.name == "grafana_session"), None)
    if not session:
        return None
    if session.expires and session.expires <= int(time.time()):
        logger.info("%s Grafana session cookie is expired", browser_name)
        return None

    parts = [f"grafana_session={session.value}"]
    expiry = next((c for c in cookies if c.name == "grafana_session_expiry"), None)
    if expiry:
        parts.append(f"grafana_session_expiry={expiry.value}")
    return "; ".join(parts), session.expires


def extract_cookie_candidates_from_browsers(grafana_url: str) -> list[CookieCandidate]:
    """Read every discoverable Grafana session without hiding access errors."""
    try:
        loaders = _browser_loaders()
    except ImportError:
        logger.warning("browser-cookie3 is not installed; skipping browser cookie import")
        return []

    hostname = urllib.parse.urlparse(grafana_url).hostname
    if not hostname:
        return []

    candidates: list[CookieCandidate] = []
    seen_cookies: set[str] = set()
    for browser_name, loader in loaders:
        # The default call retains browser-cookie3's platform support. Explicit
        # paths cover modern Network/Cookies stores and non-default profiles.
        sources: list[Path | None] = [None]
        sources.extend(_chromium_cookie_files(browser_name))
        for cookie_file in sources:
            kwargs = {"domain_name": hostname}
            if cookie_file is not None:
                kwargs["cookie_file"] = str(cookie_file)
            try:
                jar = loader(**kwargs)
                extracted = _cookie_from_jar(jar, browser_name)
            except Exception as exc:
                message = str(exc)
                missing_browser = cookie_file is None and any(
                    text in message
                    for text in (
                        "Failed to find cookies for",
                        "Failed to find Firefox cookie file",
                        "Could not find Firefox profile directory",
                        "Cannot find Safari cookie file",
                    )
                )
                logger.log(
                    logging.INFO if missing_browser else logging.WARNING,
                    "Could not read %s cookies from %s (%s): %s",
                    browser_name,
                    cookie_file or "default profile",
                    type(exc).__name__,
                    exc,
                )
                continue
            if not extracted:
                continue
            cookie, expires = extracted
            if cookie in seen_cookies:
                continue
            seen_cookies.add(cookie)
            candidates.append((cookie, browser_name, expires))
    return candidates


def extract_cookie_from_browsers(grafana_url: str) -> CookieCandidate | None:
    """Return the first unexpired browser session for backward compatibility."""
    candidates = extract_cookie_candidates_from_browsers(grafana_url)
    return candidates[0] if candidates else None


def _select_browser_cookie(
    grafana_url: str,
    cookie_validator: Callable[[str], bool] | None = None,
) -> CookieCandidate | None:
    for candidate in extract_cookie_candidates_from_browsers(grafana_url):
        cookie, browser_name, _ = candidate
        if cookie_validator is not None:
            try:
                valid = cookie_validator(cookie)
            except Exception as exc:
                logger.warning(
                    "Could not validate %s Grafana session (%s): %s",
                    browser_name,
                    type(exc).__name__,
                    exc,
                )
                continue
            if not valid:
                logger.info(
                    "%s Grafana session did not authenticate; trying other browsers",
                    browser_name,
                )
                continue
        return candidate
    return None


def setup_cookie_from_existing_browser(
    grafana_url: str,
    session_file: Path | None = None,
    cookie_validator: Callable[[str], bool] | None = None,
) -> bool:
    """Import and optionally validate a normal-browser session, with brief retries."""
    extracted = _select_browser_cookie(grafana_url, cookie_validator)
    if extracted:
        cookie, browser_name, expires = extracted
        save_cookie(cookie, source=browser_name, expires=expires, session_file=session_file)
        print(
            f"Found existing Grafana session in {browser_name}; saved session cookie."
        )
        return True

    print("No valid existing Grafana session found in your installed browsers.")
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
    for delay in BROWSER_COOKIE_RETRY_DELAYS:
        if delay:
            print(f"Waiting {delay}s for the browser to save its session...")
            time.sleep(delay)
        extracted = _select_browser_cookie(grafana_url, cookie_validator)
        if extracted:
            cookie, browser_name, expires = extracted
            save_cookie(
                cookie, source=browser_name, expires=expires, session_file=session_file
            )
            print(f"Imported Grafana session from {browser_name}; saved session cookie.")
            return True

    print(
        "Could not import a valid Grafana session from your browser. "
        "Review the browser access errors above; falling back to isolated Playwright profile."
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
        self._session_file = session_file_for(config.name)
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

    def cookie_is_valid(self, cookie: str) -> bool:
        """Validate one candidate without leaking cookies between attempts."""
        self.session.cookies.clear()
        self.seed_session_cookies(cookie)
        return self._session_is_valid()

    def _try_cookie(
        self, cookie: str, source: str, expires: Optional[int] = None
    ) -> bool:
        """Seed a cookie, validate it against an authenticated endpoint, and only
        persist it if it really authenticates. Returns True on success."""
        if self.cookie_is_valid(cookie):
            save_cookie(cookie, source=source, expires=expires, session_file=self._session_file)
            logger.info("Authenticated to Grafana via %s cookie", source)
            return True
        logger.warning("Cookie from %s did not authenticate; will escalate", source)
        return False

    def _try_browser_cookies(self) -> bool:
        for cookie, browser_name, expires in extract_cookie_candidates_from_browsers(
            self.config.grafana_url
        ):
            if self._try_cookie(cookie, source=browser_name, expires=expires):
                return True
        return False

    def ensure_authenticated(self) -> requests.Session:
        if self.config.api_token:
            return self.session

        # 1. Previously-saved cookie (only trust it if it still authenticates).
        cookie = load_saved_cookie(session_file=self._session_file)
        if cookie and self._try_cookie(cookie, source="saved"):
            return self.session

        # 2. Live cookies from the user's browsers; skip stale candidates.
        if self._try_browser_cookies():
            return self.session

        # 3. Browser cookie missing or stale -> real fresh login via Playwright SSO.
        cookie = self.get_fresh_cookie(headless=True)
        if cookie and self._try_cookie(cookie, source="playwright-profile"):
            return self.session

        raise RuntimeError(
            "Could not obtain a valid Grafana session from a saved cookie, your "
            f"browser, or the Playwright profile. Log in to Grafana in your browser "
            f"(or run `grafana-hs-mcp setup --name {self.config.name}`) and try again."
        )

    def refresh_after_401(self) -> bool:
        """Recover from a 401/403 on an authenticated request."""
        if self.config.api_token:
            return False

        # 1. Browsers may now hold a freshly-rotated cookie — validate each one.
        if self._try_browser_cookies():
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
    grafana_url: str,
    profile_dir: Path = PROFILE_DIR,
    headless: bool = False,
    session_file: Path | None = None,
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
    save_cookie(cookie, source="playwright-profile", expires=session.get("expires"), session_file=session_file)


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
