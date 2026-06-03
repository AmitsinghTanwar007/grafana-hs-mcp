# grafana-hs-mcp

Use Grafana from your AI client through MCP.

## Setup

```bash
uvx --from git+https://github.com/AmitsinghTanwar007/grafana-hs-mcp.git grafana-hs-mcp setup
```

This installs the package, installs the required Playwright browser, and opens Grafana login.

Verify:

```bash
uvx --from git+https://github.com/AmitsinghTanwar007/grafana-hs-mcp.git grafana-hs-mcp doctor
```

## Check Config

```bash
uvx --from git+https://github.com/AmitsinghTanwar007/grafana-hs-mcp.git grafana-hs-mcp env
```

## Add To opencode

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "grafana": {
      "type": "local",
      "command": [
        "uvx",
        "--from",
        "git+https://github.com/AmitsinghTanwar007/grafana-hs-mcp.git",
        "grafana-hs-mcp"
      ],
      "enabled": true
    }
  }
}
```

Restart opencode after adding this.

## Try

Ask your AI:

```text
List Grafana datasources.
```

```text
Query Loki logs from validation-service-sbx for the last 30 minutes.
```
