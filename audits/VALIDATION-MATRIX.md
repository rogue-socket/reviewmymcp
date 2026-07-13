# MCP validation matrix

This matrix indexes the repository's existing external-server validation evidence. Package versions are historical run metadata, not recommendations to install the latest package.

| Server | Class | Package/version audited | Credential or mutation boundary | Evidence |
|---|---|---|---|---|
| Memory | Official stateful reference | `@modelcontextprotocol/server-memory@0.6.3` | Disposable local state only | [2026-05-18 report](./2026-05-18/README.md), [cross-check](./2026-05-18/CROSSCHECK-memory.md) |
| Sentry | Enterprise SaaS | `@sentry/mcp-server@0.33.0` | Read-only token; no successful mutations | [2026-05-18 methodology](./2026-05-18/METHODOLOGY.md), [cross-check](./2026-05-18/CROSSCHECK-sentry.md) |
| DuckDuckGo | Community web wrapper | `ddg-mcp-search@1.1.0` | Public-network traffic only | [2026-05-18 report](./2026-05-18/README.md), [cross-check](./2026-05-18/CROSSCHECK-ddg.md) |
| Everything | Official protocol reference | `@modelcontextprotocol/server-everything@2026.1.26` | Local disposable process | [fixture replay](./2026-05-31/CROSSCHECK-fixture-replay.md) |
| Filesystem | Official local-state server | `@modelcontextprotocol/server-filesystem@2026.1.14` | Isolated temporary directory | [fixture replay](./2026-05-31/CROSSCHECK-fixture-replay.md) |
| GitHub | Hosted API wrapper | `@modelcontextprotocol/server-github@2025.4.8` | Read-only throwaway token | [fixture replay](./2026-05-31/CROSSCHECK-fixture-replay.md) |
| Playwright | Browser automation | `@playwright/mcp@0.0.75` | Public test pages; no authenticated session | [fixture replay](./2026-05-31/CROSSCHECK-fixture-replay.md) |
| Web search | Third-party scraper | `@iflow-mcp/pskill9-web-search@0.1.1` | Public search traffic only | [fixture replay](./2026-05-31/CROSSCHECK-fixture-replay.md) |

## Coverage and gaps

- The matrix includes protocol, stateful, SaaS, browser, API-wrapper, and scraping surfaces.
- Evidence combines live audit archives with replay/source cross-checks; replay-only entries must not be interpreted as a fresh live run.
- Add a target only when its command, version, safety boundary, and archived evidence are recorded. Use disposable state and read-only credentials; never use production accounts.
- The next expansion should prioritize an authenticated server with documented per-tool scopes or a non-NPM transport, because those are the least-covered envelopes.
