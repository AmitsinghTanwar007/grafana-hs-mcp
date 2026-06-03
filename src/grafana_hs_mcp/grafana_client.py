from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from .auth import AuthManager
from .config import Config


logger = logging.getLogger(__name__)

_BLOCKED_SQL_KEYWORDS = {
    "insert",
    "update",
    "delete",
    "drop",
    "truncate",
    "alter",
    "create",
    "replace",
    "grant",
    "revoke",
}


def _assert_select_only(sql: str) -> None:
    """Reject any SQL that is not a read-only SELECT statement."""
    first_word = sql.strip().split()[0].lower() if sql.strip() else ""
    if first_word in _BLOCKED_SQL_KEYWORDS:
        raise ValueError(
            f"Only SELECT queries are allowed. Got: {first_word.upper()}. "
            "query_postgres is read-only for safety."
        )


class GrafanaClient:
    def __init__(self, config: Config):
        self.config = config
        self.auth = AuthManager(config)
        self.session = self.auth.ensure_authenticated()
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None

    def start_heartbeat(self, interval_seconds: int = 240) -> None:
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            return

        def loop() -> None:
            while not self._heartbeat_stop.wait(interval_seconds):
                try:
                    self.health_check()
                    logger.debug("Grafana heartbeat OK")
                except Exception as exc:
                    logger.warning("Grafana heartbeat failed: %s", exc)

        self._heartbeat_thread = threading.Thread(target=loop, daemon=True)
        self._heartbeat_thread.start()

    def stop_heartbeat(self) -> None:
        self._heartbeat_stop.set()

    def request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = f"{self.config.grafana_url}{path}"
        timeout = kwargs.pop("timeout", 30)
        response = self.session.request(method, url, timeout=timeout, **kwargs)
        if response.status_code in (401, 403):
            logger.warning(
                "Grafana returned %s; trying silent re-auth", response.status_code
            )
            if self.auth.refresh_after_401():
                response = self.session.request(method, url, timeout=timeout, **kwargs)
        response.raise_for_status()
        return response

    def health_check(self) -> dict[str, Any]:
        return self.request("GET", "/api/health", timeout=10).json()

    def list_datasources(self) -> list[dict[str, Any]]:
        data = self.request("GET", "/api/datasources", timeout=20).json()
        return [
            {
                "name": ds.get("name"),
                "uid": ds.get("uid"),
                "type": ds.get("type"),
                "url": ds.get("url"),
                "isDefault": ds.get("isDefault"),
            }
            for ds in data
        ]

    def query_loki(
        self,
        query: str,
        start: str = "now-2h",
        end: str = "now",
        limit: int = 1000,
        datasource_uid: str | None = None,
    ) -> dict[str, Any]:
        start_dt = parse_time(start)
        end_dt = parse_time(end)
        uid = datasource_uid or self.config.loki_datasource_uid
        path = f"/api/datasources/uid/{uid}/resources/query_range"
        params = {
            "query": query,
            "start": int(start_dt.timestamp() * 1_000_000_000),
            "end": int(end_dt.timestamp() * 1_000_000_000),
            "limit": min(max(int(limit), 1), 5000),
            "direction": "forward",
        }
        data = self.request("GET", path, params=params, timeout=45).json()
        return flatten_loki_response(data)

    def query_postgres(
        self,
        datasource_uid: str,
        sql: str,
    ) -> dict[str, Any]:
        _assert_select_only(sql)
        payload = {
            "queries": [
                {
                    "refId": "A",
                    "datasource": {"uid": datasource_uid},
                    "rawSql": sql,
                    "format": "table",
                    "rawQuery": True,
                }
            ],
            "from": "now-1h",
            "to": "now",
        }
        data = self.request("POST", "/api/ds/query", json=payload, timeout=45).json()
        result = data.get("results", {}).get("A", {})
        if "error" in result:
            raise RuntimeError(result["error"])
        frames = result.get("frames", [])
        if not frames:
            return {"columns": [], "rows": []}

        schema_fields = frames[0].get("schema", {}).get("fields", [])
        columns = [field.get("name", "") for field in schema_fields]
        values = frames[0].get("data", {}).get("values", [])
        rows = [dict(zip(columns, row)) for row in zip(*values)] if values else []
        return {"columns": columns, "rows": rows}


def parse_time(value: str) -> datetime:
    value = value.strip()
    now = datetime.now(timezone.utc)
    if value == "now":
        return now
    if value.startswith("now-"):
        amount = int(value[4:-1])
        unit = value[-1]
        if unit == "m":
            return now - timedelta(minutes=amount)
        if unit == "h":
            return now - timedelta(hours=amount)
        if unit == "d":
            return now - timedelta(days=amount)
        raise ValueError(f"Unsupported relative time unit: {unit}")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def flatten_loki_response(data: dict[str, Any]) -> dict[str, Any]:
    lines: list[dict[str, Any]] = []
    for stream in data.get("data", {}).get("result", []):
        labels = stream.get("stream", {})
        for ts_ns, line in stream.get("values", []):
            lines.append(
                {
                    "timestamp": datetime.fromtimestamp(
                        int(ts_ns) / 1e9, tz=timezone.utc
                    ).isoformat(),
                    "labels": labels,
                    "line": line,
                }
            )
    return {"count": len(lines), "lines": lines}
