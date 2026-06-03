# grafana-hs-mcp

MCP server that connects your AI assistant to Grafana. Your AI can help you write and run LogQL and SQL queries — it generates the query based on what you ask, then runs it through Grafana.

## Prerequisites

- Python 3.10 or later
- Google Chrome or Chromium installed (or Playwright Chromium will be downloaded on first setup, ~300 MB)
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
grafana-hs-mcp setup       # enter your Grafana URL, log in via browser
grafana-hs-mcp doctor      # verify config and Grafana connectivity
grafana-hs-mcp configure-all   # register with all supported AI clients
```

Turn on VPN before running `setup` or `doctor` if your Grafana requires VPN access.

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
What errors appeared in the hyperswitch-integ namespace in the last 2 hours?
```

The AI writes the LogQL or SQL query based on your question, then executes it through Grafana and returns the results.

## Config and environment

```bash
grafana-hs-mcp env                # show current config values
grafana-hs-mcp env --interactive  # update config values interactively
```

## Update

```bash
grafana-hs-mcp update
```

## Cleanup

```bash
grafana-hs-mcp cleanup                  # remove local data and browser profile
grafana-hs-mcp cleanup --browser-cache  # also remove Playwright browser cache
```

## Security notes

- **Grafana session cookies** are stored in a local Playwright browser profile under `~/.grafana-hs-mcp/profile/`. Do not share this directory.
- **API token (optional):** You can set `GRAFANA_API_TOKEN` as an environment variable instead of using browser-based SSO. The token is never written to disk by this tool.
- **PostgreSQL queries are read-only.** The `query_postgres` tool only allows `SELECT` statements. `INSERT`, `UPDATE`, `DELETE`, `DROP`, and other write/DDL statements are blocked.
- **VPN required** for internal Grafana instances. The tool will warn you if Grafana is unreachable before setup proceeds.
