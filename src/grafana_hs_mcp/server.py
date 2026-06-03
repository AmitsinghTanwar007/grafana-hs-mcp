from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import load_config
from .grafana_client import GrafanaClient


logger = logging.getLogger(__name__)

mcp = FastMCP("grafana-hs-mcp")
_client: GrafanaClient | None = None


def get_client() -> GrafanaClient:
    global _client
    if _client is None:
        _client = GrafanaClient(load_config())
        _client.start_heartbeat()
    return _client


@mcp.tool()
def health_check() -> dict[str, Any]:
    """Check whether the MCP server can access Grafana."""
    return get_client().health_check()


@mcp.tool()
def list_datasources() -> list[dict[str, Any]]:
    """List Grafana datasource names, UIDs, and types."""
    return get_client().list_datasources()


@mcp.tool()
def query_loki(
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
    return get_client().query_loki(
        query=query,
        start=start,
        end=end,
        limit=limit,
        datasource_uid=datasource_uid,
    )


@mcp.tool()
def query_postgres(datasource_uid: str, sql: str) -> dict[str, Any]:
    """Run a SQL query through a Grafana PostgreSQL datasource UID."""
    return get_client().query_postgres(datasource_uid=datasource_uid, sql=sql)


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    mcp.run()
