# Cross-check: DDG MCP audit findings vs published source

The DDG audit run produced 8 findings across 4 dimensions. To validate whether those findings reflect real defects (vs evaluator artifacts), we cross-checked them against:

1. The published source code of `ddg-mcp-search@1.1.0` (extracted from the npm tarball — the GitHub repo `kawayiYokami/duckduckgo-mcp-server` returns **404**).
2. The community discourse around DDG-scraping MCP servers (GitHub issues across multiple comparable packages, blog posts, MCP discussions).

The exercise surfaced both that **every named finding is verifiable in the source** and that **the audit missed six classes of real defect**. The missed-defect classes became GitHub issues #9–#16.

## TL;DR

Audit findings agree with published source code on every named defect. But several serious defect classes (SSRF, silent-failure-on-error, error-as-content anti-pattern, supply-chain provenance gaps, scraping-fragility under load) are invisible to the current evaluator set.

## Per-finding comparison

| audit finding | severity | label | evidence |
|---|---|---|---|
| `search` accepts 4 schema-invalid calls | HIGH | **CONFIRMED** | `dist/searcher.js` destructures `request.params.arguments` directly with no validation code anywhere; zero JSON Schema runtime check |
| `fetch_content` accepts 4 schema-invalid calls | HIGH | **CONFIRMED** | same; `url` becomes whatever is passed, defaulted/coerced by JS, no type check |
| `search` accepts 3 missing-required-arg calls | HIGH | **CONFIRMED** | `query` defaulting via destructure means `undefined` flows into `URLSearchParams({q: undefined})` and silently posts |
| `fetch_content` accepts 3 missing-required-arg calls | HIGH | **CONFIRMED** | `url=undefined` triggers axios error path, returned as content string starting with `错误:` — itself a defect the audit didn't catch (errors-as-content, not isError) |
| `fetch_content` p95 = 24KB (~6k tokens) | HIGH | **CONFIRMED** | `max_length` default = 8000 chars; no compression, no summarisation |
| Tool descriptions in Chinese | MEDIUM | **CONFIRMED** | source strings: `'在DuckDuckGo上搜索并返回格式化结果'`, `'从URL获取并解析网页内容'`, all param descriptions Chinese |
| `fetch_content` arbitrary network access without safeguards | MEDIUM | **CONFIRMED but UNDER-CALIBRATED** | source has zero URL filtering — no scheme check, no localhost/private-IP block, no allowlist. This is SSRF and should be HIGH, not MEDIUM (see issue #14) |

## What the audit missed entirely

### 1. SSRF surface (issue #10)

`fetcher.js` accepts any URL with no filtering. Reachable:

- `http://169.254.169.254/latest/meta-data/` (AWS metadata)
- `http://127.0.0.1:5432/` (local Postgres)
- `http://10.0.0.1/` (internal network)
- `file:///etc/hosts` (axios may follow)

The audit's "arbitrary network access" finding is framed as a permissions concern. It is actually an SSRF primitive.

### 2. Silent failure on upstream error (issue #11)

`searcher.js:42-45`:

```js
} catch (error) {
  console.error('搜索失败:', error);
  return [];
}
```

DDG blocks via rate limiting / anomaly detection. The server returns `[]` — indistinguishable from "no results found" to the MCP client. The audit's reliability check graded A (100) because it only saw "successful" tool responses.

### 3. Error-as-content anti-pattern (issue #16)

`fetcher.js` returns error strings (e.g. `错误: HTTP 404 - 无法访问网页`) *inside the content field* instead of setting `isError: true`. The audit has no check for "error strings in content channel."

### 4. Supply-chain / provenance gaps (issue #9)

- The GitHub repo linked from `package.json` returns 404.
- The npm tarball ships `dist/` and `package.json` only — **no README, no LICENSE file**, despite `license: "MIT"` in package.json.
- Three versions published in 20 minutes on release day (1.0.0 → 1.0.1 → 1.1.0), then nothing for 5 months.
- ~30-70 downloads/day, no growth.

The audit has no provenance dimension. This is the "weekend wrapper, abandoned" profile. Anyone depending on this in production is depending on an opaque binary from an unreachable source.

### 5. Scraping fragility (issue #13)

- Truncated, non-real User-Agent (`Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36` — no Chrome/Safari version suffix).
- No retry/backoff.
- No proxy support.
- No rate-limit detection.

DDG anomaly detection blocks at ~30 calls/min. The audit ran ~21 calls in one session — well below the threshold. The "reliability: A (100)" is misleading because the server was measured in its best-case regime.

### 6. Content-Length bomb

No `maxContentLength` on `axios.get`. A 100MB page is fully downloaded before being truncated to 8000 chars. DoS/memory concern.

### 7. Indirect-prompt-injection surface beyond provenance markers

`fetch_content` strips `<script>` and a few sibling tags but leaves the rest of the DOM intact. Classic CSS `display:none` indirect-prompt-injection surface. The audit's "no provenance markers" finding names a symptom; the broader pattern (no sanitisation model, no instruction-vs-data delimiter) is the actual defect.

### 8. Version skew in the audit's own report

Original public report claimed v1.0.0 was tested. v1.0.0 only exposes 1 tool. Audit's `tool_count` = 2. We actually ran v1.1.0. Fix: auto-record resolved package version in the report (issue #12). Public report has been corrected.

## What the audit got that nobody else has noticed

- **Quantified description-clarity at 2/5** with a specific list of missing fields (when-to-use, return shape, error behaviour, max value for `max_results`). No external reviewer is grading MCP tool descriptions.
- **`accuracy.schema-misuse` at call-pair level** (4/10, 4/9 invalid-accepted) — more rigorous than any external review of any DDG MCP.
- **`efficiency.response-payload-bloat` with byte counts + token estimates**. The broader MCP discourse hand-waves "responses are too big"; the audit produces a number.

## Project health signal

- Package: `ddg-mcp-search@1.1.0`, MIT-licensed (per package.json; no LICENSE in tarball).
- Author: `kawayiYokami` (13 repos, primarily Chinese-language plugins for the AstrBot LLM chatbot framework — explains the Chinese descriptions; this MCP was likely extracted from that ecosystem).
- Repository: 404 as of 2026-05-18.
- Maintenance: dead. No commits since 2025-12-16.
- Downloads: ~30-70/day.

"Weekend wrapper, abandoned" profile. Use at your own risk.

## Recommendations for the pipeline

See issues #9–#16 on this repo. In priority order:

1. **#10** — SSRF probes for network-fetch tools (severity HIGH when unblocked).
2. **#9** — New `provenance` / `supply-chain` dimension.
3. **#11** — Silent-failure detection in reliability evaluator.
4. **#16** — Error-channel-correctness check.
5. **#14** — Severity calibration for `security.excessive-permissions`.
6. **#13** — `--stress` mode for higher-volume runs.
7. **#12** — Auto-record resolved package version in every report.
8. **#15** — (Meta) Replay this cross-check methodology against the other audited servers to identify which gaps generalise.

## Sources

- Source code: `ddg-mcp-search-1.1.0.tgz` (extracted from npm registry)
- [npm registry — ddg-mcp-search](https://registry.npmjs.org/ddg-mcp-search)
- [GitHub user kawayiYokami](https://github.com/kawayiYokami) (repo `duckduckgo-mcp-server` returns 404)
- [Snazzah/duck-duck-scrape#140 — anomaly detection](https://github.com/Snazzah/duck-duck-scrape/issues/140)
- [langgenius/dify#9045 — DDG 202 Ratelimit](https://github.com/langgenius/dify/issues/9045)
- [open-webui/open-webui#6624 — Ratelimit discussion](https://github.com/open-webui/open-webui/discussions/6624)
- [ChatForest review of nickclyde/duckduckgo-mcp-server](https://chatforest.com/reviews/duckduckgo-mcp-server/)
- [Snyk Labs — Prompt Injection meets MCP](https://labs.snyk.io/resources/prompt-injection-mcp/)
- [Promptfoo — Indirect Prompt Injection in Web-Browsing Agents](https://www.promptfoo.dev/blog/indirect-prompt-injection-web-agents/)
- [Unit 42 — MCP attack vectors](https://unit42.paloaltonetworks.com/model-context-protocol-attack-vectors/)
