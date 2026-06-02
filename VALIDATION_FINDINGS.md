# Validation Findings

Captured: 2026-06-01

This document records the end-to-end validation pass across the three active branches. Validation used `/Users/yashagrawal/Documents/codex-agent` as the sidecar intelligence layer and kept raw artifacts under `/tmp/reviewmymcp-validation/`.

## Branches Validated

- `rogue-socket/mcp-audit-plan` in `/Users/yashagrawal/conductor/workspaces/reviewmymcp/rio-de-janeiro`
- `rogue-socket/mcp-log-collector` in `/Users/yashagrawal/conductor/workspaces/reviewmymcp/harare`
- `rogue-socket/active-mcp-checker` in `/Users/yashagrawal/conductor/workspaces/reviewmymcp/curitiba`

All three worktrees were clean after validation.

## Validation Summary

The validation pass ran 14 commands across the branches. All branch checks were accepted after correcting two harness/API mismatches in the validation script.

Sidecar assessment:

- Overall: `warn`
- Tool-call depth: `3/5`
- Intelligence: `3/5`
- Output quality: `4/5`

## Branch Findings

### `mcp-audit-plan`

Status: technically healthy. The first validation pass under-counted findings because it read a non-existent `findings` field instead of `top_findings`.

- `pytest` passed: `328 passed`.
- Changed-file ruff check passed.
- `git diff --check main...HEAD` passed.
- `reviewmymcp replay` emitted parseable JSON with stable keys.
- Sample replay measured `4` calls, `10` dimensions, and `10` top findings, including high-severity findings.
- Everything replay measured `20` calls, `10` dimensions, and `10` top findings, including critical/high-severity findings.
- Resolved: replay exit `1` is intended when top findings include `critical` or `high` severity. The CLI still emits valid JSON for machine consumers.
- Remaining work: add explicit tests that known fixtures produce expected findings so validation does not regress to field-name mistakes.

### `mcp-log-collector`

Status: useful coverage infrastructure, but not yet intelligence-heavy.

- `python3 -m py_compile collect_logs.py mcp_driver/*.py scenarios/*.py` passed.
- Empty collector run passed.
- `git diff --check main...HEAD` passed.
- Everything server scenario coverage: `6` scenarios, `19` expected tool-call steps, `5` unique tools, `0` scenario/tool gaps.
- Concern: scenario coverage is structural. It does not yet prove end-to-end collection against live MCP servers feeding the audit or active branches.

### `active-mcp-checker`

Status: strongest intelligence signal, but needs deeper execution coverage.

- Focused tests passed: `25 passed`.
- Ruff check passed for `src/reviewmymcp/active` and `tests/test_active`.
- `git diff --check main...HEAD` passed.
- Simulated active run used `1` tool call and detected:
  - `injection_in_output`
  - `untrusted_content_no_provenance`
  - positive workflow signals including `tool_found`, `correct_args_first_try`, and `task_completed`
- Simulated report produced `2` top findings, `6` dimensions, and overall grade `D`.
- Concern: validation used only a one-tool-call simulation. This is promising but too shallow for confidence in multi-step agent behavior.

## Ordered Fix Queue

1. Done: added explicit expected-finding assertions to `mcp-audit-plan` replay tests.
2. Done: expanded `active-mcp-checker` coverage with a multi-turn, multi-tool security scenario that exercises malicious output, provenance, and error recovery.
3. Done: added `mcp-log-collector` artifact coverage summary and tests that validate expected scenario calls against captured events while excluding probe traffic.

## Fix Pass Notes

- `mcp-audit-plan`: added a regression test that verifies `sample_stdio_log.ndjson` produces expected `top_findings`, `call_count`, high-severity findings, and exit `1`.
- `active-mcp-checker`: fixed the security test helper to count errored tool calls, then added a multi-tool scoring test covering `search_web`, `fetch_url`, and `update_record`.
- `mcp-log-collector`: added `scenario_coverage_summary()` and tests for complete coverage, missing calls, and stale scenario tool names.

## Post-Fix Baseline

Captured after the fix pass with artifacts under `/tmp/reviewmymcp-validation-postfix/`.

Validation results:

- `mcp-audit-plan`: CLI/integration tests passed (`38 passed`), ruff passed, diff check passed.
- `mcp-log-collector`: collector tests passed (`3 passed`), py_compile passed, empty collector run passed, diff check passed.
- `active-mcp-checker`: active tests passed (`26 passed`), ruff passed, diff check passed.

