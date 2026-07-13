# Testing & Evaluation Guide

Manual testing guide for evaluating `reviewmymcp` end-to-end. Covers every CLI command, expected behaviors, and what to look for.

## Prerequisites

```bash
cd /Users/yashagrawal/conductor/workspaces/reviewmymcp/rio-de-janeiro
pip install -e ".[dev]"

# Verify the CLI is available
reviewmymcp --version

# Verify unit tests pass
python3 -m pytest tests/ -x -q
```

For LLM-judge and active-audit testing, set at least one API key:
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
# and/or
export OPENAI_API_KEY="sk-..."
# and/or
export GOOGLE_API_KEY="..."
```

For live server testing, ensure `npx` is available (Node.js):
```bash
npx --version
```

---

## Part 1: Log-Based Audit (`replay` and `audit --log-file`)

These commands take a captured NDJSON log file, run the 42 evaluator checks, and produce a scored report. Use `--no-llm-judges` for deterministic-only runs that do not call a judge provider.

### 1.1 Basic replay against the sample fixture

```bash
reviewmymcp replay tests/fixtures/sample_stdio_log.ndjson
```

**What to check:**
- Terminal output shows a table with all 9 dimensions scored (0-100) with letter grades (A-F)
- `security` dimension should score poorly (D or F) — the fixture has an SSN and a connection string in tool output
- `discoverability` should flag issues — `gd` is a poor tool name, `search_users` has a bloated description
- `efficiency` should flag `redundant-calls` — `search_users` is called twice with identical args
- `accuracy` should flag `schema-misuse` — `get_user` is called with a string for an integer field
- Top findings are listed below the table, sorted by severity
- Exit code should be **1** (critical/high findings present)

Verify exit code:
```bash
reviewmymcp replay tests/fixtures/sample_stdio_log.ndjson; echo "Exit: $?"
```

### 1.2 Replay with PII redaction disabled

```bash
reviewmymcp replay tests/fixtures/sample_stdio_log.ndjson --no-redact
```

**What to check:**
- `security` dimension should score **worse** than 1.1 — with redaction off, the `secret-leakage` check sees the raw SSN (`123-45-6789`) and connection string (`postgres://admin:s3cretpass@...`)
- Should produce CRITICAL-severity findings for `security.secret-leakage`

### 1.3 Replay the Everything Server fixture

```bash
reviewmymcp replay tests/fixtures/everything_server.ndjson
```

**What to check:**
- This is a well-built reference server — most dimensions should score A or B
- `reliability` may score D due to the timeout-behavior check (hard-cap from critical finding if a request has no response)
- No security criticals expected

### 1.4 Replay the Filesystem Server fixture

```bash
reviewmymcp replay tests/fixtures/filesystem_server.ndjson
```

**What to check:**
- Generally high scores — filesystem operations are straightforward
- May flag `security.excessive-permissions` for write/delete operations
- Check that tool names like `read_file`, `write_file`, `list_directory` get good discoverability scores

### 1.5 Output formats

Run each and verify the output is well-formed:

```bash
# JSON — should be valid, parseable JSON
reviewmymcp replay tests/fixtures/sample_stdio_log.ndjson --output json --output-file /tmp/report.json
python3 -c "import json; r=json.load(open('/tmp/report.json')); print(f'Dimensions: {len(r[\"dimension_scores\"])}, Findings: {len(r[\"top_findings\"])}')"

# HTML — should open in a browser
reviewmymcp replay tests/fixtures/sample_stdio_log.ndjson --output html --output-file /tmp/report.html
open /tmp/report.html

# SARIF — should be valid SARIF 2.1.0
reviewmymcp replay tests/fixtures/sample_stdio_log.ndjson --output sarif --output-file /tmp/report.sarif
python3 -c "import json; s=json.load(open('/tmp/report.sarif')); print(f'SARIF version: {s[\"version\"]}, runs: {len(s[\"runs\"])}, results: {len(s[\"runs\"][0][\"results\"])}')"
```

**What to check:**
- JSON report has `dimension_scores` (list of 9), `top_findings`, `server_meta`, `tool_count`, `call_count`
- HTML report renders in browser with styled tables and color-coded grades
- SARIF has `version: "2.1.0"`, one run, results with severity levels

### 1.6 Dimension filtering

```bash
reviewmymcp replay tests/fixtures/sample_stdio_log.ndjson --dimensions security,efficiency
```

**What to check:**
- Only `security` and `efficiency` dimensions appear in the output
- Other dimensions are absent (not just empty — not listed)

---

## Part 2: Live Server Audit (`audit`)

These commands start an MCP server, generate synthetic traffic, and then run evaluators. No API key needed for deterministic-only mode.

### 2.1 Audit a live server (deterministic only)

```bash
reviewmymcp audit "npx -y @modelcontextprotocol/server-everything" --no-llm-judges
```

