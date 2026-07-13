# grafana-hs-mcp

MCP server that connects your AI assistant to Grafana. Your AI can help you write and run LogQL and SQL queries for Loki, PostgreSQL, and ClickHouse — it generates the query based on what you ask, then runs it through Grafana.

## Prerequisites

- Python 3.10 or later
- Access to any browser where you can log in to Grafana (Chrome, Chromium, Brave, Edge, Firefox, Safari, etc.)
- Access to a Grafana instance (VPN may be required)
- One of: opencode, Claude Desktop, Claude Code, Cursor, or Codex

## Install

Linux / macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/AmitsinghTanwar007/grafana-hs-mcp/master/install.sh | bash
```

The installer creates an isolated Python environment under `~/.grafana-hs-mcp/app/` and adds the `grafana-hs-mcp` command to your PATH.

## Setup

```bash
grafana-hs-mcp setup           # enter your Grafana URL; imports your existing browser session if possible
grafana-hs-mcp doctor          # verify config and Grafana connectivity
grafana-hs-mcp configure-all   # register with all supported AI clients
```

During setup, the tool first tries to import an existing `grafana_session` cookie
from your normal browser profile. If you are already logged in to Grafana in
Chrome, Brave, Edge, Firefox, Safari, etc., setup can finish without opening a
new isolated browser profile.

If no existing session is found, setup opens Grafana in your default browser so
you can log in there, then retries the cookie import. The isolated Playwright
profile flow is only used as a fallback.

Turn on VPN before running `setup` or `doctor` if your Grafana requires VPN access.

## Multiple Grafana instances

You can configure as many Grafana instances as you like. All instances are peers — there is no concept of a "primary" or "default" instance.

```bash
grafana-hs-mcp setup --name prod   # set up a prod instance
grafana-hs-mcp setup --name sbx    # set up a sandbox instance
grafana-hs-mcp instances           # list all configured instances
grafana-hs-mcp doctor              # check connectivity for all instances
grafana-hs-mcp doctor --name prod  # check only prod
```

Each instance gets its own session file (`~/.grafana-hs-mcp/sessions/<name>.json`) and browser profile directory (`~/.grafana-hs-mcp/profiles/<name>/`), so their auth is completely independent.

### Using multiple instances from your AI

Once multiple instances are configured, pass `grafana_instance` to any tool to pick which one to query:

```
List datasources for the prod instance.
→ list_datasources(grafana_instance="prod")

Show Loki logs from sbx for the last 30 minutes.
→ query_loki(query='{namespace="hs-sbx"}', start="now-30m", grafana_instance="sbx")

Query the prod PostgreSQL for failed payments in the last hour.
→ query_postgres(datasource_uid="...", sql="SELECT ...", grafana_instance="prod")
```

Use `list_grafana_instances` to discover which instances are available and their datasource UIDs:

```
What Grafana instances are configured?
→ list_grafana_instances()
```

If only one instance is configured, `grafana_instance` can be omitted and it is used automatically.

### Config file format

Instances are stored in `~/.grafana-hs-mcp/config.json`:

```json
{
  "instances": {
    "prod": {
      "grafana_url": "https://grafana.prod.example.com",
      "loki_datasource_uid": "loki"
    },
    "sbx": {
      "grafana_url": "https://grafana.sbx.example.com",
      "loki_datasource_uid": "loki-sbx",
      "clickhouse_datasource_uid": "clickhouse-sbx"
    }
  }
}
```

## Add to a specific AI client

```bash
grafana-hs-mcp configure-opencode        # opencode
grafana-hs-mcp configure-claude          # Claude Desktop
grafana-hs-mcp configure-claude-code     # Claude Code (CLI)
grafana-hs-mcp configure-cursor          # Cursor
grafana-hs-mcp configure-codex           # Codex
```

Restart your AI client after running this.

### Example: Claude Desktop config

After running `configure-claude`, your `claude_desktop_config.json` will contain:

```json
{
  "mcpServers": {
    "grafana": {
      "command": "grafana-hs-mcp"
    }
  }
}
```

### Example: opencode config

After running `configure-opencode`, your `opencode.json` will contain:

```json
{
  "mcp": {
    "grafana": {
      "type": "local",
      "command": ["grafana-hs-mcp"],
      "enabled": true
    }
  }
}
```

## Sample prompts

Once configured, try asking your AI assistant:

```
List all Grafana datasources.
```

```
Show Loki logs from the last 30 minutes for namespace validation-service-sbx.
```

```
Query the sandbox PostgreSQL datasource: how many payments failed in the last hour?
```

```
Query the ClickHouse datasource: show payment events by connector for the last 24 hours.
```

```
What errors appeared in the hyperswitch-integ namespace in the last 2 hours?
```

```
Query prod Loki for errors and sbx Loki for the same errors — compare them.
```

The AI writes the LogQL or SQL query based on your question, then executes it through Grafana and returns the results.

## Config and environment

```bash
grafana-hs-mcp env                # show all instances and environment values
grafana-hs-mcp env --interactive  # update config values interactively
grafana-hs-mcp instances          # list all configured instances
```

## Update

```bash
grafana-hs-mcp update
```

## Cleanup

```bash
grafana-hs-mcp cleanup                  # remove local data and browser profiles
grafana-hs-mcp cleanup --browser-cache  # also remove Playwright browser cache
```

## Security notes

- **Grafana session cookies** are stored in `~/.grafana-hs-mcp/session.json` (single instance) or `~/.grafana-hs-mcp/sessions/<name>.json` (named instances). Do not share these files.
- **Browser profiles** (Playwright fallback) are stored under `~/.grafana-hs-mcp/profile/` (single instance) or `~/.grafana-hs-mcp/profiles/<name>/` (named instances).
- **API token (optional):** You can set `GRAFANA_API_TOKEN` as an environment variable instead of using browser-based SSO, or `GRAFANA_API_TOKEN_<NAME>` for a specific instance. The token is never written to disk by this tool.
- **PostgreSQL queries are read-only.** The `query_postgres` tool only allows `SELECT` statements. `INSERT`, `UPDATE`, `DELETE`, `DROP`, and other write/DDL statements are blocked.
- **ClickHouse queries are read-only.** The `query_clickhouse` tool only allows `SELECT` statements. `INSERT`, `ALTER`, `DELETE`, `DROP`, `OPTIMIZE`, and other write/DDL statements are blocked.
- **VPN required** for internal Grafana instances. The tool will warn you if Grafana is unreachable before setup proceeds.
