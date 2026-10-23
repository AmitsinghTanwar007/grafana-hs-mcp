import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from grafana_hs_mcp import config
from grafana_hs_mcp.config import VictoriaLogsConfig
from grafana_hs_mcp.victorialogs_client import VictoriaLogsClient


class VictoriaLogsTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = VictoriaLogsClient(VictoriaLogsConfig("https://vl.example", "test"))
        self.session_patch = patch("grafana_hs_mcp.victorialogs_client.requests.Session")
        self.session = self.session_patch.start().return_value.__enter__.return_value
        self.addCleanup(self.session_patch.stop)
        self.response = self.session.post.return_value.__enter__.return_value
        self.response.status_code = 200
        self.response.iter_content.return_value = [b'{"_msg":"hello"}\n']

    def query(self, **kwargs):
        return self.client.query("*", start="2026-10-22T16:00:00Z", end="2026-10-22T17:00:00Z", **kwargs)

    def test_request_and_raw_records(self):
        result = self.query()
        self.assertEqual(result["rows"], [{"_msg": "hello"}])
        self.assertFalse(result["possibly_more"])
        args, kwargs = self.session.post.call_args
        self.assertEqual(args[0], "https://vl.example/select/logsql/query")
        self.assertFalse(kwargs["allow_redirects"])
        self.assertFalse(self.session.trust_env)
        self.assertEqual(kwargs["data"]["start"], "2026-10-22T16:00:00+00:00")
        self.assertEqual(kwargs["timeout"], (10, 50))
        self.session.post.return_value.__exit__.assert_called_once()

    def test_stats_and_split_utf8_records(self):
        raw = json.dumps({"marker": "P", "lines": "2", "message": "café"}, ensure_ascii=False).encode()
        split = raw.index("é".encode()) + 1
        self.response.iter_content.return_value = [raw[:split], raw[split:]]
        self.assertEqual(self.query()["rows"][0]["lines"], "2")

    def test_large_record_is_supported(self):
        self.response.iter_content.return_value = [json.dumps({"_msg": "x" * 40506}).encode() + b'\n']
        self.assertEqual(len(self.query()["rows"][0]["_msg"]), 40506)

    def test_empty_result(self):
        self.response.iter_content.return_value = []
        self.assertEqual(self.query()["count"], 0)

    def test_row_limit_closes_response(self):
        self.response.iter_content.return_value = [b'{"n":1}\n{"n":2}\n']
        result = self.query(limit=1)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["rows"], [{"n": 1}])
        self.session.post.return_value.__exit__.assert_called_once()

    def test_full_server_page_is_possibly_more(self):
        result = self.query(limit=1)
        self.assertTrue(result["possibly_more"])
        self.assertFalse(result["truncated"])

    def test_byte_limit_marks_partial_result(self):
        self.response.iter_content.return_value = [b'{"n":1}\n', b'x' * 30]
        with patch("grafana_hs_mcp.victorialogs_client.MAX_RESPONSE_BYTES", 20):
            result = self.query()
        self.assertEqual(result["rows"], [{"n": 1}])
        self.assertEqual(result["truncation_reason"], "response_byte_limit")

    def test_record_limit(self):
        for data in (b'x' * 100, b'x' * 100 + b'\n'):
            self.response.iter_content.return_value = [data]
            with patch("grafana_hs_mcp.victorialogs_client.MAX_RECORD_BYTES", 80):
                with self.assertRaisesRegex(RuntimeError, "record exceeds"):
                    self.query()

    def test_bad_ndjson_does_not_leak_body(self):
        for data in (b'<html>secret token</html>', b'[]\n', b'\xff\n'):
            self.response.iter_content.return_value = [data]
            with self.assertRaises(RuntimeError) as caught:
                self.query()
            self.assertNotIn("secret", str(caught.exception))

    def test_redirect_auth_and_http_errors(self):
        for status in (302, 401, 403, 500):
            self.response.status_code = status
            with self.assertRaises(RuntimeError):
                self.query()
            self.response.iter_content.assert_not_called()

    def test_request_error_does_not_leak(self):
        import requests
        self.session.post.side_effect = requests.ConnectionError("secret credential")
        with self.assertRaisesRegex(RuntimeError, "request failed") as caught:
            self.query()
        self.assertNotIn("secret", str(caught.exception))

    def test_limits_are_capped(self):
        self.query(limit=9000, timeout_seconds=120)
        data = self.session.post.call_args.kwargs["data"]
        self.assertEqual((data["limit"], data["timeout"]), (5000, "60s"))

    def test_invalid_time_and_empty_query_make_no_request(self):
        for query, start, end in (("", "now-1h", "now"), ("*", "2026-10-23", "2026-10-22")):
            with self.assertRaises(ValueError):
                self.client.query(query, start, end)
        self.session.post.assert_not_called()

    def test_env_credentials_and_conflict(self):
        with patch.dict(os.environ, {"VICTORIALOGS_COOKIE_TEST": "_pomerium=secret"}):
            self.assertEqual(self.client._headers()["Cookie"], "_pomerium=secret")
            with patch.dict(os.environ, {"VICTORIALOGS_API_TOKEN_TEST": "token"}):
                with self.assertRaises(RuntimeError):
                    self.client._headers()
        with patch.dict(os.environ, {"VICTORIALOGS_API_TOKEN_TEST": "token"}):
            self.assertEqual(self.client._headers()["Authorization"], "Bearer token")
        with patch.dict(os.environ, {"VICTORIALOGS_COOKIE_TEST": "x=y\r\nInjected: x"}):
            with self.assertRaises(ValueError):
                self.client._headers()

    def test_cookie_file_refresh_and_expiry(self):
        with tempfile.TemporaryDirectory() as directory:
            cookie_file = Path(directory) / "cookie"
            self.client = VictoriaLogsClient(VictoriaLogsConfig("https://vl.example", "test", cookie_file))
            cookie_file.write_text("_pomerium=first")
            self.assertEqual(self.client._headers()["Cookie"], "_pomerium=first")
            cookie_file.write_text(json.dumps({"cookie": "_pomerium=second"}))
            self.assertEqual(self.client._headers()["Cookie"], "_pomerium=second")
            cookie_file.write_text(json.dumps({"cookie": "secret", "expires": 1}))
            with self.assertRaisesRegex(RuntimeError, "expired"):
                self.client._headers()

    def test_malformed_cookie_files_are_safe_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            cookie_file = Path(directory) / "cookie"
            self.client = VictoriaLogsClient(VictoriaLogsConfig("https://vl.example", "test", cookie_file))
            for contents in ('[]', '{"cookie":123}', '{"cookie":"x=y","expires":"bad"}', '{bad json'):
                cookie_file.write_text(contents)
                with self.assertRaises(RuntimeError):
                    self.client._headers()

    def test_url_and_instance_validation(self):
        for url in ("file:///tmp/a", "https://user:secret@vl.example", "https://vl.example?secret=x"):
            with self.assertRaises(ValueError):
                VictoriaLogsConfig(url)
        with self.assertRaises(ValueError):
            VictoriaLogsConfig("https://vl.example", "../bad")


