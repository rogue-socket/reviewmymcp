"""JSON report output."""

from __future__ import annotations

from pathlib import Path

from reviewmymcp.scoring.grader import AuditReport


def render_json(report: AuditReport) -> str:
    return report.model_dump_json(indent=2)


def write_json(report: AuditReport, path: str | Path) -> None:
    Path(path).write_text(render_json(report), encoding="utf-8")