**What to check:**
- Server starts, traffic is generated, evaluators run, report is printed
- Should take 10-30 seconds depending on npx cache
- Exit code 0 or 1 depending on findings
- Tool count and call count are shown in the report

### 2.2 Audit with LLM judges (requires API key)

```bash
reviewmymcp audit "npx -y @modelcontextprotocol/server-everything" --judge-provider anthropic
```

**What to check:**
- Additional checks run: `description-accuracy`, `description-clarity`, `semantic-overlap`
- These may add findings not present in the `--no-llm-judges` run
- Runtime is longer (LLM API calls)

### 2.3 Audit the filesystem server

```bash
reviewmymcp audit "npx -y @modelcontextprotocol/server-filesystem /tmp" --no-llm-judges
```

**What to check:**
- Different tool set from Everything Server
- Verify tool names are correctly discovered in the report

---

## Part 3: Convert Command

### 3.1 Passthrough conversion (identity transform + validation)

```bash
reviewmymcp convert tests/fixtures/sample_stdio_log.ndjson
```

**What to check:**
- Outputs valid NDJSON to stdout, one JSON object per line
- Same number of lines as input (13 non-empty lines)

### 3.2 Convert to file

```bash
reviewmymcp convert tests/fixtures/everything_server.ndjson --output-file /tmp/converted.ndjson
wc -l /tmp/converted.ndjson
```

**What to check:**
- File is created, line count matches input

### 3.3 Stub formats produce helpful errors

```bash
reviewmymcp convert tests/fixtures/sample_stdio_log.ndjson --format claude-desktop
```

**What to check:**
- Exits with code 2
- Error message explains that Claude Desktop does not log wire-level JSON-RPC traffic
- Suggests using `reviewmymcp watch` instead

```bash
reviewmymcp convert tests/fixtures/sample_stdio_log.ndjson --format python-sdk
```

**What to check:**
- Error message about Python MCP SDK format not yet implemented

---

## Part 4: Diff Command

### 4.1 Diff two reports

```bash
# Generate two reports
reviewmymcp replay tests/fixtures/everything_server.ndjson --output json --output-file /tmp/baseline.json
reviewmymcp replay tests/fixtures/sample_stdio_log.ndjson --output json --output-file /tmp/current.json

reviewmymcp diff /tmp/baseline.json /tmp/current.json
```

**What to check:**
- Shows regressions (new/worsened findings) and improvements
- Exit code 1 if new HIGH/CRITICAL findings exist in current vs baseline
- Diff output clearly marks what got worse vs what improved

### 4.2 Diff same report (no regressions)

```bash
reviewmymcp diff /tmp/baseline.json /tmp/baseline.json
```

**What to check:**
- No regressions detected
- Exit code 0

---

## Part 5: List Checks

```bash
reviewmymcp list-checks
```

**What to check:**
- Lists all 9 dimensions with their check IDs
- Total of 42 checks across all dimensions
- Each check ID follows the `dimension.check-name` pattern

---

## Part 6: Watch (Proxy Mode)

### 6.1 Capture traffic

```bash
mkdir -p /tmp/mcp-logs
reviewmymcp watch "npx -y @modelcontextprotocol/server-everything" --output-dir /tmp/mcp-logs
```

**What to check:**
- Proxy starts and prints connection info
- Send Ctrl+C after a few seconds to stop
- Log file is created in `/tmp/mcp-logs/`
- Log file contains valid NDJSON (one JSON object per line)
- Can be fed back to `reviewmymcp replay`:
  ```bash
  reviewmymcp replay /tmp/mcp-logs/*.ndjson
  ```

---

## Part 7: Active Audit (Prod2 — Agent-Driven Usability Testing)

This requires an API key. The agent actually interacts with a live MCP server.

### 7.1 Basic active audit

```bash
reviewmymcp active-audit "npx -y @modelcontextprotocol/server-everything" --agent-provider anthropic
```

**What to check:**
- Server starts, tools are discovered
- Tasks are generated across categories (discovery, single_tool, multi_step, error_recovery, ambiguous, edge_case)
- Agent runs through each task, calling tools via native tool-use
- Report shows 5 dimensions: tool_discovery, argument_quality, task_completion, error_recovery, multi_step_reasoning
- Each dimension has a score (0-100) and grade (A-F)
- Task outcomes are listed: OK/PARTIAL/FAIL/GAVE UP

### 7.2 Active audit with category filter

```bash
reviewmymcp active-audit "npx -y @modelcontextprotocol/server-everything" \
  --agent-provider anthropic \
  --categories discovery,single_tool
```

**What to check:**
- Only `discovery` and `single_tool` category tasks are generated
- Fewer tasks than 7.1
- Faster execution

### 7.3 Active audit with turn limit

```bash
reviewmymcp active-audit "npx -y @modelcontextprotocol/server-everything" \
  --agent-provider anthropic \
  --max-turns 5
```

**What to check:**
- No task runs more than 5 turns
- Complex tasks may show `turn_limit_hit` signal and `failure`/`partial` outcome

