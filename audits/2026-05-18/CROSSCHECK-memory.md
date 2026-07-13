# Cross-check: Memory MCP audit findings vs published source

The Memory audit run produced 5 findings across 2 dimensions (plus mostly-A grades elsewhere). To validate whether those reflect real defects we cross-checked against:

1. The published source of `@modelcontextprotocol/server-memory` (npm tarball `2025.11.25`, the calendar-versioned release that ships the in-code `version: "0.6.3"` and `protocol_version: "2025-11-25"` reported by the audit). Note: there is **no `0.6.3` tag on npm** — the package jumped from `0.6.2` (2024-12-04) to CalVer `2025.4.25`. The audit's "v0.6.3" label is the *server-instance version string*, not a published artifact.
2. The community discourse around the official memory server (~25 open issues on `modelcontextprotocol/servers`, AgentAudit/Socket reports, MCP security research).

The exercise confirmed every named finding and uncovered ~7 classes of real defect the audit missed. Unlike DDG, the Memory misses are not security primitives (SSRF, scraping) — they're **state-management, supply-chain-of-self, schema-spec, and tool-annotation** defects that this audit's evaluator set is structurally blind to.

## TL;DR

Audit findings agree with source. But the most-cited defects in upstream issues — race-condition graph corruption, npx-cache persistence loss, missing `destructiveHint` annotations on three irreversible delete tools, broken `read_graph` on entities with extra properties, `search_nodes` crashing on undefined fields, unconstrained string lengths, and an `outputSchema`-uses-Zod spec violation — are all invisible to the current evaluator set. The audit's mostly-A grades are *technically* correct on what they measure, but materially misleading about the server's real-world quality.

## Per-finding comparison

| audit finding | severity | label | evidence |
|---|---|---|---|
| `add_observations` 33% error rate (1/3) | HIGH | **CONFIRMED but UNDER-EVIDENCED** | source line 107-108: `throw new Error('Entity with name ${o.entityName} not found')` — fails hard on missing entity. Upstream issues #2030 ("frequently fails"), #3013 ("Unexpected non-whitespace character after JSON"), #2579 confirm the brittleness is real. 3 samples is too few to grade HIGH with confidence (the check itself is correct, the denominator is weak) |
| `add_observations` 1/8 schema-valid rejected | MEDIUM | **CONFIRMED** | same throw-on-missing-entity. The "valid" call asked to add observations to an entity that didn't exist — the schema permits it (only requires `entityName: string`), but runtime requires the name to resolve. Audit's remediation ("schema may be overly permissive") is exactly right |
| `create_entities` description-clarity 2/5 | MEDIUM | **CONFIRMED** | source line 195: literally `"Create multiple new entities in the knowledge graph"`. Field descriptions are tautological (line 178-180: `"The name of the entity"`, `"The type of the entity"`). All audit-listed issues match source verbatim |
| `read_graph` description-clarity 2/5 | MEDIUM | **CONFIRMED** | source line 309: `"Read the entire knowledge graph"` — five words, no return-shape, no size warning. Validates audit's complaint exactly |
| `open_nodes` description-clarity 2/5 | MEDIUM | **CONFIRMED** | source line 343: `"Open specific nodes in the knowledge graph by their names"`. "Open" is undefined as audit notes — code actually returns the filtered subgraph (entities + relations between them). Also corroborated by upstream issue #3669 ("Schema quality: missing property descriptions across official MCP servers") |

## What the audit missed entirely

### 1. Race condition → corrupted JSONL (upstream #1819, #2579, #3013, #3173)

`saveGraph` does `fs.writeFile` with no lock and no atomic rename. Multiple tool calls from one LLM response (common pattern: `create_entities` + `add_observations` + `create_relations`) interleave reads and full-file rewrites. Multiple users report this corrupts `memory.jsonl` to the point of total tool failure, recoverable only by deleting the file. The audit's `composability.concurrency-safety` was skipped ("need >= 5 pairs and >= 3 concurrent+sequential samples each") — a structural blind spot. **This is the #1 reported defect.**

### 2. Missing tool annotations on destructive operations (upstream #3400)

Zero of 9 tools carry `readOnlyHint` / `destructiveHint` / `idempotentHint` annotations. `delete_entities`, `delete_observations`, `delete_relations` perform **permanent, cascading deletions with no undo** and provide no signal to clients. The sister `server-filesystem` annotates all 14 tools. The audit has no check for "destructive tool missing destructiveHint" — should be a `conformance` or `security` check.

