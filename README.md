# mcp-log-collector

Development utility for collecting real MCP server JSON-RPC traffic as NDJSON.

This branch is a support product for `reviewmymcp audit`: it drives selected
public MCP servers over stdio, runs deterministic scenarios plus edge probes,
and writes replayable logs.

## Usage

```bash
python3 collect_logs.py --output-dir logs/
```

Skip servers that are slow, unavailable, or require credentials:

```bash
python3 collect_logs.py --output-dir logs/ --skip github playwright
python3 collect_logs.py --output-dir logs/ --skip everything --skip filesystem
```

Collected files are written as:

- `everything.ndjson`
- `filesystem.ndjson`
- `github.ndjson`
- `playwright.ndjson`
- `web_search.ndjson`

Replay a collected file from the audit branch:

```bash
reviewmymcp replay logs/everything.ndjson
```

## Servers

| Name | Command | Notes |
| --- | --- | --- |
| `everything` | `npx -y @modelcontextprotocol/server-everything` | Reference server coverage. |
| `filesystem` | `npx -y @modelcontextprotocol/server-filesystem <tmpdir>` | Uses an isolated temporary directory. |
| `github` | `npx -y @modelcontextprotocol/server-github` | Requires `GITHUB_PERSONAL_ACCESS_TOKEN`; use read-only throwaway credentials. |
| `playwright` | `npx -y @playwright/mcp@latest` | Installs Chromium if needed. |
| `web_search` | `npx -y @iflow-mcp/pskill9-web-search` | Third-party search server coverage. |

## Safety

Use throwaway or read-only credentials. The collector disables concurrent burst
traffic for GitHub and Playwright, but scenarios still call live tools. Do not
point it at production accounts with write-capable tokens.

Generated logs may contain private response data from the target server. Review
and sanitize artifacts before committing or sharing them.

## Validation

```bash
pytest
ruff check .
python3 -m py_compile collect_logs.py mcp_driver/*.py scenarios/*.py tests/test_collect_logs.py
```

The test suite checks scenario coverage summaries, stale scenario tool
detection, skip handling, and the all-skipped smoke path.
