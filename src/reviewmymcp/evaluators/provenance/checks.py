"""Provenance and supply-chain dimension evaluators."""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath

from reviewmymcp.evaluators.base import EvaluatorConfig, EvaluatorResult, Finding, Severity
from reviewmymcp.ingest.schema import McpEvent, ServerMeta


class ProvenanceEvaluator:
    dimension: str = "provenance"

    def evaluate(
        self,
        events: list[McpEvent],
        server_meta: ServerMeta,
        config: EvaluatorConfig,
    ) -> EvaluatorResult:
        findings: list[Finding] = []
        findings.extend(self._check_source_reachable(server_meta))
        findings.extend(self._check_readme_in_artifact(server_meta))
        findings.extend(self._check_license_in_artifact(server_meta))
        findings.extend(self._check_maintenance_recency(server_meta))
        findings.extend(self._check_version_churn(server_meta))

        return EvaluatorResult(
            dimension=self.dimension,
            checks_run=[
                "provenance.source-reachable",
                "provenance.readme-in-artifact",
                "provenance.license-in-artifact",
                "provenance.maintenance-recency",
                "provenance.version-churn",
            ],
            findings=findings,
        )

    def _check_source_reachable(self, server_meta: ServerMeta) -> list[Finding]:
        provenance = server_meta.provenance
        if not provenance.repository_url or provenance.source_reachable is not False:
            return []
        return [
            Finding(
                check_id="provenance.source-reachable",
                severity=Severity.MEDIUM,
                title="Declared source repository is not reachable",
                description=f"Repository URL `{provenance.repository_url}` was reported unreachable.",
                evidence={"repository_url": provenance.repository_url},
                remediation="Publish a reachable source repository or remove stale repository metadata.",
                affected_entity=provenance.repository_url,
            )
        ]

    def _check_readme_in_artifact(self, server_meta: ServerMeta) -> list[Finding]:
        files = _artifact_file_names(server_meta)
        if not files or any(name.startswith("readme") for name in files):
            return []
        return [
            Finding(
                check_id="provenance.readme-in-artifact",
                severity=Severity.INFO,
                title="Package artifact does not include a README",
                description="The installed artifact file list has no README file.",
                evidence={"artifact_files": sorted(files)[:20]},
                remediation="Ship a README in the published artifact.",
            )
        ]

    def _check_license_in_artifact(self, server_meta: ServerMeta) -> list[Finding]:
        provenance = server_meta.provenance
        files = _artifact_file_names(server_meta)
        if not provenance.license_declared or not files:
            return []
        if any(name.startswith(("license", "licence", "copying")) for name in files):
            return []
        return [
            Finding(
                check_id="provenance.license-in-artifact",
                severity=Severity.MEDIUM,
                title="Declared license is missing from package artifact",
                description=f"The package declares `{provenance.license_declared}` but ships no LICENSE/COPYING file.",
                evidence={"license_declared": provenance.license_declared, "artifact_files": sorted(files)[:20]},
                remediation="Include the license text in the published artifact.",
            )
        ]

    def _check_maintenance_recency(self, server_meta: ServerMeta) -> list[Finding]:
        days = server_meta.provenance.last_activity_days
        if days is None or days <= 180:
            return []
        severity = Severity.LOW if days > 365 else Severity.INFO
        return [
            Finding(
                check_id="provenance.maintenance-recency",
                severity=severity,
                title=f"Source activity is stale ({days} days)",
                description="The latest observed source commit or release is older than the recency threshold.",
                evidence={"last_activity_days": days},
                remediation="Review maintenance status before depending on this server in production.",
            )
        ]

    def _check_version_churn(self, server_meta: ServerMeta) -> list[Finding]:
        published = sorted(
            value
            for value in (_parse_timestamp(item) for item in server_meta.provenance.version_publish_times)
            if value is not None
        )
        for earlier, later in zip(published, published[1:], strict=False):
            if (later - earlier).total_seconds() <= 3600:
                return [
                    Finding(
                        check_id="provenance.version-churn",
                        severity=Severity.INFO,
                        title="Multiple package versions were published within one hour",
                        description="Rapid first-release churn can indicate rushed or unstable packaging.",
                        evidence={
                            "first_publish_time": earlier.isoformat(),
                            "second_publish_time": later.isoformat(),
                        },
                        remediation="Pin exact versions and review changelog/source diffs before upgrading.",
                    )
                ]
        return []


def _artifact_file_names(server_meta: ServerMeta) -> set[str]:
    return {
        PurePosixPath(path).name.lower()
        for path in server_meta.provenance.artifact_files
        if path
    }


def _parse_timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
