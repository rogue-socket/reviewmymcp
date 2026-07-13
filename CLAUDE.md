# reviewmymcp

Log-driven MCP server audit and evaluation tool.

## Tech Stack

- **Language**: Python 3.12+
- **Package management**: `pyproject.toml` (hatchling build backend)
- **CLI**: Click
- **Data models**: Pydantic v2
- **HTTP**: httpx (client), starlette + uvicorn (proxy server)
- **LLM judges**: Multi-provider — anthropic, google-genai, openai SDKs
- **Testing**: pytest (with pytest-asyncio, `asyncio_mode = "auto"`)
- **Linting/formatting**: ruff (line-length 120, target py312)

## Project Layout

```
src/reviewmymcp/
  __init__.py
  cli.py                      # Click entry points: audit, replay, diff, list-checks, watch, convert, active-audit
  config.py                   # config loading (AuditConfig, ScoringConfig)
  ingest/
    schema.py                 # McpEvent, ServerMeta, Direction, Transport (Pydantic v2)
    parser.py                 # JSON-RPC envelope parsing
    normalizer.py             # raw record -> McpEvent
    redactor.py               # PII/secret redaction (BUILTIN_PATTERNS, redact_dict, redact_string)
    file_loader.py            # NDJSON / JSON-array log loader
    correlator.py             # request/response pairing
    converter.py              # LogConverter protocol, ConverterRegistry, PassthroughConverter + stubs
  proxy/
    stdio_proxy.py            # transparent stdio proxy
    http_proxy.py             # starlette-based HTTP proxy
  evaluators/
    base.py                   # Evaluator protocol, EvaluatorConfig, EvaluatorResult, Finding, Severity
    registry.py               # register / get_by_dimension / get_all / run_all
    efficiency/checks.py      # EfficiencyEvaluator
    accuracy/checks.py        # AccuracyEvaluator
    discoverability/checks.py # DiscoverabilityEvaluator
    composability/checks.py   # ComposabilityEvaluator
    reliability/checks.py     # ReliabilityEvaluator
    security/checks.py        # SecurityEvaluator
    compliance/checks.py      # ComplianceEvaluator
    conformance/checks.py     # ConformanceEvaluator
    performance/checks.py     # PerformanceEvaluator
  scoring/
    grader.py                 # numeric 0-100 scoring + letter grades (A/B/C/D/F) with hard-cap overrides
    differ.py                 # report-to-report regression diffing
  reporting/
    terminal.py               # rich terminal output
    json_report.py
    html_report.py            # jinja2 templates under reporting/templates/
    sarif_report.py
  synthetic/
    scenario_planner.py
    agent_driver.py
    edge_probes.py
    load_generator.py
  active/
    models.py                 # TaskCategory, BehavioralSignal, ActiveTask, TaskExecution, ActiveAuditReport
    agent_loop.py             # AgentLoop: bridges LLM native tool-use <-> MCP driver
    task_generator.py         # LLM + deterministic fallback task generation (6 categories)
    signal_extractor.py       # behavioral signal extraction from agent turns
    scorer.py                 # 5-dimension scoring for active audit
  judge/
    base.py                   # JudgeProvider protocol, AgentProvider protocol, AgentTurnResponse
    prompts.py
    anthropic_judge.py
    gemini_judge.py
    openai_judge.py
    anthropic_agent.py        # AnthropicAgentProvider (native tool-use)
    openai_agent.py           # OpenAIAgentProvider (function calling)
    gemini_agent.py           # GeminiAgentProvider (function declarations)
tests/                        # mirrors src layout; fixtures/sample_stdio_log.ndjson
```

## CLI Commands

Entry point: `reviewmymcp` (defined in `[project.scripts]`).

- `audit [TARGET] [--log-file ...] [--transport stdio|http] ...` — run evaluators against live traffic or captured logs.
- `replay LOG_FILE [--output FORMAT] [--output-file ...]` — replay a log and emit a report.
- `diff BASELINE CURRENT` — compare two audit reports for regressions.
- `list-checks` — enumerate all registered evaluator checks.
- `watch TARGET [--output-dir ...] [--transport ...] [--no-redact]` — proxy + continuous capture.
- `convert INPUT_FILE [--format ...] [--output-file ...]` — convert foreign log formats to canonical NDJSON.
- `active-audit TARGET [--agent-provider ...] [--max-turns ...] [--categories ...]` — agent-driven usability testing.

## Key Conventions

