from __future__ import annotations

import logging
import threading
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP

from .config import load_config
from .grafana_client import GrafanaClient


logger = logging.getLogger(__name__)

mcp = FastMCP("grafana-hs-mcp")
_client: GrafanaClient | None = None
_client_lock = threading.Lock()


def get_client() -> GrafanaClient:
    global _client
    with _client_lock:
        if _client is None:
            _client = GrafanaClient(load_config())
            _client.start_heartbeat()
    return _client


@mcp.tool()
async def health_check() -> dict[str, Any]:
    """Check whether the MCP server can access Grafana."""
    return await anyio.to_thread.run_sync(lambda: get_client().health_check())


@mcp.tool()
async def list_datasources() -> list[dict[str, Any]]:
    """List Grafana datasource names, UIDs, and types."""
    return await anyio.to_thread.run_sync(lambda: get_client().list_datasources())


@mcp.tool()
async def query_loki(
    query: str,
    start: str = "now-2h",
    end: str = "now",
    limit: int = 1000,
    datasource_uid: str | None = None,
) -> dict[str, Any]:
    """
    Query Grafana Loki logs using LogQL.

    Time format: `now`, `now-30m`, `now-2h`, `now-1d`, or ISO datetime.
    Limit is capped to 5000.

    Before writing a query, use list_loki_labels and list_loki_label_values
    to discover the correct label names and values for this Loki instance.
    """
    return await anyio.to_thread.run_sync(
        lambda: get_client().query_loki(
            query=query,
            start=start,
            end=end,
            limit=limit,
            datasource_uid=datasource_uid,
        )
    )


@mcp.tool()
async def list_loki_labels(
    datasource_uid: str | None = None,
    start: str = "now-24h",
    end: str = "now",
) -> list[str]:
    """
    List all label names present in Loki for the given time range.

    Call this first to discover what labels are available (e.g. namespace,
    app, container, pod) before writing a LogQL query.
    """
    return await anyio.to_thread.run_sync(
        lambda: get_client().list_loki_labels(
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
) -> list[str]:
    """
    List all values for a Loki label name within the given time range.

    Use this after list_loki_labels to see what values exist for a label
    (e.g. for label "namespace" you might get ["hs-sandbox", "hs-integ"]).
    Then build a precise LogQL stream selector like {namespace="hs-sandbox"}.
    """
    return await anyio.to_thread.run_sync(
        lambda: get_client().list_loki_label_values(
            label_name=label_name,
            datasource_uid=datasource_uid,
            start=start,
            end=end,
        )
    )


@mcp.tool()
async def query_postgres(datasource_uid: str, sql: str) -> dict[str, Any]:
    """
    Run a read-only SQL SELECT query through a Grafana PostgreSQL datasource.

    Only SELECT statements are permitted. INSERT, UPDATE, DELETE, DROP, and
    other write/DDL statements are blocked for safety.
    """
    return await anyio.to_thread.run_sync(
        lambda: get_client().query_postgres(datasource_uid=datasource_uid, sql=sql)
    )


def run() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    mcp.run()