Corrected measurements:

- Audit sample replay: `4` calls, `7` tools, `10` dimensions, `10` top findings.
- Audit everything replay: `20` calls, `8` tools, `10` dimensions, `10` top findings.
- Collector coverage summary: `19/19` expected scenario calls observed in synthetic coverage validation.
- Active multi-tool simulation: `4` tool calls, `1` error, `5` top findings.

Sidecar reassessment:

- Overall: `warn`
- Tool-call depth: `4/5`
- Intelligence: `4/5`
- Output quality: `4/5`

Remaining risks:

- Replay exit `1` is intentional when high/critical findings exist, but this should be documented for CI consumers.
- Validation still relies mostly on fixtures and simulations rather than broad live MCP server runs.
- Collector coverage is not yet proven through a full collector-generated-log-to-audit replay pipeline.

Next work:

1. Done: documented replay exit-code semantics for findings versus execution failure.
2. Done: ran one full collector-to-audit pipeline using generated logs as replay input.
3. Done: added a larger active-checker scenario with more tool calls and a realistic multi-step failure/recovery path.

## Pipeline Validation

Captured under `/tmp/reviewmymcp-pipeline/`.

- Collector command: Everything server only, with filesystem/GitHub/Playwright/web search skipped.
- Collector output: `132` events, `11` errors, `19/19` expected scenario calls, `0` missing scenario calls.
- Audit replay input: `/tmp/reviewmymcp-pipeline/everything.ndjson`.
- Audit replay output: `/tmp/reviewmymcp-pipeline/audit-report.json`.
- Audit replay metrics: `15` tools, `48` calls, `132` events, `10` dimensions, `10` top findings.
- Top finding severities included `critical`, `high`, and `medium`.
- Replay exit code was `1`, as expected for high/critical findings while still writing valid JSON.

Remaining risks after this pass:

- Live pipeline has been proven for the Everything server only.
- Active checker still uses deterministic simulated sessions in tests; live LLM/tool execution remains a separate validation step.

## Live Matrix Expansion

Captured under `/tmp/reviewmymcp-live-matrix/`.

- Collector command: Filesystem server only, with Everything/GitHub/Playwright/web search skipped.
- Collector output: `135` events, `35` errors, `21/21` expected scenario calls, `0` missing scenario calls.
- Audit replay input: `/tmp/reviewmymcp-live-matrix/filesystem.ndjson`.
- Audit replay output: `/tmp/reviewmymcp-live-matrix/filesystem-audit-report.json`.
- Audit replay metrics: `14` tools, `53` calls, `135` events, `10` dimensions, `10` top findings.
- Top finding severities included `high` and `medium`.
- Replay exit code was `1`, as expected for high findings while still writing valid JSON.

Current live pipeline coverage:

- `@modelcontextprotocol/server-everything`
- `@modelcontextprotocol/server-filesystem` against an isolated temp directory

## Third-Party Live Validation

Captured under `/tmp/reviewmymcp-thirdparty/`.

- Collector command: `@iflow-mcp/pskill9-web-search` only, with Everything/filesystem/GitHub/Playwright skipped.
- Collector output: `91` events, `6` errors, `13/13` expected scenario calls, `0` missing scenario calls.
- Audit replay input: `/tmp/reviewmymcp-thirdparty/web_search.ndjson`.
- Audit replay output: `/tmp/reviewmymcp-thirdparty/web-search-audit-report.json`.
- Audit replay metrics: `1` tool, `35` calls, `91` events, `10` dimensions, `2` top findings.
- Top findings: `reliability.silent-failure-suspect` (`high`) and `efficiency.redundant-calls` (`medium`).
- Replay exit code was `1`, as expected for high findings while still writing valid JSON.

Additional branch hardening:

- `mcp-audit-plan`: added a terminal output contract test for human-readable replay output.
- `mcp-audit-plan`: added a larger replay stability test using a generated `325` event, `100` call log.

## Evidence Artifacts

- `/tmp/reviewmymcp-validation/evidence.json`
- `/tmp/reviewmymcp-validation/sidecar_assessment.txt`
- `/tmp/reviewmymcp-validation/sidecar_metrics.json`
- `/tmp/reviewmymcp-validation-postfix/evidence.json`
- `/tmp/reviewmymcp-validation-postfix/sidecar_assessment.txt`
- `/tmp/reviewmymcp-validation-postfix/sidecar_metrics.json`
