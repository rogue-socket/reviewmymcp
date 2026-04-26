"""Performance under load dimension evaluators.

Note: These checks are most useful with synthetic load test data (multiple concurrent sessions).
With production logs, they detect patterns that suggest load-related issues.
"""

from __future__ import annotations

import re
from collections import defaultdict

from reviewmymcp.evaluators.base import (
    EvaluatorConfig,
    EvaluatorResult,
    Finding,
    Severity,
)
from reviewmymcp.ingest.schema import McpEvent, ServerMeta


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    sorted_v = sorted(values)
    idx = int(len(sorted_v) * p / 100)
    return sorted_v[min(idx, len(sorted_v) - 1)]


CONNECTION_ERROR_PATTERNS = [
    re.compile(r"connection\s+(?:refused|reset|timeout|closed)", re.IGNORECASE),
    re.compile(r"too\s+many\s+open\s+files", re.IGNORECASE),
    re.compile(r"pool\s+(?:exhausted|timeout|full)", re.IGNORECASE),
    re.compile(r"ECONNREFUSED|ECONNRESET|ETIMEDOUT|EMFILE", re.IGNORECASE),
    re.compile(r"max\s+(?:connections|retries)\s+(?:reached|exceeded)", re.IGNORECASE),
]


class PerformanceEvaluator:
    dimension: str = "performance"

    def evaluate(
        self,
        events: list[McpEvent],
        server_meta: ServerMeta,
        config: EvaluatorConfig,
    ) -> EvaluatorResult:
        findings: list[Finding] = []
        findings.extend(self._check_concurrent_session_scaling(events))
        findings.extend(self._check_throughput_degradation(events))
        findings.extend(self._check_resource_contention(events))
        findings.extend(self._check_connection_pool(events))

        return EvaluatorResult(
            dimension=self.dimension,
            checks_run=[
                "performance.concurrent-session-scaling",
                "performance.throughput-degradation",
                "performance.resource-contention",
                "performance.connection-pool-exhaustion",
            ],
            findings=findings,
        )

    def _check_concurrent_session_scaling(self, events: list[McpEvent]) -> list[Finding]:
        """Compare latency across different concurrency levels."""
        findings: list[Finding] = []
        sessions: dict[str | None, list[McpEvent]] = defaultdict(list)
        for event in events:
            sessions[event.session_id].append(event)

        if len(sessions) < 3:
            return findings

        responses = [e for e in events if e.is_response and e.latency_ms is not None and e.method == "tools/call"]
        if len(responses) < 10:
            return findings

        responses.sort(key=lambda e: e.timestamp)
        time_windows: list[tuple[int, float]] = []

        for resp in responses:
            concurrent = sum(
                1
                for s_events in sessions.values()
                if any(
                    e.is_request
                    and e.timestamp <= resp.timestamp
                    and any(
                        r.is_response and r.request_event_id == e.event_id and r.timestamp >= resp.timestamp
                        for r in events
                    )
                    for e in s_events
                )
            )
            time_windows.append((max(1, concurrent), resp.latency_ms))

        low_conc = [lat for conc, lat in time_windows if conc <= 2]
        high_conc = [lat for conc, lat in time_windows if conc >= 5]

        if low_conc and high_conc and len(low_conc) >= 3 and len(high_conc) >= 3:
            low_p50 = _percentile(low_conc, 50)
            high_p50 = _percentile(high_conc, 50)

            if low_p50 > 0 and high_p50 / low_p50 > 3:
                findings.append(
                    Finding(
                        check_id="performance.concurrent-session-scaling",
                        severity=Severity.HIGH,
                        title=f"Latency {high_p50 / low_p50:.1f}x higher under concurrent load",
                        description=f"p50 at low concurrency: {low_p50:.0f}ms. p50 at high concurrency: {high_p50:.0f}ms.",
                        evidence={
                            "low_concurrency_p50": low_p50,
                            "high_concurrency_p50": high_p50,
                            "ratio": high_p50 / low_p50,
                        },
                        remediation="Investigate scaling bottlenecks. Consider connection pooling, async processing, or horizontal scaling.",
                    )
                )
        return findings

    def _check_throughput_degradation(self, events: list[McpEvent]) -> list[Finding]:
        """Check if error rate increases with request rate."""
        findings: list[Finding] = []
        responses = sorted(
            [e for e in events if e.is_response and e.method == "tools/call"],
            key=lambda e: e.timestamp,
        )
        if len(responses) < 20:
            return findings

        window_size_sec = 10
        windows: list[tuple[float, float]] = []

        i = 0
        while i < len(responses):
            window_start = responses[i].timestamp
            window_end = window_start.timestamp() + window_size_sec
            window_events = []
            j = i
            while j < len(responses) and responses[j].timestamp.timestamp() < window_end:
                window_events.append(responses[j])
                j += 1

            if len(window_events) >= 3:
                rps = len(window_events) / window_size_sec
                errors = sum(1 for e in window_events if e.is_error or (e.result and e.result.get("isError")))
                error_rate = errors / len(window_events) if window_events else 0
                windows.append((rps, error_rate))

            i = max(i + 1, j)

        if len(windows) < 3:
            return findings

        windows.sort(key=lambda w: w[0])
        low_rps_windows = windows[: len(windows) // 3]
        high_rps_windows = windows[-(len(windows) // 3) :]

        low_err = sum(w[1] for w in low_rps_windows) / len(low_rps_windows) if low_rps_windows else 0
        high_err = sum(w[1] for w in high_rps_windows) / len(high_rps_windows) if high_rps_windows else 0

        if high_err > 0.1 and high_err > low_err * 3:
            avg_high_rps = sum(w[0] for w in high_rps_windows) / len(high_rps_windows)
            findings.append(
                Finding(
                    check_id="performance.throughput-degradation",
                    severity=Severity.HIGH,
                    title=f"Error rate spikes at higher throughput ({high_err:.0%} vs {low_err:.0%})",
                    description=f"At ~{avg_high_rps:.1f} req/s, error rate is {high_err:.0%}. At lower throughput: {low_err:.0%}.",
                    evidence={
                        "high_rps_error_rate": high_err,
                        "low_rps_error_rate": low_err,
                        "high_rps": avg_high_rps,
                    },
                    remediation="Identify throughput ceiling and implement backpressure or rate limiting.",
                )
            )
        return findings

    def _check_resource_contention(self, events: list[McpEvent]) -> list[Finding]:
        """Check if certain tools slow down when other tools are running."""
        findings: list[Finding] = []
        requests = {e.event_id: e for e in events if e.is_request and e.method == "tools/call"}
        responses = [e for e in events if e.is_response and e.request_event_id in requests and e.latency_ms]

        tool_latencies_solo: dict[str, list[float]] = defaultdict(list)
        tool_latencies_concurrent: dict[str, list[float]] = defaultdict(list)

        for resp in responses:
            req = requests[resp.request_event_id]
            name = req.params.get("name", "") if req.params else ""
            concurrent_with_other = any(
                other_req.params
                and other_req.params.get("name") != name
                and other_req.timestamp <= resp.timestamp
                and other_req.event_id
                in {r.request_event_id for r in events if r.is_response and r.timestamp >= req.timestamp}
                for other_req in requests.values()
                if other_req.event_id != req.event_id
            )

            if concurrent_with_other:
                tool_latencies_concurrent[name].append(resp.latency_ms)
            else:
                tool_latencies_solo[name].append(resp.latency_ms)

        for name in set(tool_latencies_solo) & set(tool_latencies_concurrent):
            solo = tool_latencies_solo[name]
            concurrent = tool_latencies_concurrent[name]
            if len(solo) < 3 or len(concurrent) < 3:
                continue
            solo_p50 = _percentile(solo, 50)
            conc_p50 = _percentile(concurrent, 50)
            if solo_p50 > 0 and conc_p50 / solo_p50 > 2:
                findings.append(
                    Finding(
                        check_id="performance.resource-contention",
                        severity=Severity.MEDIUM,
                        title=f"Tool `{name}` {conc_p50 / solo_p50:.1f}x slower when other tools run concurrently",
                        description=f"Solo p50: {solo_p50:.0f}ms. Concurrent p50: {conc_p50:.0f}ms.",
                        evidence={"tool": name, "solo_p50": solo_p50, "concurrent_p50": conc_p50},
                        remediation="Investigate shared resources (locks, connection pools, file handles).",
                        affected_entity=name,
                    )
                )
        return findings

    def _check_connection_pool(self, events: list[McpEvent]) -> list[Finding]:
        """Check for connection-related errors that appear under load."""
        findings: list[Finding] = []
        connection_errors: list[str] = []

        for event in events:
            if not (
                event.is_error or (event.result and isinstance(event.result, dict) and event.result.get("isError"))
            ):
                continue
            text = ""
            if event.error:
                text = event.error.get("message", "")
            elif event.result:
                content = event.result.get("content", [])
                if content and isinstance(content[0], dict):
                    text = content[0].get("text", "")

            for pattern in CONNECTION_ERROR_PATTERNS:
                if pattern.search(text):
                    connection_errors.append(text[:200])
                    break

        if connection_errors:
            findings.append(
                Finding(
                    check_id="performance.connection-pool-exhaustion",
                    severity=Severity.HIGH,
                    title=f"{len(connection_errors)} connection-related errors detected",
                    description="Errors suggest connection pool exhaustion or resource limits under load.",
                    evidence={"count": len(connection_errors), "samples": connection_errors[:5]},
                    remediation="Increase connection pool size, add connection timeouts, or investigate resource leaks.",
                )
            )
        return findings
