# Scoring System Rewrite — Changelog

> Historical note: this document describes an intermediate denominator-normalized scoring model. The current model uses a square-root curve with no tool/call denominator; see the README and `scoring/grader.py` for the live contract.

Date: 2026-04-29
Branch: `rogue-socket/mcp-audit-plan`

---

## Why

The original grading system was pure severity-count based. A server with 1 bad tool out of 50 got the same grade as a server with 1 bad tool out of 1. The grades didn't reflect how widespread problems were relative to the server's size. The system also produced a single "overall grade" that masked per-dimension detail.

## What Changed

### 1. `EvaluatorResult` gained `checks_skipped` field

**File:** `src/reviewmymcp/evaluators/base.py`

**Bug:** When an evaluator couldn't run a check due to insufficient data (e.g., only 1 session in the logs, so concurrent-session checks can't run), it silently returned zero findings. The report showed "A" for that dimension — indistinguishable from "we checked and it's fine."

**Change:** Added `SkippedCheck` model (`check_id: str`, `reason: str`) and `checks_skipped: list[SkippedCheck]` field to `EvaluatorResult`. Evaluators can now report which checks they skipped and why.

**Expected behavior:** Evaluators that lack sufficient data populate `checks_skipped` instead of silently passing. The report distinguishes "no findings" from "couldn't evaluate." Note: the 9 evaluator implementations don't emit `checks_skipped` yet — that's a follow-up task. The field is wired through the full pipeline (grader, reporter, JSON/HTML output).

### 2. Scoring engine rewritten to numeric 0-100 scores

**File:** `src/reviewmymcp/scoring/grader.py`

**Bug:** Grades were derived from raw severity counts with fixed thresholds (e.g., 3+ high findings = C). No normalization. A server with 50 tools and 3 high findings got the same grade as a server with 3 tools and 3 high findings.

**Change:** Complete rewrite. The grading engine now:

- **Computes a numeric score (0-100) per dimension.** Deductions are severity-weighted: critical=25, high=10, medium=4, low=1, info=0.
- **Normalizes deductions per dimension** using a per-dimension denominator:
  - **Tool count:** discoverability, efficiency, accuracy, security. A finding's weight is divided by the number of tools, so 1 problem across 50 tools has less impact than 1 problem across 3.
  - **Call count:** reliability, composability, performance. Same logic but normalized by how many tool calls were in the logs.
  - **Raw (no normalization):** conformance, compliance. These are binary protocol checks — you either follow the spec or you don't.
- **Derives letter grades from numeric scores:** A (90+), B (75+), C (60+), D (40+), F (<40).
- **Applies hard gate overrides:** 1 critical finding caps the dimension at D regardless of score. 2+ criticals forces F.
- **Removed `overall_grade`** from `AuditReport`. Per-dimension scores are the product. CI gating uses exit codes (exit 1 if any critical/high findings).

**New inputs to `grade_results()`:** `tool_count: int` and `call_count: int` alongside the existing `total_events` and `total_sessions`.

**Normalization formula:** For normalized dimensions: `score = max(0, 100 - (raw_deduction / denominator) * 4)`. The multiplier 4 means a per-unit deduction of 25 (one critical per tool) drives the score to 0. If the denominator is 0 (e.g., no tools discovered), falls back to raw scoring.

**`DimensionScore` model changes:**
- Added `score: float` (the 0-100 numeric score)
- Added `checks_skipped: list[SkippedCheck]`
- Added `denominator_type: str | None` ("tools", "calls", or None)
- Added `denominator_value: int` (the actual count used)

**`AuditReport` model changes:**
- Removed `overall_grade: Grade`
- Added `tool_count: int`
- Added `call_count: int`

### 3. Terminal reporter updated

**File:** `src/reviewmymcp/reporting/terminal.py`

**Change:**
- Dimension table now shows "Score" column (numeric) alongside the letter grade.
- Added "Skipped" column showing count of skipped checks per dimension.
- Added "Skipped Checks" detail section listing each skipped check with its reason.
- Header shows tool count and call count when available.
- Removed overall grade display.

