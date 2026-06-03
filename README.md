# grafana-hs-mcp

Use Grafana from your AI client through MCP.

## Install

```bash
git clone git@github.com:AmitsinghTanwar007/grafana-hs-mcp.git
cd grafana-hs-mcp
python3 -m pip install -e .
grafana-hs-mcp setup
grafana-hs-mcp doctor
```

If SSH clone is not configured:

```bash
git clone https://github.com/AmitsinghTanwar007/grafana-hs-mcp.git
cd grafana-hs-mcp
python3 -m pip install -e .
grafana-hs-mcp setup
grafana-hs-mcp doctor
```

`setup` installs the required Playwright Chromium browser and opens Grafana login.

## Check Config

```bash
grafana-hs-mcp env
```

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
