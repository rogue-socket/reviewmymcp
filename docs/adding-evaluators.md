# Adding Evaluators

This guide covers how to write a new evaluator check for `reviewmymcp`.

## Architecture

Evaluators are organized by dimension. Each dimension is a subpackage under `src/reviewmymcp/evaluators/` with a `checks.py` file containing a single evaluator class.

```
evaluators/
  base.py                    # Evaluator protocol, Finding, Severity
  registry.py                # Registration and discovery
  efficiency/checks.py       # EfficiencyEvaluator
  accuracy/checks.py         # AccuracyEvaluator
  ...
```

Each evaluator class:
- Implements the `Evaluator` protocol
- Has a `dimension` attribute (string)
- Has an `evaluate()` method that returns an `EvaluatorResult`

## The Evaluator Protocol

```python
from reviewmymcp.evaluators.base import (
    Evaluator,
    EvaluatorConfig,
    EvaluatorResult,
    Finding,
    Severity,
)
from reviewmymcp.ingest.schema import McpEvent, ServerMeta


class MyEvaluator:
    dimension: str = "my_dimension"

    def evaluate(
        self,
        events: list[McpEvent],
        server_meta: ServerMeta,
        config: EvaluatorConfig,
    ) -> EvaluatorResult:
        findings: list[Finding] = []
        # ... run checks, append findings ...
        return EvaluatorResult(
            dimension=self.dimension,
            checks_run=["my_dimension.check-name"],
            findings=findings,
        )
```

## Finding Structure

Each check produces zero or more `Finding` objects:

```python
Finding(
    check_id="dimension.check-name",   # Unique, dot-separated
    severity=Severity.HIGH,             # CRITICAL, HIGH, MEDIUM, LOW, INFO
    title="One-line summary",           # What went wrong
    description="Detailed explanation", # With numbers and context
    evidence={"key": "value"},          # Concrete data supporting the finding
    remediation="How to fix it",        # Actionable advice
    affected_entity="tool_name",        # What is affected (tool name, task ID, etc.)
)
```

## Key Data Structures

### McpEvent

The normalized event you receive. Key fields:

```python
event.method         # "tools/call", "initialize", etc. (None for responses)
event.is_request     # True for JSON-RPC requests
event.is_response    # True for JSON-RPC responses
event.is_notification # True for notifications
event.is_error       # True for error responses
event.params         # Request/notification params dict
event.result         # Success response result dict
event.error          # Error response error dict
event.latency_ms     # Time between request and response (on responses only)
event.request_event_id  # Links responses back to requests
event.session_id     # Groups events into sessions
event.raw_size_bytes # Wire size
```

### ServerMeta

Extracted from the initialize handshake:

```python
server_meta.server_name          # "MyServer"
server_meta.server_version       # "1.0.0"
server_meta.protocol_version     # "2025-11-25"
server_meta.tools                # list[ToolDefinition]
server_meta.server_capabilities  # ServerCapabilities
server_meta.client_capabilities  # ClientCapabilities
```

### ToolDefinition

```python
tool.name           # "search_users"
tool.description    # "Search for users..."
tool.input_schema   # JSON Schema dict
tool.annotations    # Tool annotations dict
tool.execution      # Execution config (taskSupport, etc.)
```

## Common Patterns

### Matching requests to responses

```python
# Build a map of request event_id -> request event for tools/call
requests = {e.event_id: e for e in events if e.is_request and e.method == "tools/call"}

# Find matching responses
for event in events:
    if event.is_response and event.request_event_id in requests:
        req = requests[event.request_event_id]
        tool_name = req.params.get("name", "") if req.params else ""
        # ... analyze req + response pair
```

### Grouping by session

```python
from collections import defaultdict

sessions: dict[str | None, list[McpEvent]] = defaultdict(list)
for event in events:
    sessions[event.session_id].append(event)
```

### Checking tool call success

```python
def is_success(resp: McpEvent) -> bool:
    return not resp.is_error and (resp.result is None or not resp.result.get("isError"))
```

### Using configurable thresholds

```python
threshold = config.thresholds.get("my_check_threshold", 500)  # default 500
```

