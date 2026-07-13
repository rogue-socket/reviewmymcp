# Cross-check: Sentry MCP audit findings vs published source

The Sentry MCP audit (v0.33.0, 22 tools, 140 calls, 1 session) was run with a **read-only User Auth Token** (`org:read`, `project:read`, `event:read`, `team:read`, `member:read`). We cross-checked the findings against:

1. The published source at `getsentry/sentry-mcp` (TypeScript monorepo, ~700 GitHub stars, 107 forks, actively maintained — last push 2026-05-15), pinned to the `0.33.0` tag (commit `f9cde91`).
2. The npm tarball metadata (`@sentry/mcp-server@0.33.0`, 143 files, 8.5 MB, FSL-1.1-Apache-2.0 license).
3. Real-user issues filed against `getsentry/sentry-mcp` in the last two weeks (#937, #951, #958, #968).

## TL;DR

**The Sentry MCP server is largely well-engineered, and the audit's headline grades (accuracy D, security D) are driven almost entirely by the read-only-token test setup, not by real defects.** Of the 6 HIGH findings, 4 are token-scope confounds, 1 is overclaimed (the server's error envelope is actually well-structured), and 1 is partial-confirm (a real silent-failure bug exists nearby but the audit landed on an adjacent tool). Of the 38 MEDIUM findings, 17 (`security.excessive-permissions`) are description-heuristic noise on a server whose tools all operate against one authenticated SaaS, and 18 (`accuracy.schema-misuse: schema-valid rejected`) overwhelmingly reflect upstream Sentry API rejecting calls that have valid JSON shape but invalid IDs/scopes — not a server defect. **The audit also missed a real silent-failure defect** (`search_events` query-rewriter drops predicates) that the user community documented 4 days before this run.

## Per-finding comparison