**Expected behavior:** Running `reviewmymcp replay --log-file some.ndjson` now outputs a table like:

```
┌─────────────────┬───────┬───────┬──────────┬──────┬────────┬─────┬─────────┐
│ Dimension       │ Score │ Grade │ Critical │ High │ Medium │ Low │ Skipped │
├─────────────────┼───────┼───────┼──────────┼──────┼────────┼─────┼─────────┤
│ efficiency      │    98 │   A   │        - │    - │      1 │   - │       - │
│ security        │    75 │   D   │        1 │    - │      - │   - │       1 │
│ ...             │       │       │          │      │        │     │         │
└─────────────────┴───────┴───────┴──────────┴──────┴────────┴─────┴───────��─┘
```

### 4. Differ updated for numeric scores

**File:** `src/reviewmymcp/scoring/differ.py`

**Change:**
- `DimensionDiff` now carries `baseline_score` and `current_score` alongside the grades.
- `DiffReport` no longer has `baseline_grade`/`current_grade` (no overall grade).
- CLI diff output shows scores: `accuracy: A (92) → A (92)`.

**Expected behavior:** `reviewmymcp diff baseline.json current.json` shows per-dimension grade and score transitions. Regressions are flagged when the letter grade worsens.

### 5. HTML template updated

**File:** `src/reviewmymcp/reporting/templates/report.html.j2`

**Change:**
- Added Score column to dimension table.
- Added Skipped column.
- Added "Skipped Checks" section listing check IDs and reasons.
- Removed overall grade display.
- Header shows tool/call counts.

### 6. CLI updated to compute denominators

**File:** `src/reviewmymcp/cli.py`

**Change:** Both `audit` and `replay` commands now compute `tool_count` (from `server_meta.tools`) and `call_count` (count of `tools/call` request events) and pass them to `grade_results()`. The `diff` command output format updated to show scores.

---

### 7. All 9 evaluators now emit `checks_skipped`

**Files:** All `src/reviewmymcp/evaluators/*/checks.py`

**Bug:** When an evaluator skipped a check due to insufficient data (e.g., < 3 latency observations, < 3 sessions, no repeated calls), it silently returned zero findings. The report showed "A" for that dimension — indistinguishable from "we checked and it's fine."

**Change:** Every evaluator's `evaluate()` method now maintains a `skipped: list[SkippedCheck]` accumulator. Sub-check methods that have minimum-data guards append to it when they skip. One aggregate `SkippedCheck` per check_id (not per-tool) is emitted with a descriptive reason string.

**Skip points added (13 total):**

| Evaluator | Check | Condition | Reason |
|---|---|---|---|
| Efficiency | `latency-cliff` | < 3 latency observations per tool | `"fewer than 3 latency observations for {n} tool(s)"` |
| Efficiency | `token-cost-per-task` | no successful calls | `"no successful tool calls recorded"` |
| Conformance | `initialize-handshake` | sessions with ≤ 2 events | `"skipped {n} session(s) with <= 2 events"` |
| Reliability | `error-rate` | < 3 calls per tool | `"fewer than 3 calls for {n} tool(s)"` |
| Composability | `idempotency-violation` | < 2 repeated call groups with 2+ successes | `"fewer than 2 repeated call groups..."` |
| Composability | `concurrency-safety` | < 5 pairs or < 3 concurrent+sequential samples | `"skipped {n} tool(s): need >= 5 pairs..."` |
| Performance | `concurrent-session-scaling` | < 3 sessions, < 10 responses, or insufficient concurrency variation | `"need >= 3 sessions..."` / `"insufficient low/high-concurrency samples"` |
| Performance | `throughput-degradation` | < 20 responses or < 3 time windows | `"need >= 20 tool/call responses..."` |
| Compliance | `data-residency-signals` | no `expected_regions` configured | `"no expected_regions configured"` |
| Accuracy | `output-schema-drift` | < 3 responses per tool | `"fewer than 3 responses for {n} tool(s)"` |
| Accuracy | `description-accuracy` | LLM judge not enabled | `"LLM judge not enabled"` |
| Discoverability | `description-clarity` | LLM judge not enabled | `"LLM judge not enabled"` |
| Discoverability | `semantic-overlap` | LLM judge not enabled / < 2 tools | `"LLM judge not enabled"` / `"fewer than 2 tools to compare"` |

