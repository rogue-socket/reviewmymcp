"""Reliability dimension evaluators."""

from __future__ import annotations

from collections import defaultdict

from reviewmymcp.evaluators.base import (
    EvaluatorConfig,
    EvaluatorResult,
    Finding,
    Severity,
    SkippedCheck,
)
from reviewmymcp.ingest.schema import McpEvent, ServerMeta


class ReliabilityEvaluator:
    dimension: str = "reliability"

    def evaluate(
        self,
        events: list[McpEvent],
        server_meta: ServerMeta,
        config: EvaluatorConfig,
    ) -> EvaluatorResult:
        findings: list[Finding] = []
        skipped: list[SkippedCheck] = []
        findings.extend(self._check_error_rate(events, skipped))
        findings.extend(self._check_timeout_behavior(events))
        findings.extend(self._check_task_lifecycle(events))
        findings.extend(self._check_progress_reporting(events))
        findings.extend(self._check_retry_semantics(events))

        return EvaluatorResult(
            dimension=self.dimension,
            checks_run=[
                "reliability.error-rate",
                "reliability.timeout-behavior",
                "reliability.task-lifecycle",
                "reliability.progress-reporting",
                "reliability.retry-semantics",
            ],
            findings=findings,
            checks_skipped=skipped,
        )

    def _check_error_rate(self, events: list[McpEvent], skipped: list[SkippedCheck]) -> list[Finding]:
        findings: list[Finding] = []
        requests = {
            e.event_id: e
            for e in events
            if e.is_request and e.method == "tools/call" and not e.is_probe
        }
        responses = [
            e for e in events
            if e.is_response and e.request_event_id in requests and not e.is_probe
        ]

        tool_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "errors": 0})
        for resp in responses:
            req = requests.get(resp.request_event_id)
            if not req or not req.params:
                continue
            name = req.params.get("name", "unknown")
            tool_stats[name]["total"] += 1
            if resp.is_error or (resp.result and resp.result.get("isError")):
                tool_stats[name]["errors"] += 1

        skipped_tools = 0
        for tool_name, stats in tool_stats.items():
            if stats["total"] < 3:
                skipped_tools += 1
                continue
            rate = stats["errors"] / stats["total"]
            if rate > 0.1:
                findings.append(
                    Finding(
                        check_id="reliability.error-rate",
                        severity=Severity.HIGH,
                        title=f"Tool `{tool_name}` has {rate:.0%} error rate",
                        description=f"{stats['errors']} of {stats['total']} calls failed.",
                        evidence={"tool": tool_name, "error_rate": rate, **stats},
                        remediation="Investigate root cause of failures. Consider adding retry logic or circuit breakers.",
                        affected_entity=tool_name,
                    )
                )
        if skipped_tools:
            skipped.append(SkippedCheck(
                check_id="reliability.error-rate",
                reason=f"fewer than 3 calls for {skipped_tools} tool(s)",
            ))
        return findings

    def _check_timeout_behavior(self, events: list[McpEvent]) -> list[Finding]:
        findings: list[Finding] = []
        requests = {e.event_id: e for e in events if e.is_request and e.method == "tools/call"}
        responded = {e.request_event_id for e in events if e.is_response}

        unanswered = [req for eid, req in requests.items() if eid not in responded]
        if unanswered:
            tool_names = set()
            for req in unanswered:
                if req.params:
                    tool_names.add(req.params.get("name", "unknown"))
            findings.append(
                Finding(
                    check_id="reliability.timeout-behavior",
                    severity=Severity.CRITICAL,
                    title=f"{len(unanswered)} tool calls never received a response",
                    description=f"Tools affected: {', '.join(sorted(tool_names))}. Requests without responses indicate the server hung or crashed.",
                    evidence={"unanswered_count": len(unanswered), "tools": sorted(tool_names)},
                    remediation="Implement timeouts and task-based async execution for long-running operations.",
                )
            )

        cancellations = [e for e in events if e.is_notification and e.method == "notifications/cancelled"]
        for cancel in cancellations:
            if not cancel.params:
                continue
            cancelled_id = cancel.params.get("requestId")
            activity_after = [
                e
                for e in events
                if e.timestamp > cancel.timestamp
                and e.request_event_id
                and any(r.jsonrpc_id == cancelled_id for r in requests.values() if r.event_id == e.request_event_id)
            ]
            if activity_after:
                findings.append(
                    Finding(
                        check_id="reliability.timeout-behavior",
                        severity=Severity.HIGH,
                        title="Server continued processing after cancellation",
                        description=f"Request {cancelled_id} was cancelled but {len(activity_after)} related events occurred after.",
                        evidence={
                            "cancelled_request_id": cancelled_id,
                            "events_after": len(activity_after),
                        },
                        remediation="Respect cancellation notifications and stop processing cancelled requests.",
                    )
                )
        return findings

    def _check_task_lifecycle(self, events: list[McpEvent]) -> list[Finding]:
        findings: list[Finding] = []
        terminal = {"completed", "failed", "cancelled"}
        valid_transitions = {
            "working": {"working", "input_required", "completed", "failed", "cancelled"},
            "input_required": {"working", "input_required", "cancelled", "failed"},
        }

        task_statuses: dict[str, list[str]] = defaultdict(list)
        for event in events:
            if event.method == "notifications/tasks/status" and event.params:
                tid = event.params.get("taskId", "")
                status = event.params.get("status", "")
                if tid and status:
                    task_statuses[tid].append(status)

            if event.is_response and event.result:
                task = event.result.get("task")
                if isinstance(task, dict):
                    tid = task.get("taskId", "")
                    status = task.get("status", "")
                    if tid and status:
                        task_statuses[tid].append(status)

        for tid, statuses in task_statuses.items():
            for i in range(1, len(statuses)):
                prev = statuses[i - 1]
                curr = statuses[i]
                if prev in terminal:
                    findings.append(
                        Finding(
                            check_id="reliability.task-lifecycle",
                            severity=Severity.HIGH,
                            title=f"Task transitioned from terminal state `{prev}` to `{curr}`",
                            description=f"Task {tid} moved from `{prev}` to `{curr}`, violating the task state machine.",
                            evidence={
                                "task_id": tid,
                                "from": prev,
                                "to": curr,
                                "full_sequence": statuses,
                            },
                            remediation="Terminal states (completed, failed, cancelled) must not transition to other states.",
                            affected_entity=tid,
                        )
                    )
                    break
                allowed = valid_transitions.get(prev, set())
                if allowed and curr not in allowed:
                    findings.append(
                        Finding(
                            check_id="reliability.task-lifecycle",
                            severity=Severity.HIGH,
                            title=f"Invalid task transition from `{prev}` to `{curr}`",
                            description=f"Task {tid}: transition `{prev}` -> `{curr}` is not valid per MCP spec.",
                            evidence={"task_id": tid, "from": prev, "to": curr},
                            remediation=f"Valid transitions from `{prev}`: {sorted(allowed)}.",
                            affected_entity=tid,
                        )
                    )
        return findings

    def _check_progress_reporting(self, events: list[McpEvent]) -> list[Finding]:
        findings: list[Finding] = []
        requests = {e.event_id: e for e in events if e.is_request and e.method == "tools/call"}
        responses = {e.request_event_id: e for e in events if e.is_response and e.request_event_id in requests}
        progress_tokens: set[str | int] = set()
        for e in events:
            if e.method == "notifications/progress" and e.params:
                token = e.params.get("progressToken")
                if token is not None:
                    progress_tokens.add(token)

        long_calls: dict[str, list[float]] = defaultdict(list)
        for req_id, req in requests.items():
            resp = responses.get(req_id)
            if resp and resp.latency_ms and resp.latency_ms > 5000:
                name = req.params.get("name", "unknown") if req.params else "unknown"
                long_calls[name].append(resp.latency_ms)

        for tool_name, latencies in long_calls.items():
            avg_ms = sum(latencies) / len(latencies)
            findings.append(
                Finding(
                    check_id="reliability.progress-reporting",
                    severity=Severity.MEDIUM,
                    title=f"Tool `{tool_name}` averages {avg_ms / 1000:.1f}s with no progress notifications",
                    description=f"{len(latencies)} calls averaged {avg_ms:.0f}ms. No `notifications/progress` observed.",
                    evidence={
                        "tool": tool_name,
                        "avg_latency_ms": avg_ms,
                        "call_count": len(latencies),
                    },
                    remediation="Send `notifications/progress` for long-running operations so clients can track status.",
                    affected_entity=tool_name,
                )
            )
        return findings

    def _check_retry_semantics(self, events: list[McpEvent]) -> list[Finding]:
        findings: list[Finding] = []
        import json

        requests = sorted(
            [e for e in events if e.is_request and e.method == "tools/call" and not e.is_probe],
            key=lambda e: e.timestamp,
        )
        responses = {e.request_event_id: e for e in events if e.is_response and not e.is_probe}

        call_groups: dict[str, list[tuple[McpEvent, McpEvent | None]]] = defaultdict(list)
        for req in requests:
            if not req.params:
                continue
            key = json.dumps({"n": req.params.get("name"), "a": req.params.get("arguments")}, sort_keys=True)
            resp = responses.get(req.event_id)
            call_groups[key].append((req, resp))

        for key, pairs in call_groups.items():
            errors = [
                (req, resp)
                for req, resp in pairs
                if resp and (resp.is_error or (resp.result and resp.result.get("isError")))
            ]
            if len(errors) < 2:
                continue
            error_messages = []
            for _, resp in errors:
                if resp.error:
                    error_messages.append(resp.error.get("message", ""))
                elif resp.result:
                    content = resp.result.get("content", [])
                    if content and isinstance(content[0], dict):
                        error_messages.append(content[0].get("text", ""))

            unique_errors = set(error_messages)
            if len(unique_errors) > 1:
                call_info = json.loads(key)
                findings.append(
                    Finding(
                        check_id="reliability.retry-semantics",
                        severity=Severity.MEDIUM,
                        title=f"Tool `{call_info.get('n')}` returns different errors on retry",
                        description=f"Same arguments produced {len(unique_errors)} distinct error messages across {len(errors)} failures.",
                        evidence={
                            "tool": call_info.get("n"),
                            "unique_errors": sorted(unique_errors),
                            "failure_count": len(errors),
                        },
                        remediation="Ensure consistent error responses for the same failure condition.",
                        affected_entity=call_info.get("n", ""),
                    )
                )
        return findings
