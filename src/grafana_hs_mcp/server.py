from __future__ import annotations

import logging
import threading
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP

from .config import load_config, load_all_instances, get_default_instance_name
from .grafana_client import GrafanaClient


logger = logging.getLogger(__name__)

mcp = FastMCP("grafana-hs-mcp")

_clients: dict[str, GrafanaClient] = {}
_clients_lock = threading.Lock()


def get_client(instance: str | None = None) -> GrafanaClient:
    cfg = load_config(instance)
    key = cfg.name
    with _clients_lock:
        if key not in _clients:
            _clients[key] = GrafanaClient(cfg)
            _clients[key].start_heartbeat()
    return _clients[key]


@mcp.tool()
async def list_grafana_instances() -> list[dict[str, Any]]:
    """List all configured Grafana instances and which one is the default."""
    default = get_default_instance_name()
    instances = load_all_instances()
    return [
        {
            "name": name,
            "grafana_url": cfg.grafana_url,
            "loki_datasource_uid": cfg.loki_datasource_uid,
            "clickhouse_datasource_uid": cfg.clickhouse_datasource_uid,
            "is_default": name == default,
        }
        for name, cfg in instances.items()
    ]


@mcp.tool()
async def health_check(grafana_instance: str | None = None) -> dict[str, Any]:
    """Check whether the MCP server can access Grafana.

    grafana_instance: name of the Grafana instance to use (omit for default).
    """
    return await anyio.to_thread.run_sync(lambda: get_client(grafana_instance).health_check())


@mcp.tool()
async def list_datasources(grafana_instance: str | None = None) -> list[dict[str, Any]]:
    """List Grafana datasource names, UIDs, and types.

    grafana_instance: name of the Grafana instance to use (omit for default).
    """
    return await anyio.to_thread.run_sync(lambda: get_client(grafana_instance).list_datasources())


@mcp.tool()
async def query_loki(
    query: str,
    start: str = "now-2h",
    end: str = "now",
    limit: int = 1000,
    datasource_uid: str | None = None,
    grafana_instance: str | None = None,
) -> dict[str, Any]:
    """
    Query Grafana Loki logs using LogQL.

    Time format: `now`, `now-30m`, `now-2h`, `now-1d`, or ISO datetime.
    Limit is capped to 5000.
    grafana_instance: name of the Grafana instance to use (omit for default).

    Before writing a query, use list_loki_labels and list_loki_label_values
    to discover the correct label names and values for this Loki instance.
    """
    return await anyio.to_thread.run_sync(
        lambda: get_client(grafana_instance).query_loki(
            query=query,
            start=start,
            end=end,
            limit=limit,
            datasource_uid=datasource_uid,
        )
    )


@mcp.tool()
async def search_loki(
    query: str,
    limit: int = 500,
    datasource_uid: str | None = None,
    grafana_instance: str | None = None,
) -> dict[str, Any]:
    """
    Search Loki logs with automatic time range expansion.

    Use this when you don't know when an event occurred. It tries progressively
    wider windows — 2h, 6h, 24h, 3d, 7d — and stops as soon as results are
    found. The response includes a `searched_range` field showing which window
    produced the results.
    grafana_instance: name of the Grafana instance to use (omit for default).

    Use query_loki instead when you already know the time range.
    """
    return await anyio.to_thread.run_sync(
        lambda: get_client(grafana_instance).search_loki(
            query=query,
            limit=limit,
            datasource_uid=datasource_uid,
        )
    )


@mcp.tool()
async def list_loki_labels(
    datasource_uid: str | None = None,
    start: str = "now-24h",
    end: str = "now",
    grafana_instance: str | None = None,
) -> list[str]:
    """
    List all label names present in Loki for the given time range.

    Call this first to discover what labels are available (e.g. namespace,
    app, container, pod) before writing a LogQL query.
    grafana_instance: name of the Grafana instance to use (omit for default).
    """
    return await anyio.to_thread.run_sync(
        lambda: get_client(grafana_instance).list_loki_labels(
            datasource_uid=datasource_uid,
            start=start,
            end=end,
        )
    )


@mcp.tool()
async def list_loki_label_values(
    label_name: str,
    datasource_uid: str | None = None,
    start: str = "now-24h",
    end: str = "now",
    grafana_instance: str | None = None,
) -> list[str]:
    """
    List all values for a Loki label name within the given time range.

    Use this after list_loki_labels to see what values exist for a label
    (e.g. for label "namespace" you might get ["hs-sandbox", "hs-integ"]).
    grafana_instance: name of the Grafana instance to use (omit for default).
    """
    return await anyio.to_thread.run_sync(
        lambda: get_client(grafana_instance).list_loki_label_values(
            label_name=label_name,
            datasource_uid=datasource_uid,
            start=start,
            end=end,
        )
    )


@mcp.tool()
async def query_postgres(
    datasource_uid: str,
    sql: str,
    grafana_instance: str | None = None,
) -> dict[str, Any]:
    """
    Run a read-only SQL SELECT query through a Grafana PostgreSQL datasource.

    Only SELECT statements are permitted. INSERT, UPDATE, DELETE, DROP, and
    other write/DDL statements are blocked for safety.
    grafana_instance: name of the Grafana instance to use (omit for default).
    """
    return await anyio.to_thread.run_sync(
        lambda: get_client(grafana_instance).query_postgres(datasource_uid=datasource_uid, sql=sql)
    )


@mcp.tool()
async def query_clickhouse(
    sql: str,
    datasource_uid: str | None = None,
    grafana_instance: str | None = None,
) -> dict[str, Any]:
    """
    Run a read-only SQL SELECT query through a Grafana ClickHouse datasource.

    Only SELECT statements are permitted. INSERT, ALTER, DROP, OPTIMIZE, and
    other write/DDL statements are blocked for safety.
    grafana_instance: name of the Grafana instance to use (omit for default).

    If datasource_uid is omitted, the configured default ClickHouse datasource
    UID is used.
    """
    return await anyio.to_thread.run_sync(
        lambda: get_client(grafana_instance).query_clickhouse(sql=sql, datasource_uid=datasource_uid)
    )


def run() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    mcp.run()
