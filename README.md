# grafana-hs-mcp

Use Grafana from your AI client through MCP.

## Setup

Linux/macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/AmitsinghTanwar007/grafana-hs-mcp/master/install.sh | bash
grafana-hs-mcp setup
grafana-hs-mcp doctor
grafana-hs-mcp configure-all
```

The installer creates an isolated Python environment and makes the `grafana-hs-mcp` command available. `setup` uses your system Chrome/Chromium if available; otherwise it asks before downloading Playwright Chromium.

## Check Config

```bash
grafana-hs-mcp env
grafana-hs-mcp env --interactive
```

Interactive mode lets you update saved config values from the terminal.

## Update

```bash
grafana-hs-mcp update
```

## Cleanup

```bash
grafana-hs-mcp cleanup
```

To also remove Playwright browser cache:

```bash
grafana-hs-mcp cleanup --browser-cache
```

## Add To AI Client

```bash
grafana-hs-mcp configure-all
```

Or configure one client:

```bash
grafana-hs-mcp configure-opencode
grafana-hs-mcp configure-claude
grafana-hs-mcp configure-cursor
grafana-hs-mcp configure-codex
```

Restart your AI client after running this.

## Try

Ask your AI:

```text
List Grafana datasources.
```

```text
Query Loki logs from validation-service-sbx for the last 30 minutes.
```
