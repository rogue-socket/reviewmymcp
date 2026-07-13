# Design Decisions — reviewmymcp

Captured 2026-04-29 from design review session.

---

## Architecture

### Two independent products

- **Prod1 (`reviewmymcp audit`)**: Log-based MCP server audit. Ingests captured JSON-RPC logs, runs 42 checks across 9 dimensions, produces a graded report.
- **Prod2 (`reviewmymcp active-audit`)**: Active agent-driven usability testing. An LLM agent talks to a live MCP server, attempts tasks, and behavioral signals are scored across 5 dimensions.
- **Log collector** (`mcp-log-collector` branch): Personal dev tool for generating test NDJSON files. Not user-facing, not a product.

Prod1 and prod2 are independent codebases on separate branches. No shared library extraction — merge later if/when it makes sense.

### Separate reports, no merging

Prod1 and prod2 each produce their own report. No combined report, no merged grading. They measure fundamentally different things (protocol correctness vs. agent usability). Users run them independently.

---

## Scoring & Grading

### Numeric scores, not pure severity counts

Each dimension gets a **0-100 numeric score**. Letter grade derived from the score: A (90+), B (75+), C (60+), D (40+), F (<40).

### Scoring formula (revised 2026-05-16)

```
raw  = Σ severity_weight(f) for f in findings
score = max(0, 100 − √min(raw, 100) × 5)
```

Severity weights: critical = 25, high = 10, medium = 4, low = 1.

The square-root softens the curve so high-count low-severity findings don't crater the score linearly (e.g., 24 mediums lands at ~50, not 4). The `min(raw, 100)` cap floors the score at 50 from severity weights alone — hard caps below handle the catastrophic cases.

### Hard caps

Applied after the numeric score; never improve the grade.

- 2+ critical → F
- 1 critical → cap at D
- 5+ high → cap at D
- 3+ high → cap at C

### Why no per-dimension denominator (history)

The original design (captured at this review) was to normalize deductions by a per-dimension denominator: tool count for discoverability/efficiency/accuracy/security; call count for reliability/composability/performance; raw for conformance/compliance. Validation against the harare-collected logs (filesystem, everything, web_search, playwright, github) showed this produced opposite failure modes at the poles: a 1-tool server with 5 MEDIUM findings scored F (amplification), while a 14-tool/53-call server with 5 HIGH findings scored A (dilution). Findings already partition the surface area, so dividing by denominator double-counts. The denominator was removed; the sqrt curve plus HIGH hard caps replaced it.

### No overall server grade

Per-dimension scores are the product. No rolled-up single grade. CI gating is handled by exit codes (exit 1 if any critical/high findings).

---

## Evaluators

### checks_skipped field

`EvaluatorResult` needs a `checks_skipped: list[tuple[str, str]]` field (check ID + reason). This distinguishes "no findings" from "couldn't evaluate due to insufficient data." Example: `("performance.concurrent-session-scaling", "only 1 session in logs")`.

### Probe traffic vs. real traffic (2026-05-16)

`McpEvent` carries an `is_probe: bool` flag (plus optional `probe_type: str`) so evaluators can distinguish harness-injected adversarial calls (harare's `mcp_driver/edge_probes.py` and rio's `synthetic/edge_probes.py`) from real client traffic. Probe metadata is stamped at the source — harare's driver writes `is_probe` into the NDJSON wrapper, rio's synthetic driver sets it on in-process events. The ingestion parser reads the wrapper field; the correlator propagates from request to response. Foreign logs (Claude Desktop, Python SDK debug) have no probe concept and simply leave the flag false.

Per-check policy:

- **Filter probes (silently):** all `reliability` checks except `timeout-behavior`; `efficiency.response-payload-bloat / redundant-calls / latency-cliff / token-cost-per-task`; `accuracy.output-schema-drift / error-message-quality / description-accuracy`; `discoverability.enum-undocumented` (observed-values sub-check); all `composability` checks; `compliance.audit-trail-completeness` (orphan sub-check); `conformance.jsonrpc-conformance` (probe REQUESTS only — probe RESPONSES are server output and stay in); all `performance` checks except `connection-pool-exhaustion`.
- **Keep probes (they ARE the signal):** `reliability.timeout-behavior` (we care whether a server hangs on bad input), `accuracy.schema-misuse`, `accuracy.argument-validation-gap`, `conformance.error-code-correctness`, `performance.connection-pool-exhaustion`.
- **Unaffected:** all metadata-only checks (tool descriptions, init handshake, capability declarations, etc.).

Filtering is silent rather than reported via `SkippedCheck` — the check still ran, just on a smaller set; `SkippedCheck` is reserved for "couldn't evaluate."

