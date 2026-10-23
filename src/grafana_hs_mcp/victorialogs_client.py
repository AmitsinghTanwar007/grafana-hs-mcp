"""Direct, bounded read-only VictoriaLogs queries with independent credentials."""
from __future__ import annotations

import json
import os
import time
from typing import Any

import requests

from .config import VictoriaLogsConfig
from .grafana_client import parse_time

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_RECORD_BYTES = 1024 * 1024


class VictoriaLogsClient:
    def __init__(self, config: VictoriaLogsConfig):
        self.config = config

    def _headers(self) -> dict[str, str]:
        suffix = self.config.name.upper().replace("-", "_")
        token = os.getenv(f"VICTORIALOGS_API_TOKEN_{suffix}")
        cookie = os.getenv(f"VICTORIALOGS_COOKIE_{suffix}")
        if token and cookie:
            raise RuntimeError("Set either a VictoriaLogs token or cookie, not both")
        if not token and not cookie and self.config.cookie_file:
            try:
                text = self.config.cookie_file.read_text().strip()
                if text.startswith(("{", "[")):
                    saved = json.loads(text)
                    if not isinstance(saved, dict):
                        raise ValueError("Saved session must be an object")
                    expires = saved.get("expires")
                    if expires is not None and not isinstance(expires, (int, float)):
                        raise ValueError("Invalid expiry")
                    if expires and expires <= time.time():
                        raise RuntimeError("VictoriaLogs cookie expired; refresh the cookie file")
                    cookie = saved.get("cookie")
                else:
                    cookie = text
            except (OSError, ValueError, TypeError):
                raise RuntimeError("Cannot read VictoriaLogs cookie file") from None
            if not isinstance(cookie, str) or not cookie:
                raise RuntimeError("VictoriaLogs cookie file has no cookie")
        credential = token or cookie
        if credential and ("\r" in credential or "\n" in credential):
            raise ValueError("VictoriaLogs credentials must be a single header value")
        if cookie and "=" not in cookie:
            raise ValueError("Cookie must contain name=value, for example _pomerium=<value>")
        headers = {"Accept": "application/stream+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        elif cookie:
            headers["Cookie"] = cookie
        return headers

    def query(
        self, query: str, start: str = "now-2h", end: str = "now",
        limit: int = 1000, timeout_seconds: int = 45,
    ) -> dict[str, Any]:
        if not query.strip():
            raise ValueError("LogsQL query cannot be empty")
        start_dt, end_dt = parse_time(start), parse_time(end)
        if start_dt >= end_dt:
            raise ValueError("start must precede end")
        limit = min(max(int(limit), 1), 5000)
        timeout_seconds = min(max(int(timeout_seconds), 1), 60)
        data = {
            "query": query, "start": start_dt.isoformat(), "end": end_dt.isoformat(),
            "limit": limit, "timeout": f"{timeout_seconds}s",
        }
        headers = self._headers()
        rows: list[dict[str, Any]] = []
        truncated = False
        reason = None
        # A fresh session avoids cross-service auth and stale cookie caching.
        with requests.Session() as session:
            session.trust_env = False  # Do not attach .netrc credentials.
            try:
                with session.post(
                    self.config.url.rstrip("/") + "/select/logsql/query",
                    data=data, headers=headers, allow_redirects=False,
                    stream=True, timeout=(10, timeout_seconds + 5),
                ) as response:
                    if response.status_code in (401, 403) or 300 <= response.status_code < 400:
                        raise RuntimeError("VictoriaLogs authentication required; refresh the endpoint's SSO cookie or token")
                    if not 200 <= response.status_code < 300:
                        raise RuntimeError(f"VictoriaLogs query failed (HTTP {response.status_code})")
                    pending = b""
                    received = 0
                    stop = False
                    for chunk in response.iter_content(chunk_size=65536):
                        if not chunk:
                            continue
                        received += len(chunk)
                        if received > MAX_RESPONSE_BYTES:
                            truncated, reason = True, "response_byte_limit"
                            break
                        pending += chunk
                        while b"\n" in pending:
                            raw, pending = pending.split(b"\n", 1)
                            if len(raw) > MAX_RECORD_BYTES:
                                raise RuntimeError("VictoriaLogs record exceeds the 1 MiB limit; select fewer fields")
                            if not raw.strip():
                                continue
                            if len(rows) >= limit:
                                truncated, reason, stop = True, "row_limit", True
                                break
                            rows.append(_parse_record(raw))
                        if stop:
                            break
                        if len(pending) > MAX_RECORD_BYTES:
                            raise RuntimeError("VictoriaLogs record exceeds the 1 MiB limit; select fewer fields")
                    if pending.strip() and not truncated:
                        if len(rows) >= limit:
                            truncated, reason = True, "row_limit"
                        else:
                            rows.append(_parse_record(pending))
            except requests.RequestException:
                raise RuntimeError("VictoriaLogs request failed; check connectivity and query timeout") from None
        # The server may have applied the limit before we receive the stream.
        possibly_more = truncated or len(rows) >= limit
        return {
            "rows": rows, "count": len(rows), "start": data["start"], "end": data["end"],
            "limit": limit, "truncated": truncated, "possibly_more": possibly_more,
            "truncation_reason": reason,
        }


def _parse_record(raw: bytes) -> dict[str, Any]:
    try:
        row = json.loads(raw)
    except (ValueError, UnicodeError):
        raise RuntimeError("VictoriaLogs returned invalid NDJSON; check authentication and endpoint configuration") from None
    if not isinstance(row, dict):
        raise RuntimeError("VictoriaLogs returned a non-object NDJSON record")
    return row
