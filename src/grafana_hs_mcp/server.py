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
