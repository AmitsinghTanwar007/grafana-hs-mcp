from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from .auth import AuthManager
from .config import Config


logger = logging.getLogger(__name__)


def _assert_select_only(sql: str) -> None:
    """Reject any SQL that is not a read-only SELECT statement."""
    sql_clean = sql.strip().lower()
    if not sql_clean.startswith("select"):
        raise ValueError(
            "Only SELECT queries are allowed. query_postgres is read-only for safety."
        )


def _chunk_size(start_dt: datetime, end_dt: datetime) -> timedelta:
    """Return an appropriate chunk size based on the total range duration.

    >5d  → 1-day chunks   (e.g. now-30d to now-20d → 10 chunks)
    >1d  → 6-hour chunks  (e.g. 3-day range → 12 chunks)
    >6h  → 1-hour chunks  (e.g. 12-hour range → 12 chunks)
    ≤6h  → 30-min chunks
    """
    span = end_dt - start_dt
    if span > timedelta(days=5):
        return timedelta(days=1)
    if span > timedelta(days=1):
        return timedelta(hours=6)
    if span > timedelta(hours=6):
        return timedelta(hours=1)
    return timedelta(minutes=30)


def _iter_chunks(
    start_dt: datetime, end_dt: datetime
) -> list[tuple[datetime, datetime]]:
    """Split [start_dt, end_dt] into chunks; returns list of (chunk_start, chunk_end)."""
    size = _chunk_size(start_dt, end_dt)
    chunks = []
    cur = start_dt
    while cur < end_dt:
        nxt = min(cur + size, end_dt)
        chunks.append((cur, nxt))
        cur = nxt
    return chunks


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

    # ------------------------------------------------------------------
    # Internal: single raw Loki query (raises on HTTP error)
    # ------------------------------------------------------------------

    def _query_loki_raw(
        self,
        query: str,
        start_dt: datetime,
        end_dt: datetime,
        limit: int,
        uid: str,
    ) -> dict[str, Any]:
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

    # ------------------------------------------------------------------
    # Internal: chunked query — splits range on error or large spans
    # ------------------------------------------------------------------

    def _query_loki_chunked(
        self,
        query: str,
        start_dt: datetime,
        end_dt: datetime,
        limit: int,
        uid: str,
    ) -> dict[str, Any]:
        chunks = _iter_chunks(start_dt, end_dt)
        chunk_summary: list[dict[str, Any]] = []
        sample_lines: list[dict[str, Any]] = []
        total_count = 0
        chunks_with_results = 0
        per_chunk_limit = max(limit // max(len(chunks), 1), 50)

        for chunk_start, chunk_end in chunks:
            label = (
                f"{chunk_start.strftime('%Y-%m-%d %H:%M')} → "
                f"{chunk_end.strftime('%Y-%m-%d %H:%M')} UTC"
            )
            try:
                result = self._query_loki_raw(
                    query, chunk_start, chunk_end, per_chunk_limit, uid
                )
                count = result["count"]
                total_count += count
                if count > 0:
                    chunks_with_results += 1
                    if len(sample_lines) < 20:
                        sample_lines.extend(result["lines"][: 20 - len(sample_lines)])
                chunk_summary.append({"range": label, "count": count, "error": None})
            except Exception as exc:
                logger.warning("Chunk %s failed: %s", label, exc)
                chunk_summary.append({"range": label, "count": 0, "error": str(exc)})

        return {
            "mode": "chunked",
            "total_count": total_count,
            "chunks_searched": len(chunks),
            "chunks_with_results": chunks_with_results,
            "chunk_summary": chunk_summary,
            "sample_lines": sample_lines,
            "note": (
                f"Range was too large for a single query; "
                f"split into {len(chunks)} chunks of "
                f"{_chunk_size(start_dt, end_dt)}."
            ),
        }

    # ------------------------------------------------------------------
    # Public: query_loki — single query with automatic chunked fallback
    # ------------------------------------------------------------------

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
        capped = min(max(int(limit), 1), 5000)

        try:
            return self._query_loki_raw(query, start_dt, end_dt, capped, uid)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code in (400, 500):
                logger.warning(
                    "query_loki single-shot failed (%s); switching to chunked mode",
                    exc.response.status_code,
                )
                return self._query_loki_chunked(query, start_dt, end_dt, capped, uid)
            raise

    # ------------------------------------------------------------------
    # Public: search_loki — progressive range expansion only
    # (chunking is only for query_loki when the user gives an explicit range)
    # ------------------------------------------------------------------

    _SEARCH_RANGES = ["now-2h", "now-6h", "now-24h", "now-3d", "now-7d"]

    def search_loki(
        self,
        query: str,
        limit: int = 500,
        datasource_uid: str | None = None,
    ) -> dict[str, Any]:
        """
        Search Loki with automatic range expansion.

        Tries progressively wider windows (2h → 6h → 24h → 3d → 7d) and
        stops as soon as results are found. On any error for a given range,
        skips to the next wider range rather than chunking — chunking only
        applies in query_loki when the user gives an explicit large range.
        """
        uid = datasource_uid or self.config.loki_datasource_uid
        end_dt = parse_time("now")
        capped = min(max(int(limit), 1), 5000)

        for range_str in self._SEARCH_RANGES:
            start_dt = parse_time(range_str)
            try:
                result = self._query_loki_raw(query, start_dt, end_dt, capped, uid)
            except Exception as exc:
                logger.warning(
                    "search_loki: range %s failed (%s), trying next wider range",
                    range_str,
                    exc,
                )
                continue

            if result["count"] > 0:
                result["searched_range"] = range_str
                return result

            logger.debug("search_loki: no results in %s, widening range", range_str)

        # Exhausted all automatic ranges — ask the user for a hint
        return {
            "count": 0,
            "lines": [],
            "searched_range": self._SEARCH_RANGES[-1],
            "needs_user_input": True,
            "ask_user": (
                "No results found in the last 7 days. "
                "Do you have a rough idea when this occurred? "
                "For example: '2 weeks ago', 'around Nov 20–25', 'about 3 weeks ago', "
                "or an exact range like 'May 1 to May 10'. "
                "Once you tell me, I will search that specific window using query_loki "
                "with start='now-Xd' and end='now-Yd', or ISO dates like '2025-05-01'."
            ),
        }

    def list_loki_labels(
        self,
        datasource_uid: str | None = None,
        start: str = "now-24h",
        end: str = "now",
    ) -> list[str]:
        """Return all label names present in Loki for the given time range."""
        uid = datasource_uid or self.config.loki_datasource_uid
        path = f"/api/datasources/uid/{uid}/resources/labels"
        start_dt = parse_time(start)
        end_dt = parse_time(end)
        params = {
            "start": int(start_dt.timestamp() * 1_000_000_000),
            "end": int(end_dt.timestamp() * 1_000_000_000),
        }
        data = self.request("GET", path, params=params, timeout=20).json()
        return sorted(data.get("data", []))

    def list_loki_label_values(
        self,
        label_name: str,
        datasource_uid: str | None = None,
        start: str = "now-24h",
        end: str = "now",
    ) -> list[str]:
        """Return all values for a Loki label name within the given time range."""
        uid = datasource_uid or self.config.loki_datasource_uid
        path = f"/api/datasources/uid/{uid}/resources/label/{label_name}/values"
        start_dt = parse_time(start)
        end_dt = parse_time(end)
        params = {
            "start": int(start_dt.timestamp() * 1_000_000_000),
            "end": int(end_dt.timestamp() * 1_000_000_000),
        }
        data = self.request("GET", path, params=params, timeout=20).json()
        return sorted(data.get("data", []))

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
        if unit == "w":
            return now - timedelta(weeks=amount)
        raise ValueError(
            f"Unsupported relative time unit: {unit!r}. Use m, h, d, or w."
        )
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


def _assert_select_only(sql: str) -> None:
    """Reject any SQL that is not a read-only SELECT statement."""
    sql_clean = sql.strip().lower()
    if not sql_clean.startswith("select"):
        raise ValueError(
            "Only SELECT queries are allowed. query_postgres is read-only for safety."
        )