class VictoriaLogsConfigTest(unittest.TestCase):
    def test_preserves_legacy_grafana_and_named_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            with patch.multiple(config, APP_DIR=directory, CONFIG_FILE=directory / "config.json",
                                SESSIONS_DIR=directory / "sessions", PROFILES_DIR=directory / "profiles"):
                config.CONFIG_FILE.write_text(json.dumps({"grafana_url": "https://grafana.example"}))
                config.save_victorialogs_instance(VictoriaLogsConfig("https://vl.example", "mum"))
                self.assertEqual(config.load_config().grafana_url, "https://grafana.example")
                self.assertEqual(config.load_victorialogs_config().name, "mum")
                config.save_victorialogs_instance(VictoriaLogsConfig("https://other.example", "other"))
                with self.assertRaises(RuntimeError):
                    config.load_victorialogs_config()
                self.assertEqual(config.load_victorialogs_config("mum").url, "https://vl.example")
                self.assertEqual(config.CONFIG_FILE.stat().st_mode & 0o777, 0o600)
                config.save_instance("new", config.Config("https://new.example"))
                self.assertEqual(len(config.load_victorialogs_instances()), 2)
                with self.assertRaisesRegex(ValueError, "collides"):
                    config.save_victorialogs_instance(VictoriaLogsConfig("https://bad.example", "MUM"))
                raw = json.loads(config.CONFIG_FILE.read_text())
                raw["victorialogs_instances"]["MUM"] = {"url": "https://bad.example"}
                config.CONFIG_FILE.write_text(json.dumps(raw))
                with self.assertRaisesRegex(ValueError, "distinct"):
                    config.load_victorialogs_instances()

    def test_cli_setup_with_vl_only_configuration(self):
        from grafana_hs_mcp.cli import main
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            with patch.multiple(config, APP_DIR=directory, CONFIG_FILE=directory / "config.json",
                                SESSIONS_DIR=directory / "sessions", PROFILES_DIR=directory / "profiles"):
                with patch("builtins.print"):
                    main(["setup-victorialogs", "--name", "mum", "--url", "https://vl.example"])
                self.assertEqual(config.load_victorialogs_config().name, "mum")
                self.assertEqual(config.load_all_instances(), {})

    def test_vl_only_tools_register_and_list_without_secrets(self):
        from grafana_hs_mcp import server
        cfg = VictoriaLogsConfig("https://vl.example", "mum", Path("/private/secret"))
        with patch("grafana_hs_mcp.config.load_victorialogs_instances", return_value={"mum": cfg}):
            self.assertEqual(asyncio.run(server.list_victorialogs_instances()), [{"name": "mum", "url": "https://vl.example"}])
        tools = asyncio.run(server.mcp.list_tools())
        self.assertIn("query_victorialogs", {tool.name for tool in tools})


if __name__ == "__main__":
    unittest.main()
