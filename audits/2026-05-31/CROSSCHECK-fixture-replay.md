# Cross-check: harare fixture replay vs package source

This pass replays the five harare fixture logs through the current local evaluator set and compares the findings against package source from npm tarballs. It is a lighter-weight version of the DDG/Memory/Sentry cross-checks: enough to identify which evaluator gaps generalize, not a full community-sentiment review.

## Inputs

Reports were regenerated from:

```bash
reviewmymcp replay ~/conductor/workspaces/reviewmymcp/harare/logs/<server>.ndjson \
  --no-llm-judges --output json --output-file audits/2026-05-31/fixtures-replay/<server>/report.json
```

Source packages inspected:

| fixture | audited server | package inspected |
|---|---|---|
| `filesystem` | `secure-filesystem-server` v0.2.0 | `@modelcontextprotocol/server-filesystem@2026.1.14` |
| `everything` | `mcp-servers/everything` v2.0.0 | `@modelcontextprotocol/server-everything@2026.1.26` |
| `github` | `github-mcp-server` v0.6.2 | `@modelcontextprotocol/server-github@2025.4.8` |
| `playwright` | `Playwright` v1.61.0-alpha-1778188671000 | `@playwright/mcp@0.0.75` |
| `web_search` | `web-search` v0.1.0 | `@iflow-mcp/pskill9-web-search@0.1.1` |

## Replay summary

| fixture | calls | notable grades | headline |
|---|---:|---|---|
| filesystem | 53 | efficiency B, accuracy B, composability B, reliability B, security C | Findings mostly reflect aggressive probe inputs and generic file-access heuristics. |
| everything | 48 | accuracy B, discoverability B, reliability B | One HIGH reliability finding is a stale harness artifact: scenario calls old tool name `add`. |
| github | 43 | efficiency C, accuracy B, discoverability D, reliability B, compliance B | Large payloads and missing annotations are real; auth/scope envelope is still invisible. |
| playwright | 31 | efficiency C, accuracy D, discoverability C, security B | Browser automation risk is real; name-quality linter badly overfires on `browser_*` naming. |
| web_search | 35 | reliability B | Silent empty-success behavior is now detected, but provenance concerns remain invisible in replay-only logs. |

## Per-server assessment

### filesystem

The source is comparatively mature: tools are constrained to allowed directories, startup resolves allowed paths, and the `tools/list` response includes annotations such as `readOnlyHint`, `idempotentHint`, and `destructiveHint`. The audit's response-payload finding on `read_file` is defensible when callers read whole large files; the tool also exposes `head` and `tail`, so the remediation should steer agents to bounded reads rather than imply the server lacks any mitigation.

Overclaimed: `security.excessive-permissions` fires on five file tools even though descriptions repeatedly state "Only works within allowed directories" and annotations distinguish read/write/destructive operations. This reinforces issue #22: excessive-permissions needs awareness of explicit sandbox boundaries.

### everything

The audit report contains a HIGH `reliability.error-rate` for `add`, but current source exposes `get-sum`, not `add`. The harare scenario is stale, also calling old names `longRunningOperation`, `sampleLLM`, and `getTinyImage`. This is a harness defect, not a server defect, and matches the backlog item to update `harare/scenarios/everything.py`.

The `gzip-file-as-resource` source has real network fetch risk, but also real mitigations: protocol filtering, optional `GZIP_ALLOWED_DOMAINS`, default 10 MB max fetch size, and timeout enforcement. The current security finding is directionally useful but underexplains existing controls.

### github

The audit correctly finds large response payloads for search/list/read tools; source calls GitHub REST endpoints directly and can return large JSON. The mutating surface is also real: tools include `create_or_update_file`, `push_files`, `create_repository`, `create_issue`, `create_pull_request`, `merge_pull_request`, and `update_pull_request_branch`.