## Adding a Check to an Existing Dimension

1. Open the relevant `checks.py` (e.g., `evaluators/efficiency/checks.py`)
2. Add a new `_check_*` method to the evaluator class
3. Call it from `evaluate()` and add the check ID to `checks_run`
4. The check is automatically included in the next run

Example:

```python
class EfficiencyEvaluator:
    dimension: str = "efficiency"

    def evaluate(self, events, server_meta, config) -> EvaluatorResult:
        findings = []
        # ... existing checks ...
        findings.extend(self._check_my_new_thing(events, config))

        return EvaluatorResult(
            dimension=self.dimension,
            checks_run=[
                # ... existing check IDs ...
                "efficiency.my-new-check",
            ],
            findings=findings,
        )

    def _check_my_new_thing(self, events, config) -> list[Finding]:
        findings = []
        # ... detection logic ...
        if problem_detected:
            findings.append(Finding(
                check_id="efficiency.my-new-check",
                severity=Severity.MEDIUM,
                title="Something is wrong",
                description="Detailed explanation with numbers.",
                evidence={"metric": value},
                remediation="Do this to fix it.",
                affected_entity="tool_name",
            ))
        return findings
```

## Adding a New Dimension

1. Create `src/reviewmymcp/evaluators/my_dimension/__init__.py` (empty)
2. Create `src/reviewmymcp/evaluators/my_dimension/checks.py` with your evaluator class
3. Register it in `src/reviewmymcp/cli.py` — add the import and append to the registration list in `_register_evaluators()`

```python
# In cli.py _register_evaluators():
from reviewmymcp.evaluators.my_dimension.checks import MyDimensionEvaluator

for cls in [
    # ... existing evaluators ...
    MyDimensionEvaluator,
]:
    try:
        register(cls())
    except ValueError:
        pass
```

## LLM Judge Checks

For checks that require semantic analysis (description quality, overlap detection, prompt injection):

1. Add prompt templates to `src/reviewmymcp/judge/prompts.py`
2. In your check method, use the judge provider from config:

```python
from reviewmymcp.judge.base import JudgeRequest

async def _check_with_judge(self, server_meta, config):
    # Only run if judges are available
    if not config.judge_provider:
        return []

    # Build the judge — provider selection happens at the CLI level
    # Your check receives the events and server_meta, not the judge directly
    # For now, LLM judge checks are placeholders awaiting async integration
    return []
```

Judge checks should always be optional — the tool must produce useful results without them.

## Testing

Each evaluator should have tests in `tests/test_evaluators/`. Create fixture events using the schema models directly:

```python
from datetime import UTC, datetime
from reviewmymcp.ingest.schema import McpEvent, ServerMeta, ToolDefinition, Transport, Direction

def make_event(**kwargs) -> McpEvent:
    defaults = {
        "event_id": "test-1",
        "timestamp": datetime.now(UTC),
        "transport": Transport.STDIO,
        "direction": Direction.CLIENT_TO_SERVER,
    }
    return McpEvent(**{**defaults, **kwargs})

def test_my_check():
    events = [
        make_event(method="tools/call", is_request=True, params={"name": "test", "arguments": {}}),
        # ...
    ]
    server_meta = ServerMeta(tools=[ToolDefinition(name="test", description="Test tool")])

    evaluator = MyEvaluator()
    result = evaluator.evaluate(events, server_meta, EvaluatorConfig())

    assert len(result.findings) == 1
    assert result.findings[0].check_id == "my_dimension.my-check"
```

## Severity Guidelines

| Severity | When to use |
|----------|-------------|
| **CRITICAL** | Security vulnerabilities, data loss, protocol violations that break clients |
| **HIGH** | Significant quality issues that directly impact model performance or reliability |
| **MEDIUM** | Quality issues that degrade experience but don't break functionality |
| **LOW** | Stylistic or minor issues, opportunities for improvement |
| **INFO** | Informational observations, no action needed |

Each check should justify its severity with a real failure mode it catches. Avoid CRITICAL for anything that isn't a security or correctness issue.
