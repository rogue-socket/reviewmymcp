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
  cli.py                      # Click entry points: audit, replay, diff, list-checks, watch
  config.py                   # config loading
  ingest/
    schema.py                 # McpEvent, ServerMeta, Direction, Transport (Pydantic v2)
    parser.py                 # JSON-RPC envelope parsing
    normalizer.py             # raw record -> McpEvent
    redactor.py               # PII/secret redaction (BUILTIN_PATTERNS, redact_dict, redact_string)
    file_loader.py            # NDJSON / JSON-array log loader
    correlator.py             # request/response pairing
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
    grader.py                 # severity-weighted letter grading
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
  judge/
    base.py                   # JudgeProvider protocol
    prompts.py
    anthropic_judge.py
    gemini_judge.py
    openai_judge.py
tests/                        # mirrors src layout; fixtures/sample_stdio_log.ndjson
```

## CLI Commands

Entry point: `reviewmymcp` (defined in `[project.scripts]`).

- `audit [TARGET] [--log-file ...] [--transport stdio|http] ...` — run evaluators against live traffic or captured logs.
- `replay LOG_FILE [--output FORMAT] [--output-file ...]` — replay a log and emit a report.
- `diff BASELINE CURRENT` — compare two audit reports for regressions.
- `list-checks` — enumerate all registered evaluator checks.
- `watch TARGET [--output-dir ...] [--transport ...] [--no-redact]` — proxy + continuous capture.

## Key Conventions

- All data models use Pydantic v2 (`BaseModel`).
- Evaluators implement the `Evaluator` protocol in `evaluators/base.py` (attribute `dimension: str`, method `evaluate(events, server_meta, config) -> EvaluatorResult`).
- Each evaluator dimension lives in `evaluators/<dimension>/checks.py` exporting a `<Dimension>Evaluator` class.
- Evaluators are registered explicitly via `_register_evaluators()` in `cli.py` (no auto-discovery); duplicate registration raises `ValueError` and is caught.
- Evaluators are stateless: input is `list[McpEvent]` + `ServerMeta` + `EvaluatorConfig`, output is `EvaluatorResult` with `checks_run` and `findings`.
- Findings carry `check_id` (`"<dimension>.<check-name>"`), `Severity` (CRITICAL/HIGH/MEDIUM/LOW), `title`, `description`, `evidence` dict, `remediation`.
- LLM judge evaluators are always optional — the tool must produce useful results without them (`--no-llm-judges`).
- Use `async`/`await` for I/O-bound operations (proxy, HTTP, LLM calls).
- Type hints everywhere. No `Any` unless unavoidable. Use `from __future__ import annotations`.

## Architecture Notes

- The canonical internal data type is `McpEvent` (`ingest/schema.py`). All ingestion paths normalize to it. Properties include `is_request`, `is_response`, `is_notification`, `is_error`, plus `method`, `error`, `raw_message`, `http_headers`, `direction`, `session_id`, `timestamp`, `redacted_fields`.
- PII redaction happens at ingest time (`file_loader.load_file(..., redact=True)`), before events reach evaluators. `BUILTIN_PATTERNS` covers JWT, AWS keys, GitHub tokens, emails, SSNs, connection strings, bearer tokens; sensitive keys (`authorization`, etc.) are redacted as `[REDACTED:header]`. Extra patterns are tagged `[REDACTED:custom]`.
- The judge abstraction (`judge/base.py`) defines a `JudgeProvider` protocol with implementations for Anthropic, Gemini, and OpenAI.
- Conformance checks reference standard JSON-RPC error codes `{-32700, -32600, -32601, -32602, -32603, -32042}` plus the server-defined range `-32099..-32000`.

## Commands

```
ruff check src/ tests/         # lint
ruff format src/ tests/        # format
pytest                         # run all tests
pytest tests/test_evaluators/  # run evaluator tests only
```

## Implementation Status

All nine evaluator dimensions, ingestion (file loader + redactor + normalizer + correlator), proxies (stdio + http), scoring (grader + differ), reporting (terminal/json/html/sarif), synthetic traffic, multi-provider judges, and the full CLI surface are scaffolded with accompanying tests under `tests/`. See `PLAN.md` for the full design and milestone breakdown.
