"""HTML report output using Jinja2 templates."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from reviewmymcp.scoring.grader import AuditReport

TEMPLATE_DIR = Path(__file__).parent / "templates"


def render_html(report: AuditReport) -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)
    template = env.get_template("report.html.j2")
    return template.render(report=report)


def write_html(report: AuditReport, path: str | Path) -> None:
    Path(path).write_text(render_html(report), encoding="utf-8")
