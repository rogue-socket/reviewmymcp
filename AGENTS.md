# Repository Guidelines

## Project Structure & Module Organization

This repository root is a planning and coordination hub. `main` intentionally has no buildable source; it contains `README.md`, `DECISIONS.md`, `backlog.md`, and dated `handoffs/` notes. Treat `DECISIONS.md` as the architecture record before changing product behavior.

Active code lives in git worktrees under `/Users/yashagrawal/conductor/workspaces/reviewmymcp/`:

- `rio-de-janeiro/` (`rogue-socket/mcp-audit-plan`): primary codebase. Python package in `src/reviewmymcp/`; tests in `tests/`; docs in `docs/`.
- `curitiba/` (`rogue-socket/active-mcp-checker`): older active-checker prototype. Use for reference only.
- `harare/` (`rogue-socket/mcp-log-collector`): log collection utility and fixtures. Not user-facing.

## Build, Test, and Development Commands

Run implementation commands from `rio-de-janeiro/`, not from this root:

- `pip install -e ".[dev]"`: install the package with test and lint dependencies.
- `ruff check src/ tests/`: run lint checks.
- `pytest`: run the full test suite.
- `pytest tests/test_evaluators/`: run one focused area.
- `reviewmymcp replay traffic.ndjson`: replay captured MCP traffic.
- `reviewmymcp audit "python -m my_mcp_server"`: audit a live MCP server with synthetic traffic.

## Coding Style & Naming Conventions

The primary codebase is Python 3.12+ using Click, Pydantic v2, pytest, and ruff. Ruff is configured with `line-length = 120`, target `py312`, and lint families `E`, `F`, `I`, `N`, `W`, and `UP`. Keep modules under `src/reviewmymcp/` aligned with existing package areas such as `ingest/`, `evaluators/`, `scoring/`, `reporting/`, `synthetic/`, `active/`, and `judge/`. Name tests `test_*.py` and mirror source areas under `tests/`.

## Testing Guidelines

Add or update focused tests for behavioral changes. Evaluator work should usually include tests in `tests/test_evaluators/`; scoring changes belong in `tests/test_scoring/`; ingest changes belong in `tests/test_ingest/`. Prefer targeted pytest runs while iterating, then run `ruff check src/ tests/` and relevant broader tests before a PR.

## Commit & Pull Request Guidelines

Git history uses short imperative summaries, sometimes scoped, for example `active-audit: refuse mutating tools by default + persist agent trace` or `Rewrite dimension scoring: square-root curve, no per-dim denominator`. PRs should state the affected worktree/branch, summarize behavior changes, link relevant issues, list test commands run, and include screenshots or report artifacts only when output formatting changes.

## Security & Configuration Tips

Do not commit secrets, tokens, raw private audit logs, or generated local session notes. Use throwaway or read-only credentials for live MCP audits. For `active-audit`, prefer `--readonly`; use `--allow-mutations` only against disposable targets.

## Session docs

- `handoffs/*` - folder with dated handoff files
- `backlog.md` - living TODO. Tags: `[active]`, `[next]`, `[blocked: <reason>]`, no tag = someday.
- Both gitignored.