Heuristic detection (post-hoc pattern matching for "this looks like a bad call") was considered and rejected: a user testing their own server with a deliberately bad call would be silently excluded, and "missing required" detection needs `tools/list` schema lookup anyway. Source tagging is cleaner.

### LLM judges

The judge module (`judge/`) ships three providers (Anthropic, Gemini, OpenAI) and is wired into four checks:

1. `accuracy.description-accuracy` (2.2)
2. `discoverability.description-clarity` (3.1)
3. `discoverability.semantic-overlap` (3.2)
4. `security.prompt-injection-surface` (6.1 — LLM layer on top of existing regex; regex still fires standalone when the judge is off)

Wiring conventions: each check reads `config.judge` (a `SyncJudgeAdapter` or `None`) and emits a `SkippedCheck` with reason="LLM judge not enabled" when it's off. The CLI's `audit` and `replay` commands build the adapter unless `--no-llm-judges` is passed. Mock-judge coverage lives in `tests/test_evaluators/test_judge_integration.py`. End-to-end with a real provider against the harare fixtures has not been run yet — open task.

---

## Prod2 (Active Checker)

### Native tool-use APIs for agent loop

The agent loop should use native tool-use/function-calling APIs from the LLM provider, not the current approach of prompting the model to emit JSON with tool calls as text. This produces more reliable signal about the MCP server itself, and matches how agents actually use MCP in production.

The `JudgeProvider` interface needs a second method that accepts tool definitions and returns tool-call decisions, or the agent loop gets its own provider abstraction.

**Deferred to v2**: Text-prompt mode as an optional `--strict-discovery` flag — a harder discoverability test where the model gets no structured tool-use scaffolding.

### Model dependency

Default to a mid-tier model (flash-class). Document that the grade reflects usability for that model class. Users can re-run with a different model. No cross-model normalization.

---

## Log Ingestion

### Supported formats

The file loader handles NDJSON (one JSON object per line) and JSON arrays. Each record can be a raw JSON-RPC message or a wrapper with `direction`, `timestamp`, `session_id`, and `message` fields.

### Converter strategy

Document the expected log format thoroughly in `docs/log-format.md`. Ship converter scripts (or a `reviewmymcp convert` command) for the 2-3 most common sources (Claude Desktop logs, Python SDK debug output). Don't try to auto-detect every format in the parser.

### Log format standardization

Not pursuing formal standardization. Document the format well and let adoption happen organically.

---

## Dependencies

### LLM SDKs bundled

`anthropic`, `google-genai`, and `openai` are hard dependencies in `pyproject.toml`. Not splitting into optional extras for now.

---

## What's deferred

| Item | Status |
|------|--------|
| Prod1/prod2 code sharing | Merged 2026-04-29 (single tree on `rogue-socket/mcp-audit-plan`) |
| LLM judge wiring | Done 2026-04-29; real-provider end-to-end run still pending |
| Synthetic traffic path validation | Done 2026-05-16 — `reviewmymcp audit "npx -y @modelcontextprotocol/server-everything"` runs end-to-end; 7/9 dims match replay grades on same server, 2 diverge by design (probe-counting checks see more aggressive synth probes) |
| Text-prompt agent mode (strict discovery) | v2 |
| `diff` command schema stability | After scoring model settles |
| JSON report schema stability | After scoring model settles |
| Cross-model normalization for prod2 | Not planned |
| Overall server grade | Not planned |

---

## Critical Path to Working Prod1

1. ~~Feed real/user logs into prod1 — fix ingest pipeline~~ (done — harare fixtures replay cleanly)
2. ~~Validate evaluators produce sensible findings against real data~~ (done — calibration tests pin the distribution; probe filtering removed the residual artifacts)
3. ~~Update grading to numeric scores~~ (done 2026-05-15 — switched to `100 − √min(raw,100) × 5`; per-dimension denominators dropped)
4. ~~Add `checks_skipped` to evaluator results~~ (done — used widely)
5. ~~Verify terminal report output is readable and actionable~~ (done — rich-based renderer ships in `reporting/`)
6. ~~Wire up LLM judges~~ (done in code + mock-judge tests; real-provider e2e pending)
7. ~~Validate synthetic traffic path~~ (done 2026-05-16 against `server-everything`; pipeline + grades both verified)
8. ~~Tackle prod2~~ (active/ module merged; native tool-use; `active-audit` CLI ships with `--readonly`/`--allow-mutations`/`--trace-file` safety guards as of 2026-05-16)

Open: 6 (real-provider judge e2e). Cosmetic: subprocess child-watcher cleanup race in `synthetic/agent_driver.py:stop` emits a "Loop ... is closed" message after the run finishes — doesn't affect exit code or output, but ugly for CI logs.