- All data models use Pydantic v2 (`BaseModel`).
- Evaluators implement the `Evaluator` protocol in `evaluators/base.py` (attribute `dimension: str`, method `evaluate(events, server_meta, config) -> EvaluatorResult`).
- Each evaluator dimension lives in `evaluators/<dimension>/checks.py` exporting a `<Dimension>Evaluator` class.
- Evaluators are registered explicitly via `_register_evaluators()` in `cli.py` (no auto-discovery); duplicate registration raises `ValueError` and is caught.
- Evaluators are stateless: input is `list[McpEvent]` + `ServerMeta` + `EvaluatorConfig`, output is `EvaluatorResult` with `checks_run`, `findings`, and `checks_skipped`.
- Findings carry `check_id` (`"<dimension>.<check-name>"`), `Severity` (CRITICAL/HIGH/MEDIUM/LOW/INFO), `title`, `description`, `evidence` dict, `remediation`.
- Evaluators report `SkippedCheck(check_id, reason)` when a check can't run due to insufficient data.
- LLM judge evaluators are always optional — the tool must produce useful results without them (`--no-llm-judges`).
- Use `async`/`await` for I/O-bound operations (proxy, HTTP, LLM calls).
- Type hints everywhere. No `Any` unless unavoidable. Use `from __future__ import annotations`.

## Architecture Notes

- The canonical internal data type is `McpEvent` (`ingest/schema.py`). All ingestion paths normalize to it. Properties include `is_request`, `is_response`, `is_notification`, `is_error`, plus `method`, `error`, `raw_message`, `http_headers`, `direction`, `session_id`, `timestamp`, `redacted_fields`.
- PII redaction happens at ingest time (`file_loader.load_file(..., redact=True)`), before events reach evaluators. `BUILTIN_PATTERNS` covers JWT, AWS keys, GitHub tokens, emails, SSNs, connection strings, bearer tokens; sensitive keys (`authorization`, etc.) are redacted as `[REDACTED:header]`. Extra patterns are tagged `[REDACTED:custom]`.
- The judge abstraction (`judge/base.py`) defines a `JudgeProvider` protocol with implementations for Anthropic, Gemini, and OpenAI.
- The agent abstraction (`judge/base.py`) defines an `AgentProvider` protocol for LLM native tool-use, with implementations for Anthropic, OpenAI, and Gemini.
- Scoring uses numeric 0-100 per dimension with severity-weighted deductions (critical=25, high=10, medium=4, low=1) normalized by tool count or call count. Hard-cap overrides: 1 critical → D, 2+ criticals → F.
- Conformance checks reference standard JSON-RPC error codes `{-32700, -32600, -32601, -32602, -32603, -32042}` plus the server-defined range `-32099..-32000`.

## Commands

```
ruff check src/ tests/         # lint
ruff format src/ tests/        # format
pytest                         # run all tests
pytest tests/test_evaluators/  # run evaluator tests only
```

## Branches & Worktrees

| Branch | Worktree | Purpose |
|--------|----------|---------|
| `main` | `~/Documents/reviewmymcp` | Root. Contains `DECISIONS.md` (design decisions from review session). |
| `rogue-socket/mcp-audit-plan` | `rio-de-janeiro` | **Main codebase.** Prod1 (log-based audit) + Prod2 (active agent-driven testing) merged here. |
| `rogue-socket/active-mcp-checker` | `curitiba` | **Prod2 prototype.** Earlier standalone active checker (text-prompt agent mode). Has `runs/REPORT.md` with empirical results from 4 real MCP servers. |
| `rogue-socket/mcp-log-collector` | `harare` | Dev utility for capturing NDJSON from real servers. Not user-facing. |

### Key docs across branches

| Doc | Branch | What it is |
|-----|--------|------------|
| `DECISIONS.md` | `main` | Design decisions — two products, numeric scoring, per-dimension denominators, no overall grade, native tool-use, deferred items. Source of truth for "why." |
| `README.md` | `mcp-audit-plan` | User-facing docs — all 7 CLI commands, 42 checks, scoring formula, CI integration, config. |
| `PLAN.md` | `mcp-audit-plan` | Original PRD/design doc — problem framing, milestone breakdown, evaluator specs. |
| `CHANGELOG-scoring-rewrite.md` | `mcp-audit-plan` | What changed in the scoring rewrite and why. |
| `TESTING.md` | `mcp-audit-plan` | Manual testing guide — 10 sections, copy-paste commands, evaluation checklist. |
| `docs/log-format.md` | `mcp-audit-plan` | Log format spec — NDJSON/JSON, wrapper fields, McpEvent schema, PII redaction. |
| `docs/adding-evaluators.md` | `mcp-audit-plan` | Contributor guide — how to write new evaluator checks. |
| `runs/REPORT.md` | `active-mcp-checker` | Comparative test report — active checker results against filesystem, GitHub, puppeteer, web-search servers (Gemini 2.5 Flash). Includes the GitHub PAT incident. |
| `README.md` | `active-mcp-checker` | Prod2 prototype docs — architecture diagram, pipeline, behavioral signals. Diverged from rio-de-janeiro (text-prompt vs native tool-use). |

## Implementation Status

All nine evaluator dimensions, ingestion (file loader + redactor + normalizer + correlator + converter), proxies (stdio + http), scoring (grader + differ), reporting (terminal/json/html/sarif), synthetic traffic, multi-provider judges, active audit module (agent loop + task generator + signal extractor + scorer), and the full CLI surface (7 commands) are implemented with 267 tests under `tests/`. See `PLAN.md` for the full design and milestone breakdown.
