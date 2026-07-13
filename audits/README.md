# Real-world MCP audits

Archived runs of `reviewmymcp` against real, public MCP servers. Each dated folder is a self-contained snapshot: server reports, methodology, cross-check writeups.

## Runs

| Date | Servers | Notes |
|---|---|---|
| [2026-05-18](./2026-05-18/) | Memory (official), Sentry (official), DuckDuckGo (community) | Baseline comparison across three quality tiers. First run with the Claude Agent SDK judge integration. Includes cross-check of DDG audit findings against published source code. |
| [2026-05-31](./2026-05-31/) | Everything, filesystem, GitHub, Playwright, web search | Fixture replay and source cross-check across protocol, local-state, hosted API, browser, and third-party scraper surfaces. |

See the [validation matrix](./VALIDATION-MATRIX.md) for the complete indexed coverage and safety boundaries.

## Purpose

These audits exist for three reasons:

1. **Validate the pipeline.** Running against real servers exposes evaluator bugs that synthetic tests miss.
2. **Calibrate severity.** Real findings on real servers tell us whether MEDIUM/HIGH thresholds make sense.
3. **Demonstrate value.** Reproducible reports against well-known servers make it easier for others to assess whether `reviewmymcp` is worth adopting.

## Conventions

- One folder per audit date, format `YYYY-MM-DD`.
- One subfolder per audited server, named after the server (matches the MCP slug where possible).
- Each server folder contains `report.html` (human-readable) and `report.json` (machine-readable).
- Date-folder `README.md` summarises that run's setup, results, and cross-checks.
- `METHODOLOGY.md` in each date-folder records the exact commands, environment, and judge configuration so the run is reproducible.
- `CROSSCHECK-<server>.md` files document findings from triangulating the audit against external sources (source code, community sentiment).

## Reproducing a run

Each `METHODOLOGY.md` includes the exact commands. Numbers will vary between runs because the LLM scenario planner is non-deterministic, but the same defect *classes* should reproduce.
