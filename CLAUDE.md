# harare — MCP Log Collector

## What this workspace does

Drives real MCP servers over stdio, captures all JSON-RPC 2.0 traffic bidirectionally, and writes NDJSON log files for the `reviewmymcp` audit pipeline.

## Implementation status: COMPLETE

All 5 servers have been run and logs written to `logs/`:

| Server | Log file | Events |
|---|---|---|
| everything | `logs/everything.ndjson` | 183 |
| filesystem | `logs/filesystem.ndjson` | 251 |
| github | `logs/github.ndjson` | 547 |
| playwright | `logs/playwright.ndjson` | 227 |
| web_search | `logs/web_search.ndjson` | 91 |

Total: 1299 events across 5 servers.

## File structure

```
harare/
  collect_logs.py              # CLI entrypoint — orchestrates all servers
  mcp_driver/
    __init__.py
    schema.py                  # McpEvent + ToolDefinition dataclasses (no pydantic)
    driver.py                  # StdioAgentDriver — async subprocess, JSON-RPC, event collection
    edge_probes.py             # Adversarial probes: missing args, wrong types, malformed wire
  scenarios/
    __init__.py
    everything.py
    filesystem.py
    github.py
    playwright.py
    web_search.py
  logs/                        # Output NDJSON files (gitignored)
```

## Running

```bash
# Run all servers (requires GITHUB_PERSONAL_ACCESS_TOKEN for github)
GITHUB_PERSONAL_ACCESS_TOKEN=ghp_... python collect_logs.py

# Skip specific servers
python collect_logs.py --skip github playwright

# Custom output dir and timeout
python collect_logs.py --output-dir /tmp/mcp-logs --timeout 120
```

## Key design decisions

- **No pip dependencies** — stdlib + asyncio only. MCP servers installed on-the-fly via `npx -y`.
- **StdioAgentDriver** (`mcp_driver/driver.py`) — async subprocess driver that records every sent/received message as a `McpEvent` with direction, timestamp, and latency.
- **Edge probes** run after each server's scenarios: missing required args, wrong types, empty args, extra args, nonexistent tool, malformed JSON-RPC.
- **Concurrent burst sessions** — 3 parallel `StdioAgentDriver` instances per server to exercise `performance.concurrent-session-scaling`.
- **NDJSON output** — each line is the raw JSON-RPC message merged with envelope fields (`direction`, `timestamp`, `session_id`). Matches the format expected by the review tool's ingest pipeline.
- **`is_error` detection** — flagged when `error` key present in message OR when `result.isError` is true (MCP tool error convention).

## Python environment

Use the `reviewmymcp` conda env if it exists, or Python 3.11+. No extra packages needed beyond stdlib.

## Logs are gitignored

The `logs/` directory is in `.gitignore`. Re-run `collect_logs.py` to regenerate.
