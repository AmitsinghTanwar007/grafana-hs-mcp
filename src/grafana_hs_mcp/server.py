from __future__ import annotations

import logging
import threading
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP

from .config import load_config, load_all_instances
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
    """List all configured Grafana instances. Use the 'name' field as grafana_instance in other tools."""
    instances = load_all_instances()
    return [
        {
            "name": name,
            "grafana_url": cfg.grafana_url,
            "loki_datasource_uid": cfg.loki_datasource_uid,
            "clickhouse_datasource_uid": cfg.clickhouse_datasource_uid,
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
async def query_prometheus(
    query: str,
    start: str = "now-1h",
    end: str = "now",
    step: str = "60s",
    datasource_uid: str | None = None,
    grafana_instance: str | None = None,
) -> dict[str, Any]:
    """Run a read-only PromQL range query through Grafana.

    Use list_datasources first to find the Prometheus or VictoriaMetrics UID.
    Time values may be `now`, `now-30m`, `now-2h`, `now-1d`, or ISO datetime.
    """
    return await anyio.to_thread.run_sync(
        lambda: get_client(grafana_instance).query_prometheus(
            query=query,
            start=start,
            end=end,
            step=step,
            datasource_uid=datasource_uid,
        )
    )


@mcp.tool()
async def list_dashboards(
    grafana_instance: str | None = None,
) -> list[dict[str, Any]]:
    """List dashboards visible to the authenticated Grafana user."""
    return await anyio.to_thread.run_sync(
        lambda: get_client(grafana_instance).list_dashboards()
    )


@mcp.tool()
async def get_dashboard(
    uid: str,
    grafana_instance: str | None = None,
) -> dict[str, Any]:
    """Get a dashboard definition and metadata by UID."""
    return await anyio.to_thread.run_sync(
        lambda: get_client(grafana_instance).get_dashboard(uid)
    )


@mcp.tool()
async def create_dashboard(
    dashboard: dict[str, Any],
    folder_uid: str = "",
    overwrite: bool = False,
    message: str = "Created by grafana-hs-mcp",
    grafana_instance: str | None = None,
) -> dict[str, Any]:
    """Create a Grafana dashboard from a dashboard JSON object.

    This performs a write operation. The dashboard must include a title.
    Set overwrite=true only when intentionally replacing an existing dashboard.
    """
    return await anyio.to_thread.run_sync(
        lambda: get_client(grafana_instance).create_dashboard(
            dashboard=dashboard,
            folder_uid=folder_uid,
            overwrite=overwrite,
            message=message,
        )
    )


@mcp.tool()
async def update_dashboard(
    dashboard: dict[str, Any],
    folder_uid: str = "",
    message: str = "Updated by grafana-hs-mcp",
    grafana_instance: str | None = None,
) -> dict[str, Any]:
    """Update an existing Grafana dashboard.

    The dashboard JSON must include its existing `uid`. This performs a write
    operation and overwrites the current dashboard revision.
    """
    return await anyio.to_thread.run_sync(
        lambda: get_client(grafana_instance).update_dashboard(
            dashboard=dashboard,
            folder_uid=folder_uid,
            message=message,
        )
    )


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
