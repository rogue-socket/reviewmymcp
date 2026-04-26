# reviewmymcp

Log-driven audit and evaluation tool for [MCP (Model Context Protocol)](https://modelcontextprotocol.io) servers. Point it at a server or a log file — get back a graded report with specific findings and remediation guidance.

## Why

MCP servers are proliferating faster than quality can keep pace. The protocol is well-specified; the implementations are not. Common failure modes — bloated tool descriptions that eat context windows, schemas the model misuses, tools that overlap, latency cliffs, silent auth failures, prompt injection in tool outputs — are invisible without traffic-level analysis.

`reviewmymcp` ingests MCP traffic (captured from production or generated synthetically), runs 42 evaluator checks across 9 dimensions, and produces a graded report.

## Quick Start

```bash
pip install -e ".[dev]"

# Audit a captured log file
reviewmymcp replay traffic.ndjson

# Audit a live stdio MCP server (generates synthetic traffic)
reviewmymcp audit "python -m my_mcp_server"

# Capture traffic without evaluating (proxy mode)
reviewmymcp watch "python -m my_mcp_server"

# Compare two reports for regressions
reviewmymcp diff baseline.json current.json
```

## Commands

### `reviewmymcp audit [TARGET]`

Run a full audit. Provide either a server command (stdio) or `--log-file`.

```bash
# From a log file
reviewmymcp audit --log-file traffic.ndjson

# Against a live server (generates synthetic traffic + edge probes)
reviewmymcp audit "python -m my_server"

# JSON output for CI
reviewmymcp audit --log-file traffic.ndjson --output json --output-file report.json

# SARIF for GitHub code scanning
reviewmymcp audit --log-file traffic.ndjson --output sarif --output-file results.sarif

# Skip LLM judges (faster, cheaper, deterministic-only)
reviewmymcp audit --log-file traffic.ndjson --no-llm-judges

# Use Gemini as the judge provider
reviewmymcp audit "python -m my_server" --judge-provider gemini
```

**Options:**

| Flag | Description |
|------|-------------|
| `--log-file PATH` | Use captured logs instead of live traffic |
| `--transport stdio\|http` | Force transport type (auto-detected) |
| `--output terminal\|json\|html\|sarif` | Output format (default: terminal) |
| `--output-file PATH` | Write report to file |
| `--dimensions LIST` | Comma-separated dimensions to run |
| `--severity LEVEL` | Minimum severity to report |
| `--config PATH` | Config file (thresholds, PII patterns) |
| `--no-redact` | Disable PII redaction |
| `--no-llm-judges` | Skip LLM-judge evaluators |
| `--judge-provider` | `anthropic` \| `gemini` \| `openai` |
| `--judge-model` | Override judge model name |

### `reviewmymcp replay <LOG_FILE>`

Replay a captured log file through evaluators. Same output options as `audit`.

```bash
reviewmymcp replay traffic.ndjson --output html --output-file report.html
```

### `reviewmymcp watch <TARGET>`

Start a transparent proxy and continuously capture traffic to an NDJSON log file.

```bash
# Stdio proxy
reviewmymcp watch "python -m my_server" --output-dir ./logs

# HTTP proxy (listens on :8080, forwards to upstream)
reviewmymcp watch http://localhost:3000/mcp --transport http
```

### `reviewmymcp diff <BASELINE> <CURRENT>`

Compare two JSON audit reports. Exits with code 1 if any new HIGH or CRITICAL findings (for CI gating).

```bash
reviewmymcp audit --log-file v1.ndjson --output json --output-file baseline.json
reviewmymcp audit --log-file v2.ndjson --output json --output-file current.json
reviewmymcp diff baseline.json current.json
```

### `reviewmymcp list-checks`

Print all available evaluator checks with their dimensions.

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Audit passed (no critical/high findings) |
| 1 | Audit found high or critical findings |
| 2 | Audit could not run (bad config, connection failure) |

## Evaluation Dimensions

The tool evaluates MCP servers across 9 dimensions with 42 total checks:

### Efficiency (5 checks)
Measures token cost and waste.

| Check | Severity | What it detects |
|-------|----------|----------------|
| `description-bloat` | HIGH | Tool descriptions >500 tokens, or total >5000 tokens |
| `response-payload-bloat` | HIGH | Tool responses >4000 tokens at p95 |
| `redundant-calls` | MEDIUM | Same tool called with identical arguments in a session |
| `latency-cliff` | HIGH | p99 >30s or p99/p50 ratio >10x |
| `token-cost-per-task` | MEDIUM | >10k tokens per successful tool call |

### Accuracy (5 checks)
Measures whether tools behave as described.

| Check | Severity | What it detects |
|-------|----------|----------------|
| `schema-misuse` | HIGH | Schema-invalid args accepted, or valid args rejected |
| `output-schema-drift` | MEDIUM | Inconsistent response structures across calls |
| `argument-validation-gap` | HIGH | Missing required fields accepted silently |
| `error-message-quality` | MEDIUM | Generic/uninformative error messages |
| `description-accuracy` | MEDIUM | Description promises vs actual behavior (LLM judge) |

### Discoverability (6 checks)
Measures how well models can find and understand tools.

| Check | Severity | What it detects |
|-------|----------|----------------|
| `name-quality` | LOW | Short, ambiguous, or verb-less tool names |
| `missing-examples` | MEDIUM | Complex schemas with no usage examples |
| `enum-undocumented` | MEDIUM | Enum values not documented, or implicit enums |
| `rest-wrapper-smell` | MEDIUM | CRUD patterns suggesting raw REST API wrapper |
| `description-clarity` | MEDIUM | Vague or jargon-heavy descriptions (LLM judge) |
| `semantic-overlap` | HIGH | Tools with >80% functional overlap (LLM judge) |

### Composability (5 checks)
Measures behavior under chained and programmatic use.

| Check | Severity | What it detects |
|-------|----------|----------------|
| `error-recovery-surface` | HIGH | >50% of errors give no recovery path |
| `idempotency-violation` | HIGH | Different results for identical repeated calls |
| `chained-call-failure` | MEDIUM | Output of tool A unusable as input to tool B |
| `concurrency-safety` | MEDIUM | Higher error rate under concurrent calls |
| `programmatic-readiness` | MEDIUM | Prose output instead of structured data |

### Reliability (5 checks)
Measures whether tools work consistently.

| Check | Severity | What it detects |
|-------|----------|----------------|
| `error-rate` | HIGH | >10% failure rate per tool |
| `timeout-behavior` | CRITICAL | Requests that never receive a response |
| `task-lifecycle` | HIGH | Invalid task state transitions |
| `progress-reporting` | MEDIUM | Long operations (>5s) with no progress notifications |
| `retry-semantics` | MEDIUM | Non-deterministic errors on retry |

### Security (5 checks)
Measures attack surface and data safety.

| Check | Severity | What it detects |
|-------|----------|----------------|
| `prompt-injection-surface` | CRITICAL | Injection patterns in tool outputs |
| `secret-leakage` | CRITICAL | API keys, tokens, passwords in responses |
| `auth-flow-correctness` | HIGH | Missing PKCE, tokens in URLs |
| `scope-creep` | HIGH | Using capabilities not declared during init |
| `excessive-permissions` | MEDIUM | Dangerous operations without safeguards |

### Compliance (4 checks)
Measures governance and audit readiness.

| Check | Severity | What it detects |
|-------|----------|----------------|
| `pii-in-responses` | HIGH | Emails, SSNs, phone numbers, credit cards |
| `audit-trail-completeness` | MEDIUM | Orphan requests, missing init handshake |
| `consent-flow-gaps` | HIGH | Sampling/elicitation without declared capability |
| `data-residency-signals` | MEDIUM | Unexpected geographic regions in responses |

### Conformance (6 checks)
Measures MCP protocol spec adherence.

| Check | Severity | What it detects |
|-------|----------|----------------|
| `initialize-handshake` | CRITICAL | Missing or out-of-order initialization |
| `capability-mismatch` | HIGH | Undeclared capabilities in use |
| `jsonrpc-conformance` | HIGH | Malformed JSON-RPC 2.0 messages |
| `session-management` | MEDIUM | Missing Mcp-Session-Id after assignment |
| `error-code-correctness` | LOW | Non-standard JSON-RPC error codes |
| `notification-correctness` | MEDIUM | Notifications with `id` field |

### Performance Under Load (4 checks)
Measures behavior as concurrent usage scales.

| Check | Severity | What it detects |
|-------|----------|----------------|
| `concurrent-session-scaling` | HIGH | >3x latency increase under concurrent load |
| `throughput-degradation` | HIGH | Error rate spike at higher request rates |
| `resource-contention` | MEDIUM | Tools slowing each other under concurrent use |
| `connection-pool-exhaustion` | HIGH | Connection-related errors under load |

## Grading

Each dimension gets a letter grade:

| Grade | Criteria |
|-------|----------|
| **A** | No critical or high findings. At most 2 medium. |
| **B** | No critical. At most 2 high. |
| **C** | No critical. 3+ high or 5+ medium. |
| **D** | 1 critical, or 5+ high. |
| **F** | 2+ critical. |

**Overall grade** = worst dimension grade, with a one-letter uplift if 7+ of 9 dimensions are A/B.

## Log Format

`reviewmymcp` accepts logs in NDJSON format (one JSON object per line). Each line is either a raw JSON-RPC message or a wrapper with metadata:

```jsonl
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{...},"direction":"client_to_server","timestamp":"2025-01-15T10:00:00Z","session_id":"sess-1"}
{"jsonrpc":"2.0","id":1,"result":{...},"direction":"server_to_client","timestamp":"2025-01-15T10:00:01Z","session_id":"sess-1"}
```

See [docs/log-format.md](docs/log-format.md) for the full specification.

## LLM Judges

Four checks use an LLM judge for semantic analysis. These are always optional (`--no-llm-judges` to skip).

Supported providers:

| Provider | Flag | Default Model | Env Variable |
|----------|------|---------------|-------------|
| Anthropic | `--judge-provider anthropic` | `claude-haiku-4-5-20251001` | `ANTHROPIC_API_KEY` |
| Google Gemini | `--judge-provider gemini` | `gemini-2.0-flash` | `GOOGLE_API_KEY` |
| OpenAI | `--judge-provider openai` | `gpt-4o-mini` | `OPENAI_API_KEY` |

## CI Integration

```yaml
# GitHub Actions
- name: Audit MCP Server
  run: |
    reviewmymcp audit --log-file traffic.ndjson \
      --output sarif --output-file results.sarif \
      --no-llm-judges
- name: Upload SARIF
  uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: results.sarif
```

For regression gating:

```yaml
- name: Check for regressions
  run: reviewmymcp diff baseline.json current.json
  # Exits 1 if any new HIGH+ findings
```

## Synthetic Traffic

When auditing a live server without `--log-file`, `reviewmymcp` generates synthetic traffic:

1. **Initializes** the server and discovers all tools
2. **Plans scenarios** using an LLM (or fallback basic scenarios)
3. **Executes scenarios** — multi-step tool call sequences
4. **Runs edge probes** — missing args, wrong types, nonexistent tools, malformed JSON-RPC
5. **Captures** all traffic as `McpEvent` records for evaluation

Use `--no-llm-judges` to skip LLM-based scenario planning (uses deterministic fallback).

## Configuration

Create a JSON config file and pass it with `--config`:

```json
{
  "judge": {
    "provider": "anthropic",
    "model": "claude-haiku-4-5-20251001",
    "dual_call": true
  },
  "redaction": {
    "enabled": true,
    "extra_patterns": ["INTERNAL-\\d{6}"]
  },
  "thresholds": {
    "description_bloat_single": 500,
    "description_bloat_total": 5000,
    "response_bloat_p95_bytes": 16000,
    "latency_cliff_p99_ms": 30000,
    "latency_cliff_ratio": 10,
    "token_cost_bytes_per_call": 40000
  }
}
```

## Adding Evaluators

See [docs/adding-evaluators.md](docs/adding-evaluators.md) for how to write new checks.

## Project Structure

```
src/reviewmymcp/
  cli.py                  # Click CLI entry points
  config.py               # Configuration loading
  ingest/                 # Log ingestion, parsing, normalization, redaction
  proxy/                  # Stdio and HTTP transparent proxies
  evaluators/             # 9 dimension subpackages, 42 checks
  scoring/                # Grading engine, report diffing
  reporting/              # Terminal, JSON, HTML, SARIF output
  synthetic/              # Traffic generation, edge probes, load testing
  judge/                  # Multi-provider LLM judge abstraction
```
