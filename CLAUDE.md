# reviewmymcp

Log-driven MCP server audit and evaluation tool.

## Tech Stack

- **Language**: Python 3.12+
- **Package management**: `pyproject.toml` with pip/uv
- **CLI**: Click
- **Data models**: Pydantic v2
- **HTTP**: httpx (client), starlette + uvicorn (proxy server)
- **LLM judges**: Multi-provider — anthropic, google-genai, openai SDKs
- **Testing**: pytest
- **Linting/formatting**: ruff

## Project Layout

```
src/reviewmymcp/          # main package
  cli.py                  # Click entry points
  config.py               # config loading
  ingest/                 # log ingestion, parsing, normalization, redaction
  proxy/                  # stdio and HTTP transparent proxies
  evaluators/             # pluggable evaluators (one subpackage per dimension)
    base.py               # Evaluator protocol, Finding, EvaluatorResult
    registry.py           # evaluator discovery
    efficiency/
    accuracy/
    discoverability/
    composability/
    reliability/
    security/
    compliance/
    conformance/
    performance/
  scoring/                # grading engine, report diffing
  reporting/              # terminal, JSON, HTML, SARIF output
  synthetic/              # traffic generation, load testing
  judge/                  # LLM judge abstraction (multi-provider)
tests/                    # mirrors src layout
```

## Key Conventions

- All data models use Pydantic v2 (`BaseModel`).
- Evaluators implement the `Evaluator` protocol defined in `evaluators/base.py`.
- Each evaluator is a single file in its dimension subpackage.
- LLM judge evaluators are always optional — the tool must produce useful results without them (`--no-llm-judges`).
- Use `async`/`await` for I/O-bound operations (proxy, HTTP, LLM calls).
- Type hints everywhere. No `Any` unless unavoidable.

## Commands

```
ruff check src/ tests/        # lint
ruff format src/ tests/       # format
pytest                        # run all tests
pytest tests/test_evaluators/ # run evaluator tests only
```

## Architecture Notes

- The canonical internal data type is `McpEvent` (defined in `ingest/schema.py`). All ingestion paths normalize to this.
- Evaluators are stateless: they receive `list[McpEvent]` + `ServerMeta` + config, and return `EvaluatorResult`.
- The judge abstraction (`judge/base.py`) defines a `JudgeProvider` protocol with implementations for Anthropic, Gemini, and OpenAI.
- PII redaction happens at ingest time, before events reach evaluators.