**Security evaluator:** No data-insufficiency guards exist — no changes needed.

**Expected behavior:** Running `reviewmymcp replay --log-file some.ndjson` with a thin log file now shows which checks were skipped and why, rather than silently showing "A" grades.

### 8. LLM judge wired into 4 evaluator checks

**Files:**
- `src/reviewmymcp/judge/base.py` — Added `SyncJudgeAdapter`
- `src/reviewmymcp/evaluators/base.py` — Added `judge: Any = None` field to `EvaluatorConfig`
- `src/reviewmymcp/evaluators/accuracy/checks.py` — New `_check_description_accuracy()`
- `src/reviewmymcp/evaluators/discoverability/checks.py` — New `_check_description_clarity()` and `_check_semantic_overlap()`
- `src/reviewmymcp/evaluators/security/checks.py` — Judge augmentation in `_check_prompt_injection()`
- `src/reviewmymcp/cli.py` — `_build_judge()` helper, wiring in `audit` and `replay` commands

**Problem:** The `Evaluator` protocol is sync, but `JudgeProvider.complete()` is async. The judge module existed with 3 provider implementations and 4 prompt templates, but no evaluator called them.

**Solution:** Added `SyncJudgeAdapter` in `judge/base.py` — a thin sync wrapper that calls `asyncio.run()` on the async provider. Constructed once in the CLI and passed via `EvaluatorConfig.judge`. Evaluators gate on `config.judge is None`.

**Four checks implemented:**

1. **`accuracy.description-accuracy`** — Per tool: aggregates call stats (total, successes, errors, sample outputs, avg latency), calls judge with `DESCRIPTION_ACCURACY` prompt. Score ≤ 2 → Finding(MEDIUM). Judge failure → SkippedCheck.

2. **`discoverability.description-clarity`** — Per tool: calls judge with `DESCRIPTION_CLARITY` prompt including name, description, and schema. Score ≤ 2 → Finding(MEDIUM).

3. **`discoverability.semantic-overlap`** — Pairwise tool comparison, capped at 20 pairs. Calls judge with `SEMANTIC_OVERLAP` prompt. Score ≥ 4 → Finding(MEDIUM for 4, HIGH for 5). Requires ≥ 2 tools.

4. **`security.prompt-injection-surface`** — Augments existing regex detection. After the regex pass, non-flagged responses are sent to the judge (capped at 50) with `PROMPT_INJECTION` prompt. Risk score ≥ 4 → Finding(HIGH for 4, CRITICAL for 5). Title prefixed with "Judge:" to distinguish from regex findings.

**CLI changes:**
- Extracted `_build_judge(provider_name, model)` helper (reusable factory for all 3 providers).
- `audit` command: builds `SyncJudgeAdapter` when `--no-llm-judges` is not set, passes via `eval_config.judge`.
- `replay` command: gained `--judge-provider` and `--judge-model` options with same wiring.
- Removed broken `eval_config.enabled_checks = []` line — no evaluator ever read it. Gating is now `config.judge is None`.

**Expected behavior:** Running `reviewmymcp audit "npx my-server"` (without `--no-llm-judges`) calls the LLM judge for the 4 enhanced checks. Running with `--no-llm-judges` emits SkippedCheck entries for those checks. `replay` now also supports judge options.

## Files Not Changed

- **`reporting/json_report.py`** — No changes needed. It calls `model_dump_json()` which automatically serializes the new fields.
- **`reporting/sarif_report.py`** — No changes needed. It iterates `dimension_scores[].findings` which is unchanged.

---

## Test Changes

### `tests/test_scoring/test_grader.py` — Rewritten

29 tests across 3 test classes:

