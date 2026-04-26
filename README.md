# reviewmymcp — Active MCP Checker

Agent-driven usability testing for MCP servers. Instead of analyzing logs (passive mode) or scripting tool calls (synthetic mode), the active checker puts a real LLM agent in front of your MCP server, gives it tasks, and observes how well the server supports the agent's workflow.

Think of it as a **usability interview** for your MCP server — the agent is the test subject, and the server's design is what's being evaluated.

## How It Works

```
                    ┌─────────────┐
                    │  Task       │  Generates realistic tasks
                    │  Library    │  (LLM-planned or fallback)
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │  Agent      │  LLM agent reasons about
                    │  Loop       │  which tools to call and how
                    └──────┬──────┘
                           │
              ┌────────────▼────────────┐
              │  MCP Server (stdio)     │  Real tool calls
              │  via StdioAgentDriver   │  over JSON-RPC
              └────────────┬────────────┘
                           │
                    ┌──────▼──────┐
                    │  Observer   │  Extracts behavioral signals
                    │             │  from each session
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │  Scorer     │  Maps signals → Findings
                    │             │  → A-F graded report
                    └─────────────┘
```

The pipeline:

1. **Connect** to the MCP server and discover its tools
2. **Generate tasks** across 6 categories (discovery, single-tool, multi-step, error recovery, ambiguous, edge case)
3. **Run an LLM agent** through each task — it decides which tools to call, how to build arguments, and how to handle errors
4. **Observe** the agent's behavior: did it find the right tool? struggle with arguments? recover from errors? successfully chain calls?
5. **Score** the observations into findings across 5 dimensions (discoverability, reliability, composability, efficiency, accuracy)
6. **Grade** using the same A-F grading engine as the passive audit

## Setup

### Prerequisites

- Python 3.12+
- An LLM API key (Anthropic, Google Gemini, or OpenAI)

### Install

```bash
# From the repo root (where pyproject.toml lives)
pip install -e ".[dev]"
```

### Environment

Set the API key for your chosen LLM provider:

```bash
# Pick one:
export ANTHROPIC_API_KEY="sk-ant-..."
export GOOGLE_API_KEY="AIza..."
export OPENAI_API_KEY="sk-..."
```

The active checker uses the LLM for two purposes:
- **Task generation** — planning realistic test scenarios from the tool definitions
- **Test agent** — the LLM that actually uses the MCP tools during the test

## Usage

### CLI

```bash
# Basic active audit against a stdio MCP server
reviewmymcp active-audit "python -m my_mcp_server"

# Use Gemini as the LLM provider
reviewmymcp active-audit "npx @modelcontextprotocol/server-filesystem /tmp" \
  --judge-provider gemini --judge-model gemini-2.5-flash

# JSON output for CI
reviewmymcp active-audit "python -m my_server" \
  --output json --output-file active-report.json

# HTML report
reviewmymcp active-audit "python -m my_server" \
  --output html --output-file report.html

# Control number of test tasks
reviewmymcp active-audit "python -m my_server" --task-count 12

# Disable PII redaction
reviewmymcp active-audit "python -m my_server" --no-redact
```

### Options

| Flag | Description |
|------|-------------|
| `--judge-provider` | `anthropic` \| `gemini` \| `openai` (default: `anthropic`) |
| `--judge-model` | Override model name (e.g. `gemini-2.5-flash`, `claude-haiku-4-5-20251001`) |
| `--task-count N` | Number of test tasks to generate (default: 8) |
| `--output FORMAT` | `terminal` \| `json` \| `html` \| `sarif` (default: `terminal`) |
| `--output-file PATH` | Write report to file |
| `--config PATH` | Config file (same format as passive audit) |
| `--no-redact` | Disable PII redaction on captured traffic |

### Programmatic API

```python
import asyncio
from reviewmymcp.active.runner import run_active_check
from reviewmymcp.judge.gemini_judge import GeminiJudge

async def main():
    judge = GeminiJudge(model="gemini-2.5-flash")
    result = await run_active_check(
        server_command=["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        judge=judge,
        task_count=6,
    )
    print(f"Grade: {result.report.overall_grade.value}")
    print(f"Completed: {result.summary['completed']}/{result.summary['total_tasks']}")

    for ds in result.report.dimension_scores:
        print(f"  {ds.dimension}: {ds.grade.value}")

asyncio.run(main())
```