### 7.4 JSON output

```bash
reviewmymcp active-audit "npx -y @modelcontextprotocol/server-everything" \
  --agent-provider anthropic \
  --output json \
  --output-file /tmp/active-report.json
```

**What to check:**
- Valid JSON written to file
- Contains `dimension_scores`, `task_executions`, `total_tasks`, `total_turns`
- Each task execution has `task`, `turns`, `outcome`, `signals`

### 7.5 Different agent providers (if multiple API keys available)

```bash
# OpenAI
reviewmymcp active-audit "npx -y @modelcontextprotocol/server-everything" --agent-provider openai

# Gemini
reviewmymcp active-audit "npx -y @modelcontextprotocol/server-everything" --agent-provider gemini
```

**What to check:**
- Each provider completes without errors
- Scores may differ between providers (this is expected — different models handle tool-use differently)
- Compare qualitatively: which provider found tools more easily? Struggled less with arguments?

### 7.6 Active audit against filesystem server

```bash
reviewmymcp active-audit "npx -y @modelcontextprotocol/server-filesystem /tmp" --agent-provider anthropic
```

**What to check:**
- Agent interacts with file operations (read, write, list)
- Tasks involve actual filesystem operations on `/tmp`
- Verify no destructive operations were performed outside `/tmp`

> **Safety note:** The filesystem server is scoped to `/tmp` so the blast radius is limited. Do NOT point it at your home directory or project directory.

---

## Part 8: Cross-Cutting Concerns

### 8.1 Exit codes

| Scenario | Expected |
|----------|----------|
| `replay` on clean fixture (everything_server) | 0 or 1 depending on findings |
| `replay` on bad fixture (sample_stdio_log) | 1 (has critical security findings) |
| `replay` on bad fixture with `--no-redact` | 1 |
| `diff` with no regressions | 0 |
| `diff` with new critical/high findings | 1 |
| `convert` with invalid format | 2 |

### 8.2 Config file

Create a config file and verify it's respected:

```bash
cat > /tmp/test-config.json << 'EOF'
{
  "scoring": {
    "severity_weights": {
      "critical": 50.0,
      "high": 20.0,
      "medium": 8.0,
      "low": 2.0,
      "info": 0.0
    }
  },
  "thresholds": {
    "description_bloat_single": 200,
    "description_bloat_total": 2000
  }
}
EOF

reviewmymcp replay tests/fixtures/sample_stdio_log.ndjson --config /tmp/test-config.json
```

**What to check:**
- With lower `description_bloat_single` threshold (200 vs default 500), more tools should trigger the bloat check
- Scores should differ from the default run due to different severity weights

### 8.3 Error handling

```bash
# Non-existent log file
reviewmymcp replay /tmp/does-not-exist.ndjson

# Invalid server command
reviewmymcp audit "this-command-does-not-exist"

# Non-existent format
reviewmymcp convert tests/fixtures/sample_stdio_log.ndjson --format nonexistent
```

**What to check:**
- Each fails gracefully with a clear error message
- Exit code 2 for config/setup errors

---

## Part 9: Evaluation Checklist

After running through the above, assess:

| Area | Questions |
|------|-----------|
| **Scoring accuracy** | Do the grades feel right? Does a well-built server (Everything) score better than the intentionally-bad sample fixture? |
| **Finding quality** | Are finding titles clear? Are descriptions actionable? Does the evidence point to specific tools/messages? |
| **Remediation** | Is the remediation text useful? Could a developer read it and know what to fix? |
| **Terminal UX** | Is the table readable? Are colors meaningful? Is the output overwhelming or too sparse? |
| **HTML report** | Does it look professional? Is it self-contained (no external dependencies)? |
| **Active audit realism** | Do the generated tasks make sense for the server's tools? Does the agent's behavior reflect real-world usage? |
| **Provider parity** | Do different agent providers (Anthropic/OpenAI/Gemini) produce comparable quality results? |
| **Performance** | Is `replay` fast (<2s for small fixtures)? Is `active-audit` reasonable (minutes, not hours)? |
| **Failure modes** | What happens with an empty log file? A single-message log? A log with only errors? |

---

## Part 10: Testing with Your Own MCP Servers

To test against any stdio MCP server you have locally:

```bash
# Audit with synthetic traffic
reviewmymcp audit "python -m your_server_module" --no-llm-judges

# Or capture traffic first, then replay
reviewmymcp watch "python -m your_server_module" --output-dir ./logs
# ... interact with the server through the proxy ...
# Ctrl+C to stop
reviewmymcp replay ./logs/*.ndjson

# Active audit (agent-driven)
reviewmymcp active-audit "python -m your_server_module" --agent-provider anthropic
```

For servers you've previously captured traffic from, convert the logs:
```bash
reviewmymcp convert your-existing-logs.json --output-file traffic.ndjson
reviewmymcp replay traffic.ndjson
```
