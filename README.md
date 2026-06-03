# grafana-hs-mcp

Use Grafana from your AI client through MCP.

## Setup

Linux/macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/AmitsinghTanwar007/grafana-hs-mcp/master/install.sh | bash
grafana-hs-mcp setup
grafana-hs-mcp doctor
```

The installer creates an isolated Python environment and makes the `grafana-hs-mcp` command available. `setup` installs the required Playwright browser and opens Grafana login.

## Check Config

```bash
grafana-hs-mcp env
grafana-hs-mcp env --interactive
```

Interactive mode lets you update saved config values from the terminal.

## Add To opencode

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "grafana": {
      "type": "local",
      "command": ["grafana-hs-mcp"],
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