Missed/under-modeled: none of those mutating tools carry MCP annotations in `tools/list`. The new `conformance.destructive-hint-missing` check does not fire because it is intentionally limited to destructive verbs such as delete/remove/drop. This shows the annotation check should eventually broaden to "mutating tool missing readOnlyHint/destructiveHint/idempotentHint", not only permanent delete tools.

Also missed: auth scope awareness. The server reads `GITHUB_PERSONAL_ACCESS_TOKEN`, but the report has no token-scope manifest and the package does not advertise per-tool required scopes. Issue #19's new metadata path helps when scopes are supplied, but GitHub still needs a source/API-derived scope map.

### playwright

The audit's browser-automation risk findings are materially real: the package exposes page navigation, JS evaluation, file upload, form filling, clicking, request inspection, and optional unsafe code execution. The package does annotate the active tool surface with `readOnlyHint`, `destructiveHint`, and `openWorldHint`, so the missing-annotation class does not apply here.

Overclaimed: `discoverability.name-quality` fires on most tools because the linter does not recognize the domain prefix `browser_` as an organizing namespace. The names are consistent and documented in the README. This is the clearest additional example for issue #22's naming-linter recalibration.

Missed: network policy configuration. The README documents `--allowed-origins` and `--blocked-origins`, but says allowed origins are not a security boundary and do not affect redirects. The audit currently has no check that records the browser network envelope used during a run.

### web_search

Source inspection shows the package is a small Google-scraping wrapper. It sends requests to `https://www.google.com/search` with a fixed User-Agent, has no retry/backoff, no timeout, and no rate-limit detection. The refreshed replay now flags `reliability.silent-failure-suspect` for repeated success-shaped empty search payloads, so the DDG silent-failure class generalizes here.

The tool does validate `query` and `limit`, caps `limit` at 10, and returns Axios errors with `isError: true`, so the DDG error-as-content issue does not reproduce here. Provenance remains absent because these replay logs do not include resolved package metadata.

## Generalized gap classes

| class | seen on | status |
|---|---|---|
| Harness drift / stale scenario tool names | everything | Fix harare scenario names before using fixture grades as evidence. |
| Heuristic overclaim on sandboxed file/browser tools | filesystem, playwright | Issue #22 should account for explicit sandbox/annotation evidence and domain prefixes. |
| Missing mutating-tool annotations beyond delete verbs | github | Broaden #18 follow-up to all mutating operations, not just destructive deletes. |
| Auth scope envelope missing | github, sentry | #19 foundation is in place; needs source/API scope mapping for GitHub-like servers. |
| Scraper fragility / empty-success behavior | web_search, ddg | #11 now detects empty-success clusters; #13 stress mode should expose rate-limit behavior. |
| OutputSchema wire-format defect | memory only in current set | #20 is useful, but these five fixture servers did not reproduce it. |
| Unsafe persistence defaults | memory only in current set | #21 does not generalize to these stateless/browser/API wrappers. |
| Provenance unavailable in replay-only artifacts | web_search, github, playwright | #9 adds the dimension, but replay logs need live/package metadata to populate it. |

## Recommendations

1. Fix `harare/scenarios/everything.py` before treating Everything fixture regressions as product findings.
2. Extend issue #18 after the first implementation: flag mutating tools without any useful annotations, especially GitHub-style create/update/push/merge tools.
3. Use #22 to recalibrate `security.excessive-permissions` and `discoverability.name-quality` with evidence from filesystem and Playwright.
4. Add package/runtime envelope metadata to replay reports when the original command or package spec is known.
5. Treat this directory as dated evidence for #15's first cross-check pass; a deeper source/community pass for GitHub and Playwright can be tracked separately.

## Sources

- Replay reports: `audits/2026-05-31/fixtures-replay/*/report.json`
- Source tarballs: npm packages listed in the inputs table
- Collector definitions: `~/conductor/workspaces/reviewmymcp/harare/collect_logs.py`
- Scenario definitions: `~/conductor/workspaces/reviewmymcp/harare/scenarios/*.py`
