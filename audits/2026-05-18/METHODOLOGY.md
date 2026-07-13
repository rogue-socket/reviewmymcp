# Methodology — 2026-05-18 run

## Driver

`reviewmymcp audit` (live synthetic audit) — spins each server up via stdio, generates scenarios via the LLM scenario planner, runs deterministic edge probes against every tool, captures all JSON-RPC traffic, runs evaluators, grades.

## Judge

Claude, accessed via [`claude-agent-sdk`](https://pypi.org/project/claude-agent-sdk/) using the local authenticated `claude` CLI as a subprocess. No `ANTHROPIC_API_KEY` was set in the environment; the SDK routed all judge calls through the local Claude Code subscription.

The judge swap is in `src/reviewmymcp/judge/anthropic_judge.py`: when `ANTHROPIC_API_KEY` is unset, `AnthropicJudge` falls back to `claude_agent_sdk.query()` for each `complete()` call.

## Environment

- macOS 15.3.0 (darwin)
- Python 3.14.0
- Node 25.6.1, npx 11.x
- `reviewmymcp` from this repo at commit `cf0535c` (scoring rewrite + Prod2 agent loop merged)
- `claude` CLI 2.1.133 (Claude Code) authenticated via subscription
- `claude-agent-sdk` installed via `pip install claude-agent-sdk`

## Commands

```bash
# Memory MCP — local knowledge graph, no auth
MEMORY_FILE_PATH=/tmp/memory-mcp-audit.json \
  reviewmymcp audit "npx -y @modelcontextprotocol/server-memory" \
  --transport stdio --judge-provider anthropic \
  --output html --output-file report.html

# Sentry MCP — needs read-only User Auth Token
# Scopes used: org:read, project:read, event:read, team:read, member:read
export SENTRY_ACCESS_TOKEN=sntryu_<redacted>
reviewmymcp audit "npx -y @sentry/mcp-server" \
  --transport stdio --judge-provider anthropic \
  --output html --output-file report.html

# DuckDuckGo MCP — version pinned to match the audited build
reviewmymcp audit "npx -y ddg-mcp-search@1.1.0" \
  --transport stdio --judge-provider anthropic \
  --output html --output-file report.html
```

For each server, both HTML and JSON reports were produced (`--output html` and `--output json` separately).

## Blast-radius decisions

- **Memory**: pointed `MEMORY_FILE_PATH` at `/tmp/memory-mcp-audit.json` to keep the audit's writes off any pre-existing knowledge graph. All operations are local.
- **Sentry**: token granted only `*:read` scopes. The audit's synthetic scenarios and edge probes attempted some write tools (`update_issue`, `create_team`, etc.) — these return HTTP 403 at the Sentry API and cannot mutate the user's account regardless of what arguments are passed. Confirmed via the captured traffic: zero successful mutations.
- **DuckDuckGo**: no auth, public web scraping. ~30 search calls (scenario + probes) — well under DDG's anomaly-detection threshold. Did not trigger blocking in this run.

## Pipeline configuration

- `--judge-provider anthropic` — routes to `AnthropicJudge` which used the Agent SDK path (no API key set).
- `--transport stdio` explicit (default would have been detected from the command anyway).
- Default `--severity info` — all severity levels included in the report.
- No `--no-redact`; the ingest-time redactor stripped values that looked sensitive from captured strings before writing reports.
- No `--no-llm-judges`; LLM-dependent checks (description-clarity, description-accuracy, semantic-overlap) executed.

## What was NOT exercised

- Concurrent burst sessions (`harare`-style multi-session traffic) — not used; the live synthetic audit is single-session.
- Performance load tests — `performance.throughput-degradation` and `performance.concurrent-session-scaling` reported "insufficient data" for all three servers.
- HTTP transport — all three servers are stdio.

## Cost / time

- Memory: ~5 min wall-clock
- DDG: ~3 min wall-clock
- Sentry: ~11 min wall-clock (22 tools × per-tool LLM checks → many subprocess spawns; the persistent `ClaudeSDKClient` optimisation is filed as a future-work item but not implemented in this run)

Total: ~20 min for all three. Cost in Claude tokens: comes out of the Claude Code subscription, no API billing.

## Reproducing

Run the commands above from a clean shell with `claude` authenticated and `reviewmymcp` installed. The HTML and JSON files in each `<server>/` folder of this dated run are the outputs.

Exact numbers will differ between runs because the LLM scenario planner is non-deterministic (temperature=0.3). Defect *classes* should reproduce: schema-misuse on DDG, description-clarity on all three, payload bloat on DDG, schema-rejection on Sentry, ambiguous errors on Sentry.
