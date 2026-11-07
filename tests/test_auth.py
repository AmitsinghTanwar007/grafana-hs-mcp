import io
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from grafana_hs_mcp import auth
from grafana_hs_mcp.config import Config


GRAFANA_URL = "https://grafana.example.com"


def cookie_jar(value: str, expires: int | None = None):
    return [
        SimpleNamespace(name="grafana_session", value=value, expires=expires),
        SimpleNamespace(name="grafana_session_expiry", value="12345", expires=expires),
    ]


class BrowserCookieDiscoveryTest(unittest.TestCase):
    def test_read_errors_are_visible_and_other_browsers_are_tried(self):
        chrome = Mock(side_effect=RuntimeError("Unable to get key for cookie decryption"))
        future = int(time.time()) + 3600
        firefox = Mock(return_value=cookie_jar("firefox-session", future))

        with (
            patch.object(
                auth,
                "_browser_loaders",
                return_value=[("Chrome", chrome), ("Firefox", firefox)],
            ),
            patch.object(auth, "_chromium_cookie_files", return_value=[]),
            self.assertLogs(auth.logger, level="WARNING") as logs,
        ):
            candidates = auth.extract_cookie_candidates_from_browsers(GRAFANA_URL)

        self.assertEqual(
            candidates,
            [
                (
                    "grafana_session=firefox-session; grafana_session_expiry=12345",
                    "Firefox",
                    future,
                )
            ],
        )
        self.assertIn("Unable to get key for cookie decryption", "\n".join(logs.output))
        firefox.assert_called_once_with(domain_name="grafana.example.com")

    def test_explicit_profile_paths_are_loaded_and_duplicate_sessions_removed(self):
        default_db = Path("/fake/Default/Network/Cookies")
        profile_db = Path("/fake/Profile 1/Network/Cookies")
        loader = Mock(return_value=cookie_jar("same-session"))

        with (
            patch.object(auth, "_browser_loaders", return_value=[("Chrome", loader)]),
            patch.object(
                auth, "_chromium_cookie_files", return_value=[default_db, profile_db]
            ),
        ):
            candidates = auth.extract_cookie_candidates_from_browsers(GRAFANA_URL)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0][1], "Chrome")
        self.assertEqual(
            loader.call_args_list,
            [
                call(domain_name="grafana.example.com"),
                call(domain_name="grafana.example.com", cookie_file=str(default_db)),
                call(domain_name="grafana.example.com", cookie_file=str(profile_db)),
            ],
        )

    def test_discovers_modern_and_legacy_databases_in_all_profiles(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in (
                "Default/Cookies",
                "Default/Network/Cookies",
                "Profile 1/Network/Cookies",
                "Profile 2/Cookies",
            ):
                cookie_file = root / relative_path
                cookie_file.parent.mkdir(parents=True, exist_ok=True)
                cookie_file.touch()
            (root / "Profile 3").mkdir()

            with patch.object(auth, "_chromium_user_data_dirs", return_value=(root,)):
                cookie_files = auth._chromium_cookie_files("Chrome")

        self.assertEqual(
            cookie_files,
            [
                root / "Default/Network/Cookies",
                root / "Default/Cookies",
                root / "Profile 1/Network/Cookies",
                root / "Profile 2/Cookies",
            ],
        )

    def test_expired_sessions_are_not_returned(self):
        expired = int(time.time()) - 1
        loader = Mock(return_value=cookie_jar("expired", expired))

        with (
            patch.object(auth, "_browser_loaders", return_value=[("Chrome", loader)]),
            patch.object(auth, "_chromium_cookie_files", return_value=[]),
        ):
            self.assertEqual(auth.extract_cookie_candidates_from_browsers(GRAFANA_URL), [])


class BrowserCookieSetupTest(unittest.TestCase):
    def test_setup_validates_each_candidate_and_saves_the_valid_one(self):
        stale = ("grafana_session=stale", "Chrome", None)
        fresh = ("grafana_session=fresh", "Firefox", None)
        validator = Mock(side_effect=[False, True])

        with (
            patch.object(auth, "extract_cookie_candidates_from_browsers", return_value=[stale, fresh]),
            patch.object(auth, "save_cookie") as save_cookie,
        ):
            imported = auth.setup_cookie_from_existing_browser(
                GRAFANA_URL, cookie_validator=validator
            )

        self.assertTrue(imported)
        self.assertEqual(validator.call_args_list, [call(stale[0]), call(fresh[0])])
        save_cookie.assert_called_once_with(
            fresh[0], source="Firefox", expires=None, session_file=None
        )

    def test_setup_retries_while_browser_flushes_new_session(self):
        fresh = ("grafana_session=fresh", "Chrome", None)

        with (
            patch.object(
                auth,
                "extract_cookie_candidates_from_browsers",
                side_effect=[[], [], [fresh]],
            ) as extract,
            patch("builtins.input", side_effect=["", ""]),
            patch.object(auth.webbrowser, "open"),
            patch.object(auth.time, "sleep") as sleep,
            patch.object(auth, "save_cookie") as save_cookie,
            redirect_stdout(io.StringIO()) as output,
        ):
            imported = auth.setup_cookie_from_existing_browser(GRAFANA_URL)

        self.assertTrue(imported)
        self.assertEqual(extract.call_count, 3)
        sleep.assert_called_once_with(2)
        self.assertIn("Waiting 2s", output.getvalue())
        save_cookie.assert_called_once_with(
            fresh[0], source="Chrome", expires=None, session_file=None
        )


class AuthManagerBrowserCookieTest(unittest.TestCase):
    def test_stale_browser_cookie_does_not_hide_valid_cookie(self):
        manager = auth.AuthManager(
            Config(grafana_url=GRAFANA_URL, profile_dir=Path("/unused"))
        )
        stale = ("grafana_session=stale", "Chrome", None)
        fresh = ("grafana_session=fresh", "Firefox", 12345)
        manager._session_is_valid = Mock(side_effect=[False, True])
        manager.get_fresh_cookie = Mock()

        with (
            patch.object(auth, "load_saved_cookie", return_value=None),
            patch.object(
                auth,
                "extract_cookie_candidates_from_browsers",
                return_value=[stale, fresh],
            ),
            patch.object(auth, "save_cookie") as save_cookie,
        ):
            session = manager.ensure_authenticated()

        self.assertIs(session, manager.session)
        self.assertEqual(manager._session_is_valid.call_count, 2)
        manager.get_fresh_cookie.assert_not_called()
        save_cookie.assert_called_once_with(
            fresh[0],
            source="Firefox",
            expires=12345,
            session_file=manager._session_file,
        )

    def test_failed_validation_clears_cookie_before_next_attempt(self):
        manager = auth.AuthManager(Config(grafana_url=GRAFANA_URL))
        manager._session_is_valid = Mock(return_value=False)

        manager.cookie_is_valid("grafana_session=first")
        manager.session.cookies.set("unrelated", "value", domain="grafana.example.com")
        manager.cookie_is_valid("grafana_session=second")

        values = {
            cookie.name: cookie.value for cookie in manager.session.cookies
        }
        self.assertEqual(values, {"grafana_session": "second"})


if __name__ == "__main__":
    unittest.main()