| audit finding | severity | label | evidence |
|---|---|---|---|
| `find_organizations`: 1/4 schema-invalid calls accepted | HIGH | **CONFOUND (likely auto-constraint-injection)** | `server.ts` auto-injects `constraint` params on every call (`paramsWithConstraints = {...params, ...applicableConstraints}`), so a "schema-invalid" probe whose missing field is a constraint will be silently filled. Worth re-running with `--no-constraints` before claiming it's a server bug. |
| `list_issues` 100% error rate (3/3) | HIGH | **CONFOUND** | source: `search_issues` requires `event:read` — token had it, but agent-rewriter path also needs a valid `organizationSlug` and an LLM provider. Audit's synthetic `organizationSlug='acme-corp'` returns the captured 404. Reproduces against any wrong slug. |
| `update_issue` 100% error rate (3/3) | HIGH | **CONFOUND** | `requiredScopes: ["event:write"]` in source. Token had `event:read` only. Every call returns upstream 403/404. |
| `list_events` 100% error rate (3/3) | HIGH | **PARTIAL CONFIRM** | partly a confound (slug mismatch), but `search_events` *also* genuinely returns 404 in production for some legitimate orgs — see [issue #937](https://github.com/getsentry/sentry-mcp/issues/937) (filed 2026-05-07, still open). Even a perfectly-scoped agent hits this. |
| `get_issue_tag_values` 100% error rate (3/3) | HIGH | **CONFOUND** | `requiredScopes: ["event:read"]` (we had it). Failures are 404s on the synthetic issue ID — would succeed with a real ID. |
| `composability.error-recovery-surface`: 16/28 ambiguous | HIGH | **OVERCLAIMED** | the captured `**Input Error**…` examples actually *are* well-structured: bold header, HTTP code, the specific Sentry API message, the offending parameter values, and "You may be able to resolve the issue by addressing the concern and trying again." Source: `error-handling.ts` formats these via `formatErrorForUser` with explicit branches for `UserInputError`, `ApiClientError` (4xx), `ApiServerError` (5xx with Sentry event IDs), `Authorization Expired`. The audit's heuristic is mis-firing — these are some of the better-formatted error envelopes in any MCP server. |
| 18× `accuracy.schema-misuse: schema-valid rejected` | MEDIUM | **DEFENSIBLE BEHAVIOR** | schemas are Zod (`z.string().trim().min(1)`, `z.number().min(1).max(100)`, `superRefine(validateSlugOrId)`), validated by the MCP SDK before the handler. Rejections after that point are *upstream API* rejections (404 on nonexistent IDs, 403 on insufficient scope, business rules). The remediation ("add constraints that reflect actual validation") would require encoding "issue exists in your org" in JSON Schema — not possible. |
| 17× `security.excessive-permissions: arbitrary network access / file system access` | MEDIUM | **OVERCLAIMED** | heuristic seems to match on words like "URL", "region", "DSN", "file", "attachment" in tool descriptions. Every tool here makes exactly one type of request — to `https://*.sentry.io/api/0/` (or to OpenAI/Anthropic for the agent rewriter). There is no `fetch_content`-style URL primitive. `regionUrl` is a parameter telling the server which Sentry datacenter the org lives in, not an SSRF surface. Severity calibration also wrong relative to the actual SSRF case on DDG. |
| `whoami`, `analyze_issue_with_seer` naming issues | LOW | **OVERCLAIMED** | `whoami` is a well-known Unix idiom; `analyze_issue_with_seer` literally starts with the verb `analyze`. Linter is too strict. |
| `list_events` description references HTTP methods | MEDIUM | **OVERCLAIMED** | triggered on the disclosure "AI-powered search is unavailable (no OPENAI_API_KEY or ANTHROPIC_API_KEY)" — this is a legitimate functional note about LLM dependency, not a leaky REST-wrapper smell. |
| `search_docs` 5.1s without progress notifications | MEDIUM | **CONFIRMED** | genuine, single observation, low-stakes. |

## What the audit missed entirely

### 1. The `search_events` silent-predicate-drop bug (Sentry-equivalent of DDG silent failure)

[Issue #968](https://github.com/getsentry/sentry-mcp/issues/968), filed 4 days before the audit: the `search_events` agent rewriter (an embedded LLM that translates natural-language → Sentry Discover query syntax) **silently strips custom tag predicates** it doesn't recognize (`tags[type]:Unified`, `tags[sequence]:...`). The returned `View in Sentry` URL also omits the stripped clauses, so the caller has no signal anything was lost. Identical numbers returned for `tags[type]:Unified` vs `tags[type]:Regular`. The user reports "the tool returns confidently-formatted JSON for a query that isn't the one the caller asked for." This is the same defect class as DDG's `return []` on rate-limit. **Generalised: any MCP tool that wraps a natural-language → DSL rewriter needs a silent-predicate-drop check.** A simple signal: compare the input query string against the `URL` returned in the response and flag unexplained subtraction.

### 2. The `list_events`/`search_events` 404 ([issue #937](https://github.com/getsentry/sentry-mcp/issues/937))

The audit *did* flag `list_events` at 100% error rate but attributed it to "investigate root cause / consider retry logic" — generic. The actual community signal is sharper: this tool fails 404 against legitimate orgs that have data, while sibling agent-backed tools (`search_issues`, `search_issue_events`) succeed against the same org. There's a real URL-construction bug in `search-events/handler.ts`. The audit had no way to know its 404s were partly real because it couldn't tell that apart from its own scope/slug issues.

### 3. Indirect prompt injection via fetched Sentry data

Stacktraces, breadcrumbs, transaction names, and user-supplied tag values flow back to the agent as content. None of it is sanitised. `analyze_issue_with_seer` will literally read attacker-controlled error messages and act on them. The audit has no instruction-vs-data delimiter check. Different surface from DDG (Sentry is authenticated, lower probability of malicious input) but real for any org accepting unsanitised user input.

### 4. `regionUrl` parameter trust

Several tools accept a caller-supplied `regionUrl` (e.g. `https://us.sentry.io`). The source does not pin this to an allowlist — an agent could be tricked into sending a token to an attacker-controlled URL. This is a low-severity SSRF/exfil surface the heuristic missed because it fired on the description, not the parameter shape.

### 5. Per-finding token-scope inference

The audit's report has no "token scope used" field, and no logic that says "this tool requires `event:write`, you ran with `event:read`, suppress error-rate finding." This would convert 4 of the 6 HIGH findings from `CONFOUND` to suppressed-with-explanation.

### 6. License is FSL-1.1-Apache-2.0, not MIT-equivalent

The npm metadata reports `FSL-1.1-ALv2` — Functional Source License with a 2-year delayed Apache transition. Anyone planning to vendor/fork this needs to know that; the audit has no license dimension.

## What the audit got that nobody else has noticed

- **`composability.error-recovery-surface` scoring**, even though *over*-applied here, is a legitimate dimension nobody else is measuring on MCP servers. MCPSafe's parallel scan ([issue #958](https://github.com/getsentry/sentry-mcp/issues/958)) gave Grade C with 123 medium findings — none specific to error-channel quality.
- **Meta-honesty about coverage** — flags `composability.idempotency-violation` and `performance.concurrent-session-scaling` as skipped with reason. Rare.
- **`list_events` 5.1s latency without progress notifications** — concrete, actionable, and absent from any external review.

## Project health signal

- Repo: `getsentry/sentry-mcp` — 693 stars, 107 forks, active (commits within 3 days of audit), maintained by Sentry employees with structured triage tooling.
- License: FSL-1.1-Apache-2.0 (delayed open source).
- Architecture: pnpm monorepo, Zod-validated schemas, Hono on Cloudflare Workers + stdio adapter, OpenTelemetry GenAI semconv, vitest evals.
- Open issues are mostly *feature requests and refinements* (better routing, snapshot tools, telemetry attribute names), not the "what is this and why doesn't it work" profile of DDG.

This is the **opposite** profile from DDG. Real product, real maintainers, real users filing actionable bug reports.

## Recommendations for the audit pipeline

In priority order:

1. **Token-scope-aware reliability suppression** (new). Read the token's scopes (Sentry exposes them via `GET /api/0/users/me/`). For each tool whose `requiredScopes` aren't satisfied, downgrade `reliability.error-rate` finding to INFO with explanation: "tool needs scope X, audit token has Y." This single change removes 3–4 of the 6 HIGH findings on this report.
2. **Silent-failure-via-rewriter check** (extension of DDG issue #11). For any tool that includes an agent/rewriter step (detectable from description keywords: "natural language", "AI-powered", "translates"), compare input query against the response's echo/URL/`query_executed` field and flag unexplained subtraction. Would have caught Sentry #968.
3. **`accuracy.schema-misuse: valid-rejected` recalibration.** When the rejection text is a clean upstream-API error response (4xx with a message), this isn't schema misuse — it's the schema correctly *not* encoding upstream business rules. Downgrade to INFO unless the rejection is malformed.
4. **`security.excessive-permissions` heuristic refinement.** The current keyword match (URL/region/file/DSN/attachment) over-fires on any well-described SaaS-wrapper tool. Tighten to: tool description *or implementation* shows a user-controlled URL/path being dereferenced. The DDG `fetch_content` case should still trip; Sentry's `find_dsns` should not.
5. **`composability.error-recovery-surface` heuristic recalibration.** "Ambiguous" should require both (a) no retryability hint AND (b) no specific failure reason. Sentry's `**Input Error**\n\nThere was an HTTP 404 error…API error (404): Project does not exist…You may be able to resolve…` satisfies (b). Reduce to MEDIUM. Add a positive check for `isError: true` being correctly set.
6. **Naming linter exceptions.** Whitelist `whoami` and prefix-verb tools like `analyze_*`.
7. **Token-scope manifest in report.** Emit `auth.scopes_used` and `auth.scopes_required` per tool so consumers can see the test envelope.
8. **License + provenance dimension** (carried over from DDG cross-check #9). Sentry would score well here; DDG would not.

## Sources

- Source code: `getsentry/sentry-mcp@0.33.0` (commit `f9cde91`); files inspected — `packages/mcp-core/src/server.ts`, `internal/error-handling.ts`, `tools/find-organizations.ts`, `tools/update-issue.ts`, `tools/search-issues/handler.ts`, `toolDefinitions.json`.
- npm: `https://registry.npmjs.org/@sentry/mcp-server/0.33.0` (FSL-1.1-Apache-2.0, 143 files, 8.5 MB).
- [Issue #937 — search_events 404 in prod](https://github.com/getsentry/sentry-mcp/issues/937)
- [Issue #951 — ApiServerError gateway timeout in update_issue](https://github.com/getsentry/sentry-mcp/issues/951)
- [Issue #958 — MCPSafe AIVSS 55/100 Grade C scan](https://github.com/getsentry/sentry-mcp/issues/958)
- [Issue #968 — search_events silently drops custom tag predicates](https://github.com/getsentry/sentry-mcp/issues/968)
- [Sentry MCP docs](https://docs.sentry.io/product/sentry-mcp/)