### 3. Default persistence inside `node_modules` / npx cache (upstream #692, #1018, #1846)

`defaultMemoryPath` = `path.dirname(fileURLToPath(import.meta.url)) + 'memory.jsonl'`. When launched via `npx -y`, this lives in `~/.npm/_npx/<hash>/node_modules/.../dist/memory.jsonl` — wiped on npx cache rotation or package update. **Silent data loss** of months of accumulated agent memory. Relative `MEMORY_FILE_PATH` resolves against the same install dir, not the user's project. Cited by upstream issue #4117 as the top item in a defense-in-depth review.

### 4. `search_nodes` crashes on entities with undefined fields (upstream #2044)

Calls `.toLowerCase()` on `e.name`, `e.entityType`, and every observation. If any are `undefined` (possible via legacy data, manual JSONL edits, or schema-loose writes from #3144), the entire tool throws. No defensive coding. The audit's `reliability.error-rate` only catches errors that occur during the audit's own session; this latent failure mode goes undetected.

### 5. `read_graph` rejects entities with additional properties (upstream #3144)

`outputSchema` declared as `z.object({...})` defaults to strict. If `memory.jsonl` contains an entity with a `custom_id` field (added by tooling, manual edit, or a future server version), `read_graph` fails with `MCP error -32602: Structured content does not match the tool's output schema`. **Storage layer is permissive; output schema is strict.** Audit has no check for "writes accept fields that reads reject."

### 6. `outputSchema` Zod-vs-JSON-Schema spec violation (upstream #3622)

Every tool's `outputSchema` is a Zod object. Per MCP spec, `outputSchema` must be plain JSON Schema. Works with Claude Desktop's bundled SDK; fails with the npm-distributed SDK. Audit has no check for "tool schema is wire-format-correct."

### 7. Unconstrained string parameters (upstream #3537)

Every `z.string()` in the source has no `.max()`, no pattern, no enum. An adversarial caller can pass arbitrarily large `name`, `entityType`, or `query` strings — written verbatim to disk, returned in `read_graph`. The third-party audit `mcp-security-audit` flagged Memory at 92/100 specifically on this. The audit's `security.excessive-permissions` doesn't model this DoS surface.

### 8. No secret redaction before persistence (upstream #4117)

Observations are written verbatim. If an agent persists a fetched web page, log, or error message containing tokens, they sit in `memory.jsonl` forever and re-enter the model on every `read_graph`. Memory is a **persistent prompt-injection surface** — content written today re-injects into every future session. Audit has no check for this.

### 9. Version metadata drift

The audit reports `"server_version": "0.6.3"` but no such tag exists on npm. The shipping tarball is `2025.11.25`. The in-code version string was never bumped after the CalVer migration. Recommend the audit pipeline record both `tools/list`-reported version *and* resolved npm version (mirrors DDG cross-check issue #12).

## What the audit got that nobody else has noticed

- **Quantified `add_observations` failure rate at the call level** — 1/3 in this run, 1/8 valid-rejected. The community issues describe the symptom ("frequently fails") qualitatively; the audit produced numbers.
- **Description-clarity scored at 2/5 with item-level rationale** — issue #3669 makes the same complaint in aggregate but doesn't grade per-tool. Audit is more rigorous.
- **`accuracy.schema-misuse` cleanly separates "schema accepts" from "runtime rejects"** — useful framing the upstream issues stumble around but never name.

## Project health signal

- Package: `@modelcontextprotocol/server-memory@2025.11.25` (latest: `2026.1.26`), MIT-licensed, Anthropic-maintained, ~44K weekly downloads, 5 npm maintainers, signed tarball.
- Repo: `modelcontextprotocol/servers` — alive, actively triaged, lots of inbound contributions.
- Maintenance status: **active but understaffed for this server specifically.** The race-condition bug (#1819, May 2025) has an open PR (#3060, then #3073) but no merged fix as of `2026.1.26`. Critical issues #692/#1018 (env-var/path bug) sat open for 6+ months. Hardening proposal #4117 is comprehensive and gets crickets.
- Clean profile (Anthropic-maintained, signed, popular, transparent source) but **the "official reference implementation" framing oversells operational maturity.** This is a sample, not a production memory layer.

## Recommendations for the audit pipeline

Memory exercises *different* gaps than DDG. New checks worth filing (in priority order):

1. **Race-condition probe** — Drive N concurrent writes from one session and validate file integrity afterward. Reproduces #1819 in seconds.
2. **Destructive-tool-without-destructiveHint check** — `conformance` or `security` dimension. Auto-fires when a tool whose name matches `/^(delete|remove|drop|clear|purge)/` lacks `destructiveHint: true`.
3. **Persistence-location check** — For servers with file-based state, verify default path is not inside `node_modules` / npm/npx cache / temp dir. New dimension or `reliability` check.
4. **Output-schema wire-format check** — Confirm `tools/list` `outputSchema` is valid JSON Schema (not Zod, not language-specific). Could ship as `conformance.tool-schema-shape`.
5. **Write/read schema-symmetry check** — Sample writes, then read back; flag if any persisted field is rejected by the read tool's output schema. Catches #3144-class bugs.
6. **String-parameter constraint check** — Flag string params with no `maxLength` / `pattern` / `enum`. Mirrors `mcp-security-audit`'s approach, generalises to all servers.
7. **Persistent-content provenance check** — For tools that persist arbitrary string content (memory, notes, document stores), flag missing redaction-before-persist. New defect class beyond DDG's "no provenance markers in fetched HTML."
8. **Larger-N samples** — `add_observations` graded HIGH off 3 samples; many other checks skipped for thin denominators (`fewer than 3 latency observations for 4 tool(s)`, `fewer than 3 calls for 4 tool(s)`). The audit harness needs to drive enough traffic to hit minimums on every tool.
9. **Auto-record resolved npm version** — same as DDG cross-check issue #12. The "v0.6.3" label here was the server's self-reported string, not the actual artifact.

**Answer to the framing question:** the DDG-specific gaps (SSRF, scraping-fragility, error-as-content, supply-chain provenance) genuinely do *not* apply to Memory — Memory has no network surface, ships from Anthropic with a signed tarball and clean dep tree, and uses MCP `isError` correctly. But Memory exposes a **different, equally large** class of audit blind spots around state management, tool annotations, and schema/spec conformance. The cross-check methodology generalises; the specific defect classes do not.

## Sources

- Source code: `@modelcontextprotocol/server-memory@2025.11.25` (npm tarball)
- Audit report: `./memory-mcp/report.json`
- [npm registry — @modelcontextprotocol/server-memory](https://www.npmjs.com/package/@modelcontextprotocol/server-memory)
- [#1819 — race condition corrupts JSON](https://github.com/modelcontextprotocol/servers/issues/1819)
- [#2579 — race-condition + env-misconfig deep dive](https://github.com/modelcontextprotocol/servers/issues/2579)
- [#3013 — `create_relations`/`add_observations` failing on WSL2](https://github.com/modelcontextprotocol/servers/issues/3013)
- [#3173 — Memory MCP JSON Parsing Error - All Tools Failing](https://github.com/modelcontextprotocol/servers/issues/3173)
- [#3400 — Add tool annotations to server-memory (9 tools, 0 annotated)](https://github.com/modelcontextprotocol/servers/issues/3400)
- [#3144 — read_graph schema validation error on additional properties](https://github.com/modelcontextprotocol/servers/issues/3144)
- [#3622 — outputSchema uses Zod, violating spec](https://github.com/modelcontextprotocol/servers/issues/3622)
- [#3537 — Unconstrained string parameters across all official servers](https://github.com/modelcontextprotocol/servers/issues/3537)
- [#3315 — AgentAudit: path-traversal finding](https://github.com/modelcontextprotocol/servers/issues/3315)
- [#692 — Memory MCP ignores custom storage path](https://github.com/modelcontextprotocol/servers/issues/692)
- [#1018 — Environment variables not respected in server-memory](https://github.com/modelcontextprotocol/servers/issues/1018)
- [#2030 — add_observations frequently fails](https://github.com/modelcontextprotocol/servers/issues/2030)
- [#2044 — search_nodes "Cannot read properties of undefined"](https://github.com/modelcontextprotocol/servers/issues/2044)
- [#4117 — safer persistence defaults, atomic writes, quotas, redaction](https://github.com/modelcontextprotocol/servers/issues/4117)
- [Upwind — Unpacking MCP Security Risks](https://www.upwind.io/feed/unpacking-the-security-risks-of-model-context-protocol-mcp-servers)
