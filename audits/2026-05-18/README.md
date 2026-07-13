# 2026-05-18 — Memory, Sentry, DuckDuckGo

Three real-world MCP servers, one audit pipeline run, three quality tiers.

| Server | Package | Version | Tier |
|---|---|---|---|
| [Memory](./memory-mcp/) | `@modelcontextprotocol/server-memory` | 0.6.3 | Official reference implementation |
| [Sentry](./sentry-mcp/) | `@sentry/mcp-server` | 0.33.0 | Official enterprise SaaS |
| [DuckDuckGo](./ddg-mcp/) | `ddg-mcp-search` | 1.1.0 | Community wrapper |

See [`METHODOLOGY.md`](./METHODOLOGY.md) for exact commands, environment, and judge setup. Three cross-check documents triangulate the audit findings against published source code + community sentiment:

- [`CROSSCHECK-ddg.md`](./CROSSCHECK-ddg.md) — DDG audit: every named finding CONFIRMED, but **6 classes of defect were missed** (SSRF, silent-failure, error-as-content, supply-chain, scraping-fragility, severity calibration).
- [`CROSSCHECK-memory.md`](./CROSSCHECK-memory.md) — Memory audit: every named finding CONFIRMED, but **9 classes of defect were missed** (race conditions, destructive-tool annotations, persistence location, Zod-vs-JSON-Schema spec violation, string constraints, secret redaction).
- [`CROSSCHECK-sentry.md`](./CROSSCHECK-sentry.md) — Sentry audit: **most findings are confounds or overclaims**. 4/6 HIGH findings are token-scope confounds; 17 MEDIUM security findings are heuristic noise on a SaaS-wrapper; 18 MEDIUM accuracy findings reflect defensible behaviour (server enforces business rules that JSON Schema can't express). The audit also missed a real silent-rewriter bug filed upstream 4 days before this run.

## Headline scores

Numbers below are from the JSON outputs in each `<server>/report.json` (canonical). The HTML reports in the same folders are from a separate audit run earlier the same day — scores there will differ slightly because the LLM scenario planner is non-deterministic (see "Inter-run variance" below).

| dimension       | Memory    | DDG       | Sentry    |
|-----------------|-----------|-----------|-----------|
| accuracy        | 90 A      | 68 **C**  | 54 **D**  |
| compliance      | 100 A     | 100 A     | 100 A     |
| composability   | 100 A     | 100 A     | 84 B      |
| conformance     | 100 A     | 100 A     | 100 A     |
| discoverability | 82 B      | 85 B      | 87 B      |
| efficiency      | 100 A     | 84 B      | 100 A     |
| performance     | 100 A     | 100 A     | 100 A     |
| reliability     | 84 B      | 100 A     | 66 C      |
| security        | 100 A     | 90 A      | 57 D      |

| | Memory | DDG | Sentry |
|---|---|---|---|
| events captured | 138 | 48 | 286 |
| sessions | 1 | 1 | 1 |
| tools | 9 | 2 | 22 |
| tool calls | 66 | 21 | 140 |
| findings (C/H/M/L) | 0/1/4/0 | 0/5/3/0 | 0/6/38/2 |

### Inter-run variance

`reviewmymcp audit` uses an LLM scenario planner with temperature=0.3, so two runs against the same server may produce different scenarios. Different scenarios → different defect coverage → slightly different scores. Example: in this archive, the Memory HTML was generated from an earlier run that didn't trip `reliability.error-rate` on `add_observations` (scored 100 A). The JSON was generated from a later run that did (scored 84 B). Both are valid; the defect is real (`add_observations` errors when its target entity doesn't exist, and one of the two runs landed scenarios that hit that case). The take-away is to read findings in terms of *defect classes*, not specific numeric scores.

## What the cross-checks revealed about the audit pipeline

Three servers, three different audit-pipeline failure modes — far more useful than the headline grades:

| Server | Audit verdict | Reality (per cross-check) | Pipeline failure mode |
|---|---|---|---|
| **DDG** | C-grade accuracy, B-grade security/efficiency | Findings real, but audit also missed SSRF, silent-failure, supply-chain rot, scraping fragility | **Too narrow** — checks the surface it knows; blind to whole defect classes |
| **Memory** | Mostly A — one HIGH on `add_observations`, four MEDIUM description-clarity | Audit missed 9 real defects including race-condition graph corruption, missing destructiveHint annotations on irreversible delete tools, persistence in npx cache, Zod-vs-JSON-Schema spec violation | **Different blind spots than DDG** — state-management, schema/spec, annotations |
| **Sentry** | D-grade accuracy + security, C-grade reliability | Most HIGH findings are token-scope confounds; 17 MEDIUM security and 18 MEDIUM accuracy are heuristic over-firing; one real silent-rewriter bug was missed | **Too eager** — false positives from coarse heuristics and no auth-scope awareness |

The audit pipeline has both **false positives** (Sentry's D grades inflated by environment) and **false negatives** (Memory and DDG defects entirely missed). Filing 6 new issues to address the most impactful gaps:

- Token-scope-aware reliability suppression (#17 — would remove 4 false-positive HIGH findings on Sentry)
- Race-condition probe (#18 — would catch Memory's #1 reported defect)
- Destructive-tool-without-destructiveHint check (#19 — broad applicability)
- Persistence-location safety check (#20 — node_modules/npx-cache trap)
- Tool-schema wire-format check (#21 — Zod-vs-JSON-Schema spec conformance)
- Heuristic recalibration: composability.error-recovery-surface + security.excessive-permissions false positives + naming linter (#22)

(See actual issue numbers in the repo's tracker once filed.) These extend the original 8 issues (#9–#16) filed after the DDG cross-check.

The clean Anthropic reference implementation (Memory) still returned mostly A grades — confirming the pipeline doesn't grade-deflate every server. But "A" is not the same as "no defects": it means "no defects in the dimensions the pipeline measures."

The DDG cross-check (see [`CROSSCHECK-ddg.md`](./CROSSCHECK-ddg.md)) confirmed every named DDG finding is verifiable in the published source code, but also identified **six classes of defect the audit missed entirely**. Those gaps became GitHub issues #9–#16 on this repo.

## Counter-intuitive result

The "decent" Sentry server scored worse on paper than the "mid" DDG server. The reasons are explained in detail in [`CROSSCHECK-ddg.md`](./CROSSCHECK-ddg.md) and the public summary, but in brief:

1. **Surface area amplifies findings.** 22 Sentry tools = 22 chances for a per-tool check to fire. DDG's 2 tools cap that at 2.
2. **Auth scope is a confound.** Read-only Sentry token + write tools → 100% errors on `update_issue` etc., counted as reliability defects.
3. **Stricter server validation → more "accuracy" findings.** Sentry's schema doesn't match its server's actual constraints. DDG has no validation at all (worse defect, but produces *fewer* findings).
4. **Composability checks fire on multi-step error chains.** Sentry returned ambiguous errors on 58% of failed calls. DDG never had enough failing sequential calls for the check to have data.

## Caveats

- Single-session, no concurrent burst. `performance.concurrent-session-scaling`, `performance.throughput-degradation`, `composability.idempotency-violation` report "insufficient data" on most servers — that's correct behavior.
- LLM-judge findings depend on the judge model. Different model → potentially different findings.
- Sentry "reliability" findings are partly an artifact of read-only token scope.
- Tool count distorts cross-server comparison.