**`TestScoreDimension`** (6 tests) — Unit tests for the `_score_dimension()` function:
- No findings → 100
- Raw dimension deduction (high = -10, critical = -25)
- Floors at 0 (can't go negative)
- Tool-count normalization: 1 high across 10 tools = 96, across 2 tools = 80
- Call-count normalization
- Zero denominator falls back to raw

**`TestGradeFromScore`** (7 tests) — Unit tests for `_grade_from_score()`:
- Score thresholds: 95→A, 90→A, 89→B, 75→B, 60→C, 40→D, 39→F
- Hard gate: 1 critical caps at D even with score 95
- Hard gate: 2 criticals forces F even with score 95
- 1 critical + low score: takes the worse of D and the score-based grade

**`TestGradeResults`** (13 tests) — Integration tests for the full `grade_results()` pipeline:
- No findings → A/100
- Low/info findings → still A
- Critical caps at D, two criticals → F
- Normalization: 1 high in discoverability with 50 tools → A (99.2), with 2 tools → B (80)
- Conformance ignores tool_count (raw scoring)
- No `overall_grade` attribute on report
- Top findings sorted by severity
- Metadata (events, sessions, tool_count, call_count) carried through
- `checks_skipped` carried through to `DimensionScore`
- `denominator_type`/`denominator_value` populated
- Multiple dimensions with different scoring behaviors

### `tests/test_reporting/test_outputs.py` — Updated

11 tests (was 5):
- JSON output has no `overall_grade`, has `tool_count`/`call_count`
- JSON dimension scores have `score` field
- JSON dimension scores have `checks_skipped`
- JSON roundtrip works with new schema
- HTML contains numeric scores
- HTML contains skipped checks
- SARIF unchanged behavior (still valid)

### `tests/test_scoring/test_differ.py` — Updated

7 tests:
- Removed assertions on `baseline_grade`/`current_grade` (no overall grade)
- Added test for `baseline_score`/`current_score` on dimension diffs

### `tests/test_cli.py` — Fixed

3 assertions updated:
- `test_replay_json`: checks for `dimension_scores` and `tool_count` instead of `overall_grade`
- `test_replay_to_file`: checks for `dimension_scores` instead of `overall_grade`
- `test_diff_command`: checks for `accuracy` in output instead of `Baseline`

### `tests/test_evaluators/test_checks_skipped.py` — New

17 tests covering all `checks_skipped` emission points:
- Efficiency: `latency-cliff` skipped with < 3 observations, `token-cost-per-task` skipped with no successes, no skip with enough data
- Conformance: `initialize-handshake` skipped for small sessions
- Reliability: `error-rate` skipped with < 3 calls
- Composability: `idempotency-violation` skipped with no repeats, `concurrency-safety` skipped with < 5 pairs
- Performance: `concurrent-session-scaling` skipped with < 3 sessions, `throughput-degradation` skipped with < 20 responses
- Compliance: `data-residency-signals` skipped without config, not skipped with config
- Accuracy: `output-schema-drift` skipped with < 3 responses, `description-accuracy` skipped without judge
- Discoverability: `description-clarity` skipped without judge, `semantic-overlap` skipped without judge, skipped with < 2 tools
- Security: no checks_skipped without judge

### `tests/test_evaluators/test_judge_integration.py` — New

12 tests using a `MockJudge` (synchronous mock returning canned `JudgeResponse`):
- `accuracy.description-accuracy`: low score → finding, high score → no finding, judge failure → SkippedCheck, no calls → no judge invocation
- `discoverability.description-clarity`: low score → finding, high score → no finding
- `discoverability.semantic-overlap`: high overlap → HIGH finding, low overlap → no finding, pair cap at 20 (30 total calls: 10 clarity + 20 overlap)
- `security.prompt-injection-surface`: judge flags subtle injection → finding, regex-flagged responses not sent to judge, no judge → regex-only still works

---

## Test Results

```
194 passed, 0 failed (0.26s)
```

Full suite passes including all pre-existing evaluator tests, ingest tests, synthetic tests, plus 29 new tests for checks_skipped and judge integration.
