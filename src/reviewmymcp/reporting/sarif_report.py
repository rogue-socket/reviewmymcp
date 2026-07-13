"""SARIF 2.1.0 report output for CI integration."""

from __future__ import annotations

import json
from pathlib import Path

from reviewmymcp.evaluators.base import Severity
from reviewmymcp.scoring.grader import AuditReport

SEVERITY_TO_SARIF = {
    Severity.CRITICAL: "error",
    Severity.HIGH: "error",
    Severity.MEDIUM: "warning",
    Severity.LOW: "note",
    Severity.INFO: "note",
}


def render_sarif(report: AuditReport) -> str:
    rules = []
    results = []
    rule_ids: set[str] = set()

    for ds in report.dimension_scores:
        for finding in ds.findings:
            if finding.check_id not in rule_ids:
                rule_ids.add(finding.check_id)
                rules.append(
                    {
                        "id": finding.check_id,
                        "shortDescription": {"text": finding.check_id},
                        "defaultConfiguration": {
                            "level": SEVERITY_TO_SARIF.get(finding.severity, "note"),
                        },
                    }
                )

            results.append(
                {
                    "ruleId": finding.check_id,
                    "level": SEVERITY_TO_SARIF.get(finding.severity, "note"),
                    "message": {"text": f"{finding.title}\n\n{finding.description}"},
                    "properties": {
                        "severity": finding.severity.value,
                        "affected_entity": finding.affected_entity,
                        "remediation": finding.remediation,
                    },
                }
            )

    sarif = {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/main/sarif-2.1/schema/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "reviewmymcp",
                        "version": "0.1.0",
                        "informationUri": "https://github.com/reviewmymcp/reviewmymcp",
                        "rules": rules,
                    },
                },
                "results": results,
            }
        ],
    }
    return json.dumps(sarif, indent=2)


def write_sarif(report: AuditReport, path: str | Path) -> None:
    Path(path).write_text(render_sarif(report), encoding="utf-8")