### Integration with the CLI

To register the `active-audit` command alongside the existing CLI, add this to `cli.py`:

```python
from reviewmymcp.active.cli import active_audit
cli.add_command(active_audit)
```

## Task Categories

The active checker generates tasks across 6 categories to probe different aspects of MCP quality:

| Category | What it tests | Example |
|----------|---------------|---------|
| **Discovery** | Can the agent figure out what the server offers? | "Explore what this server can do" |
| **Single tool** | Can the agent find and correctly call one tool? | "Read the contents of config.json" |
| **Multi-step** | Can the agent chain tool calls together? | "Search for .log files, then read the newest one" |
| **Error recovery** | Can the agent recover when a call fails? | "Read a file that doesn't exist and handle the error" |
| **Ambiguous** | Does the agent pick the right tool when descriptions overlap? | "I need to see the contents of this file" (read_file vs read_text_file) |
| **Edge case** | How does the server handle unusual inputs? | "Edit a file with multiple changes using dry-run mode" |

With an LLM provider configured, tasks are generated dynamically based on the actual tool definitions. Without one, deterministic fallback tasks are used.

## Behavioral Signals

The observer watches each agent session and extracts 16 behavioral signals:

### Positive signals (things going well)

| Signal | Meaning |
|--------|---------|
| `tool_found` | Agent found and used the expected tool |
| `correct_args_first_try` | Called the tool with valid arguments on the first attempt |
| `error_recovered` | Hit an error, understood it, and successfully retried |
| `task_completed` | Finished the task within the turn limit |
| `chaining_success` | Successfully chained multiple tools together |

### Negative signals (problems with the MCP)

| Signal | Meaning | Maps to |
|--------|---------|---------|
| `tool_not_found` | Agent couldn't find the expected tool | discoverability |
| `wrong_tool_chosen` | Agent used a different tool than expected | discoverability |
| `arg_struggle` | Needed multiple attempts to get arguments right | discoverability |
| `error_not_recovered` | Hit an error and couldn't continue | reliability |
| `unhelpful_error` | Server returned a vague/generic error message | reliability |
| `gave_up` | Agent abandoned the task entirely | composability |
| `task_failed` | Exhausted the turn limit without finishing | composability |
| `excessive_turns` | Completed but used nearly all available turns | efficiency |
| `chaining_failure` | Couldn't chain expected tools together | composability |
| `tool_confusion` | Confused about which tool to use | discoverability |
| `description_mismatch` | Tool behavior didn't match its description | accuracy |

## Scoring

Negative signals are mapped to findings with severity levels, grouped by dimension:

| Dimension | What's evaluated |
|-----------|-----------------|
| **Discoverability** | Can agents find and understand the tools? |
| **Reliability** | Do tools work consistently? Are errors helpful? |
| **Composability** | Can tools be chained? Can agents complete workflows? |
| **Efficiency** | How many turns does the agent need? |
| **Accuracy** | Does tool behavior match descriptions? |

Grading uses the same engine as the passive audit:

| Grade | Criteria |
|-------|----------|
| **A** | No critical or high findings. At most 2 medium. |
| **B** | No critical. At most 2 high. |
| **C** | No critical. 3+ high or 5+ medium. |
| **D** | 1 critical, or 5+ high. |
| **F** | 2+ critical. |

## Active vs Passive: When to Use Which

| | Passive (`audit --log-file`) | Active (`active-audit`) |
|---|---|---|
| **Input** | Captured log files | Live MCP server |
| **What it measures** | Protocol correctness, performance, security | Agent usability, discoverability, workflow support |
| **Dimensions** | 9 (42 checks) | 5 (agent-behavioral) |
| **LLM required** | Optional (4 judge checks) | Required (agent + task generation) |
| **Best for** | CI gating, regression detection, compliance | Design feedback, UX evaluation, pre-release testing |
| **Analogy** | Code review | User testing |

They're complementary — run passive for protocol-level correctness, active for agent-level usability.

## Module Structure

```
src/reviewmymcp/active/
  __init__.py        # Package marker
  task_library.py    # Task generation (LLM-planned + deterministic fallback)
  agent_loop.py      # LLM agent execution loop with tool call recording
  observer.py        # Behavioral signal extraction from sessions
  scorer.py          # Signal → Finding → AuditReport conversion
  runner.py          # Pipeline orchestrator
  cli.py             # `active-audit` Click command
```

