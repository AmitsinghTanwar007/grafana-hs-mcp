# grafana-hs-mcp

MCP server that lets AI clients query Grafana using a user's own Grafana SSO session.

Current MVP tools:

- `health_check`
- `list_datasources`
- `query_loki`
- `query_postgres`

## Quick Start

Install from GitHub:

```bash
git clone git@github.com:AmitsinghTanwar007/grafana-hs-mcp.git
cd grafana-hs-mcp
python3 -m pip install -e .
grafana-hs-mcp setup
grafana-hs-mcp doctor
```

`grafana-hs-mcp setup` automatically installs the Playwright Chromium browser if needed, so users do not need to run `playwright install chromium` manually.

If SSH clone is not configured, use HTTPS:

```bash
git clone https://github.com/AmitsinghTanwar007/grafana-hs-mcp.git
cd grafana-hs-mcp
python3 -m pip install -e .
grafana-hs-mcp setup
```

For local development on this machine, the repo already exists at:

```bash
cd ~/grafana-hs-mcp
```

## One-Time Auth Setup

Run on a machine with a display:

```bash
grafana-hs-mcp setup
```

If running on an SSH server, start/use an Xvfb display first, then run:

```bash
DISPLAY=:200 grafana-hs-mcp setup
```

The setup command saves:

```text
~/.grafana-hs-mcp/config.json
~/.grafana-hs-mcp/profile/
```

No password is stored by this package. The browser profile stores the Google/Grafana session.

If you already have a valid Playwright profile, reuse it:

```bash
grafana-hs-mcp setup \
  --grafana-url https://grafana.internal.staging.in1.hyperswitch.net \
  --profile-dir ~/.grafana_pw_profile \
  --skip-browser
```

## Verify

```bash
grafana-hs-mcp doctor
```

Expected output includes Grafana health plus datasource names.

To inspect the effective config/env values in the terminal:

```bash
grafana-hs-mcp env
```

Interactive mode offers to run `doctor` after showing values:

```bash
grafana-hs-mcp env --interactive
```

Sensitive values like `GRAFANA_API_TOKEN` are masked by default.

If you only want to see config/env values:

```bash
grafana-hs-mcp env
```

## Run As MCP Server

```bash
grafana-hs-mcp
```

Usually your AI client runs this command automatically from its MCP config.

## opencode Config Example

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

After adding MCP config, restart opencode.

## Example Natural Language Prompts

```text
List Grafana datasources.
```

```text
Query Loki for validation-service-sbx logs from the last 30 minutes containing pay_123.
```

```text
Run this SQL through the sandbox datasource: select payment_id from payment_intent limit 5.
```

## Environment Overrides

```bash
export GRAFANA_URL=https://grafana.internal.staging.in1.hyperswitch.net
export GRAFANA_LOKI_DATASOURCE_UID=loki
export GRAFANA_API_TOKEN=glsa_xxx
```

If `GRAFANA_API_TOKEN` is set, Playwright is skipped.
