# grafana-hs-mcp

MCP server that connects your AI assistant to Grafana. Your AI can help you write and run PromQL, LogQL, and read-only SQL queries, and can manage dashboards through Grafana's API.

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
Query the VictoriaMetrics datasource for CPU usage in the hyperswitch namespace.
```

```
List the Grafana dashboards in prod.
```

```
Create a dashboard for UCS CI runner CPU, memory, and pod counts.
```

```
What errors appeared in the hyperswitch-integ namespace in the last 2 hours?
```

```
Query prod Loki for errors and sbx Loki for the same errors — compare them.
```

The AI writes the PromQL, LogQL, or SQL query based on your question, then executes it through Grafana and returns the results. Dashboard creation and updates use Grafana's dashboard API and require explicit use of the corresponding write tool.

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

## Direct VictoriaLogs endpoints

VictoriaLogs can be queried directly even when it is absent from Grafana's
configured datasources. It uses **LogsQL**, independent named configuration, and
separate credentials; Grafana login cookies and tokens are never reused.

```bash
grafana-hs-mcp setup-victorialogs --name mum \
  --url https://victorialogs.example.com \
  --cookie-file ~/.grafana-hs-mcp/vl-cookie
```

Use the base URL, without `/select/vmui/`. The cookie file contains a complete
Cookie header, such as `_pomerium=<session value>`, or saved-session JSON with
`cookie` and optional numeric Unix `expires` fields. Protect it with `chmod 600`.
Refresh the file when SSO expires; it is read again on every query. This command
only saves configuration; test authenticated access with the tools below.

Alternatively, set `VICTORIALOGS_COOKIE_MUM` or `VICTORIALOGS_API_TOKEN_MUM` in the
MCP server environment. The suffix is the instance name uppercased, with hyphens
replaced by underscores; names must have distinct suffixes. Environment credentials
override the file, and setting both a cookie and token is an error. No secret is
written into the instance configuration. Cookie-file paths and secrets are not
returned by instance discovery.

Tools:

- `list_victorialogs_instances()` lists names and endpoint URLs.
- `query_victorialogs(query, start="now-2h", end="now", limit=1000,
  victorialogs_instance=None, timeout_seconds=45)` posts to the read-only
  `/select/logsql/query` endpoint. Omit the instance only if exactly one is configured.

Example raw query: `{stream="jplp"} "connector-service-http"`.
Example aggregation:

```text
{stream="jplp"} "connector-service-http" ("stdout P" OR "stdout F")
| extract "stdout <marker> " from _msg
| len(_msg) as msg_len
| stats by (marker, _time:1h) count() as lines, max(msg_len) as max_len
```

Specify explicit ISO UTC start/end for historical incidents; the end is exclusive.
Results preserve arbitrary JSON fields in `rows`, with `count`, effective time
bounds, and limit metadata. `truncated` indicates a local response limit;
`possibly_more` also becomes true when the returned count reaches the server row
limit. Neither flag establishes the total number of matching records.

Requests are capped at 5,000 rows and a 60-second server timeout (45 by default).
Responses are streamed with an 8 MiB total bound and a 1 MiB individual-record
bound, supporting application records larger than 16 KB. If the total byte limit
is reached, complete rows already parsed are returned with a truncation reason;
a record exceeding 1 MiB raises an error. Use `fields` or narrower queries to
reduce large responses. Redirects are rejected with an authentication error;
refresh the cookie rather than supplying credentials from another service.
Direct requests ignore proxy and `.netrc` settings from the environment, and do
not automatically log in or retry queries. Grafana's existing `doctor` command
checks Grafana instances; use a narrow `query_victorialogs` query to verify VL.

Developer validation:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```