---

## Test Results: Filesystem MCP Server

We ran the active checker against `@modelcontextprotocol/server-filesystem` v0.2.0 (14 tools) using `gemini-2.5-flash` as the test agent. Here are the findings.

### Summary

| Metric | Value |
|--------|-------|
| Overall grade | **C** |
| Tasks completed | 2/6 (33%) |
| Avg turns per task | 3.5 |
| Positive signals | 28 |
| Negative signals | 13 |

### Dimension Scores

| Dimension | Grade | Findings |
|-----------|-------|----------|
| Discoverability | C | 7 medium |
| Composability | B | 4 medium |
| Efficiency | A | 2 low |
| Reliability | A | 0 |
| Accuracy | A | 0 |

### Task Results

| # | Category | Task | Result | Turns |
|---|----------|------|--------|-------|
| 1 | Discovery | Explore server capabilities | Failed | 3/3 |
| 2 | Single tool | Create a directory (`create_directory`) | Failed | 2/2 |
| 3 | Multi-step | Search for .log files, then read them | **Passed** | 5/5 |
| 4 | Error recovery | Read non-existent file, handle error | **Passed** | 7/7 |
| 5 | Ambiguous | Read a file (choice between overlapping tools) | Failed | 2/2 |
| 6 | Edge case | Multi-edit with dry-run flag | Failed | 2/2 |

### Key Findings

**1. Discoverability is the weakest dimension (Grade C)**

The filesystem server has 14 tools, several of which overlap significantly:
- `read_file` (deprecated) vs `read_text_file` — same functionality, confusing for the agent
- `list_directory` vs `list_directory_with_sizes` vs `directory_tree` — three ways to list files
- The agent consistently called `list_allowed_directories` as a preamble step, burning a turn figuring out what paths are valid before doing actual work

**2. The agent always calls `list_allowed_directories` first**

In every session, the agent's first action was to check which directories it's allowed to access. This is rational behavior — the server restricts paths — but it means every task costs an extra turn. The server could improve by including the allowed directories in its `instructions` field during initialization, so the agent knows upfront.

**3. Deprecated tools cause confusion**

`read_file` is marked as deprecated in its description ("DEPRECATED: Use read_text_file instead"), but it's still listed as an available tool. The agent sometimes picked it anyway. Removing deprecated tools from the `tools/list` response (or at minimum moving the deprecation notice to a prominent position) would reduce confusion.

**4. Error recovery works well**

The error recovery task (reading a non-existent file) passed. The agent received a clear error, understood it, listed the directory to find valid files, and retried successfully. This is a strength — error messages from the filesystem server are specific and actionable.

**5. Multi-step chaining works but is slow**

The multi-step task (search then read) completed, but used all 5 available turns. The agent needed to: (1) check allowed dirs, (2) list the directory, (3) search for files, (4) read the result, (5) report. The overhead of the `list_allowed_directories` preamble compounds with multi-step workflows.

**6. LLM-generated tasks were well-targeted**

Gemini 2.5 Flash generated contextually relevant tasks that tested real filesystem operations: creating directories, searching by glob pattern, editing with dry-run mode, and reading files with ambiguous tool choices. The task generation quality was high.

### Recommendations for the Filesystem MCP Server

1. **Remove deprecated tools** from `tools/list`, or at least don't surface `read_file` alongside `read_text_file`
2. **Include allowed directories in `instructions`** during `initialize` so agents don't need a preamble call
3. **Consolidate listing tools** — `list_directory`, `list_directory_with_sizes`, and `directory_tree` could be a single tool with options
4. **Increase turn budgets** for LLM-generated tasks, or design tools that require fewer round-trips

### Raw Signal Breakdown

```
Positive signals (28):
  tool_found ×6          — agent found the expected tool
  correct_args_first_try ×12  — correct arguments on first attempt
  error_recovered ×4     — recovered after errors
  task_completed ×2      — finished within turn limit
  chaining_success ×1    — chained search → read successfully

Negative signals (13):
  wrong_tool_chosen ×5   — used unexpected tools (mostly list_allowed_directories)
  tool_not_found ×3      — didn't use the expected tool at all
  task_failed ×4         — hit turn limit
  excessive_turns ×2     — completed but barely
  arg_struggle ×1        — needed 2 attempts for read_text_file
```
