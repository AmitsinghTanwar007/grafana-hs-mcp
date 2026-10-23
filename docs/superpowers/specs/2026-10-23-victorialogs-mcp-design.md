# Direct VictoriaLogs querying

## Problem and scope

Grafana MCP currently queries logs through Loki datasources. The incident's
VictoriaLogs deployment is available directly behind SSO and is not configured
as a datasource in the accessible Grafana instance. Add direct, read-only
LogsQL querying to the existing MCP server without changing Grafana authentication.

## Alternatives

1. Direct VictoriaLogs HTTP API (recommended): supports this deployment and the
   existing incident query, with independent endpoint configuration and sessions.
2. Grafana datasource proxy: reuses Grafana authentication but requires an
   installed and configured VictoriaLogs datasource that is currently absent.
3. Separate VictoriaLogs MCP server: avoids extending this package but requires
   another installation, configuration, and authentication lifecycle.

## Configuration and authentication

Keep named VictoriaLogs instances in a new top-level `victorialogs_instances`
configuration section. Each contains its base URL and an optional cookie file.
Environment overrides provide per-instance cookie or bearer credentials without
storing them in configuration. Never reuse a Grafana cookie or token for another
host. Preserve all existing Grafana configuration and commands.

Cookie files contain a Cookie header value or the existing saved-session JSON
format. Cookies are read when querying, allowing users to refresh a file without
restarting MCP. Browser import, if implemented, must select only unexpired cookies
matching the requested host and `/select/logsql/query` path. Credentials are never
included in tool output or errors. HTTP redirects are disabled so SSO redirects
produce a clear authentication error and cannot carry credentials elsewhere.

## API and tools

Add `list_victorialogs_instances` and `query_victorialogs` tools. The query accepts
LogsQL, a named instance, start/end (default last two hours), and a row limit
(default 1,000, maximum 5,000). It posts URL-encoded form data to
`/select/logsql/query` with query, start, end, limit, and a server-side timeout.
Support the existing `now`, `now-2h`, and ISO datetime conventions, validate that
start precedes end, and send explicit UTC bounds. End is exclusive.

The endpoint returns NDJSON for both ordinary records and aggregation queries.
Preserve every returned field, including arbitrary stats column names. Return
the effective time range, rows, row count, limit, and an explicit truncation
indicator if local limits are reached. Bound response bytes as well as row count,
close streamed responses on every exit path, and report malformed JSON or HTTP
errors without exposing response bodies or credentials.

Only the fixed read API endpoint is callable. No ingestion, deletion, arbitrary
URLs, live tailing, automatic wide-range searches, or automatic retries are added.

## CLI and documentation

Provide an independent VictoriaLogs setup command that saves a named URL and
optional cookie-file reference without requiring a Grafana instance. Document
environment credentials, cookie-file permissions, SSO refresh requirements, and
a raw-log and P/F aggregate query. Existing Grafana setup remains compatible.

## Validation

Use isolated temporary configuration and mocked HTTP responses to verify:

- Existing Grafana configurations remain loadable and unchanged on VL setup.
- Named instance selection and missing/ambiguous instance errors.
- Credential selection and host isolation; redirect/401/403 handling.
- Request method/path/form fields and bounded UTC time range.
- NDJSON parsing for raw records and stats, empty results, invalid JSON.
- Row/byte bounds, large individual log entries, and response cleanup.
- MCP registration and server startup with VL-only configuration.

Where authenticated access is available, run a narrow historical read-only query
against the incident endpoint and report live validation separately from mocks.
No credentials, raw incident payloads, or local configuration enter git.

## Implementation plan

1. Add independent VictoriaLogs configuration model, loader, and saver.
2. Add the HTTP client and bounded streaming query implementation.
3. Register the tools and add the CLI setup command and docs.
4. Add and run meaningful configuration/client/MCP tests and compilation checks.
5. Review the diff, commit changes, push the branch, and open a pull request.

Official API reference: https://docs.victoriametrics.com/victorialogs/querying/
