"""Reliability dimension evaluators."""

from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path

from reviewmymcp.evaluators.base import (
    EvaluatorConfig,
    EvaluatorResult,
    Finding,
    Severity,
    SkippedCheck,
)
from reviewmymcp.ingest.schema import McpEvent, ServerMeta

CONCURRENT_WRITE_PROBE_TYPES = {"concurrent_write_integrity", "concurrent_write_integrity_readback"}
JSON_CORRUPTION_SIGNATURES = (
    "unexpected non-whitespace character",
    "unexpected end of json input",
    "unterminated string in json",
    "json parse",
    "json parsing",
    "invalid json",
    "malformed json",
)


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
        findings.extend(self._check_error_rate(events, server_meta, skipped))
        findings.extend(self._check_silent_failure_suspect(events))
        findings.extend(self._check_timeout_behavior(events))
        findings.extend(self._check_task_lifecycle(events))
        findings.extend(self._check_progress_reporting(events))
        findings.extend(self._check_retry_semantics(events))
        findings.extend(self._check_concurrent_write_integrity(events))
        findings.extend(self._check_unsafe_persistence_default(server_meta))
        findings.extend(self._check_rate_limit_collapse(events))
        findings.extend(self._check_silent_degradation(events))

        return EvaluatorResult(
            dimension=self.dimension,
            checks_run=[
                "reliability.error-rate",
                "reliability.silent-failure-suspect",
                "reliability.timeout-behavior",
                "reliability.task-lifecycle",
                "reliability.progress-reporting",
                "reliability.retry-semantics",
                "reliability.concurrent-write-integrity",
                "reliability.unsafe-persistence-default",
                "reliability.rate-limit-collapse",
                "reliability.silent-degradation",
            ],
            findings=findings,
            checks_skipped=skipped,
        )

    def _check_error_rate(
        self,
        events: list[McpEvent],
        server_meta: ServerMeta,
        skipped: list[SkippedCheck],
    ) -> list[Finding]:
        findings: list[Finding] = []
        requests = {e.event_id: e for e in events if e.is_request and e.method == "tools/call" and not e.is_probe}
        responses = [e for e in events if e.is_response and e.request_event_id in requests and not e.is_probe]

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
                missing_scopes = self._missing_required_scopes(tool_name, server_meta)
                if missing_scopes:
                    findings.append(
                        Finding(
                            check_id="reliability.error-rate",
                            severity=Severity.INFO,
                            title=f"Tool `{tool_name}` failed but audit token lacks required scopes",
                            description=(
                                f"{stats['errors']} of {stats['total']} calls failed. "
                                f"`{tool_name}` requires scope(s) {', '.join(missing_scopes)} "
                                "that were not present in the audit token."
                            ),
                            evidence={
                                "tool": tool_name,
                                "error_rate": rate,
                                **stats,
                                "scopes_used": server_meta.auth.scopes_used,
                                "required_scopes": server_meta.auth.scopes_required.get(tool_name, []),
                                "missing_scopes": missing_scopes,
                                "suppressed": True,
                            },
                            remediation="Rerun with a token that has the required scopes, or treat this as an auth-envelope limitation.",
                            affected_entity=tool_name,
                        )
                    )
                    continue
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
            skipped.append(
                SkippedCheck(
                    check_id="reliability.error-rate",
                    reason=f"fewer than 3 calls for {skipped_tools} tool(s)",
                )
            )
        return findings

    def _check_silent_failure_suspect(self, events: list[McpEvent]) -> list[Finding]:
        requests = {
            e.event_id: e
            for e in events
            if e.is_request and e.method == "tools/call" and not e.is_probe and not e.is_stress
        }
        tool_sequences: dict[str, list[bool]] = defaultdict(list)
        tool_errors: dict[str, int] = defaultdict(int)

        for response in events:
            if not response.is_response or response.request_event_id not in requests:
                continue
            request = requests[response.request_event_id]
            tool_name = request.params.get("name", "unknown") if request.params else "unknown"
            if response.is_error or (response.result and response.result.get("isError")):
                tool_errors[tool_name] += 1
                continue
            tool_sequences[tool_name].append(_is_success_shaped_empty(response))

        findings = []
        for tool_name, sequence in tool_sequences.items():
            total = len(sequence)
            empty_count = sum(1 for is_empty in sequence if is_empty)
            if total < 3 or empty_count == 0 or tool_errors.get(tool_name, 0):
                continue
            empty_rate = empty_count / total
            max_streak = _max_true_streak(sequence)
            if not (empty_count == total or max_streak >= 3):
                continue
            severity = Severity.HIGH if empty_rate > 0.5 else Severity.MEDIUM
            findings.append(
                Finding(
                    check_id="reliability.silent-failure-suspect",
                    severity=severity,
                    title=f"Tool `{tool_name}` returns success-shaped empty payloads",
                    description=(
                        f"{empty_count} of {total} successful responses were default-shaped empty payloads "
                        "with no explicit errors."
                    ),
                    evidence={
                        "tool": tool_name,
                        "empty_count": empty_count,
                        "total": total,
                        "empty_rate": empty_rate,
                        "max_empty_streak": max_streak,
                    },
                    remediation="Return explicit tool errors for upstream failures, and distinguish true empty results from fallback failures.",
                    affected_entity=tool_name,
                )
            )
        return findings

    def _missing_required_scopes(self, tool_name: str, server_meta: ServerMeta) -> list[str]:
        required = set(server_meta.auth.scopes_required.get(tool_name, []))
        if not required:
            tool = next((t for t in server_meta.tools if t.name == tool_name), None)
            required = set(tool.required_scopes) if tool else set()
        if not required or not server_meta.auth.scopes_used:
            return []
        used = set(server_meta.auth.scopes_used)
        return sorted(required - used)

    def _check_rate_limit_collapse(self, events: list[McpEvent]) -> list[Finding]:
        baseline: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "errors": 0})
        stress: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "errors": 0})
        requests = {e.event_id: e for e in events if e.is_request and e.method == "tools/call"}

        for response in events:
            if not response.is_response or response.request_event_id not in requests:
                continue
            request = requests[response.request_event_id]
            if request.is_probe or response.is_probe:
                continue
            tool_name = request.params.get("name", "unknown") if request.params else "unknown"
            bucket = stress if (request.is_stress or response.is_stress) else baseline
            bucket[tool_name]["total"] += 1
            if response.is_error or (response.result and response.result.get("isError")):
                bucket[tool_name]["errors"] += 1

        findings = []
        for tool_name, stress_stats in stress.items():
            if stress_stats["total"] < 5:
                continue
            base_stats = baseline.get(tool_name, {"total": 0, "errors": 0})
            baseline_rate = base_stats["errors"] / base_stats["total"] if base_stats["total"] else 0.0
            stress_rate = stress_stats["errors"] / stress_stats["total"]
            if stress_rate <= 0.1 or (baseline_rate > 0 and stress_rate <= baseline_rate * 2):
                continue
            findings.append(
                Finding(
                    check_id="reliability.rate-limit-collapse",
                    severity=Severity.HIGH,
                    title=f"Tool `{tool_name}` error rate collapsed under stress",
                    description=f"Stress error rate was {stress_rate:.0%}, baseline was {baseline_rate:.0%}.",
                    evidence={
                        "tool": tool_name,
                        "baseline": base_stats,
                        "stress": stress_stats,
                        "baseline_error_rate": baseline_rate,
                        "stress_error_rate": stress_rate,
                    },
                    remediation="Add rate-limit detection, retry/backoff, and clear rate-limit error handling.",
                    affected_entity=tool_name,
                )
            )
        return findings

    def _check_silent_degradation(self, events: list[McpEvent]) -> list[Finding]:
        requests = {
            e.event_id: e
            for e in events
            if e.is_request and e.method == "tools/call" and e.is_stress and not e.is_probe
        }
        stats: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "empty_success": 0})

        for response in events:
            if not response.is_response or response.request_event_id not in requests:
                continue
            request = requests[response.request_event_id]
            tool_name = request.params.get("name", "unknown") if request.params else "unknown"
            stats[tool_name]["total"] += 1
            if _is_success_shaped_empty(response):
                stats[tool_name]["empty_success"] += 1

        findings = []
        for tool_name, tool_stats in stats.items():
            if tool_stats["total"] < 5:
                continue
            empty_rate = tool_stats["empty_success"] / tool_stats["total"]
            if empty_rate <= 0.5:
                continue
            findings.append(
                Finding(
                    check_id="reliability.silent-degradation",
                    severity=Severity.HIGH,
                    title=f"Tool `{tool_name}` returned empty successes under stress",
                    description=(
                        f"{tool_stats['empty_success']} of {tool_stats['total']} stress responses were "
                        "success-shaped empty payloads."
                    ),
                    evidence={"tool": tool_name, "empty_rate": empty_rate, **tool_stats},
                    remediation="Return explicit errors or backpressure signals instead of empty successful responses.",
                    affected_entity=tool_name,
                )
            )
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

    def _check_concurrent_write_integrity(self, events: list[McpEvent]) -> list[Finding]:
        requests = {
            e.event_id: e
            for e in events
            if e.is_request and e.method == "tools/call" and e.is_probe and e.probe_type in CONCURRENT_WRITE_PROBE_TYPES
        }
        samples = []
        for resp in events:
            if not (
                resp.is_response
                and resp.request_event_id in requests
                and resp.is_probe
                and resp.probe_type in CONCURRENT_WRITE_PROBE_TYPES
            ):
                continue
            error_text = _response_error_text(resp)
            if not error_text or not _contains_json_corruption_signature(error_text):
                continue
            req = requests[resp.request_event_id]
            tool_name = req.params.get("name", "unknown") if req.params else "unknown"
            samples.append(
                {
                    "tool": tool_name,
                    "probe_type": resp.probe_type,
                    "message": error_text[:300],
                }
            )

        if not samples:
            return []

        tools = sorted({sample["tool"] for sample in samples})
        return [
            Finding(
                check_id="reliability.concurrent-write-integrity",
                severity=Severity.HIGH,
                title="Concurrent write integrity probe produced JSON corruption errors",
                description=(
                    "A concurrent write burst or readback returned JSON parse/corruption errors, "
                    "which suggests non-atomic persistence or race-prone state handling."
                ),
                evidence={"tools": tools, "samples": samples[:5]},
                remediation="Serialize writes, use atomic write-and-rename, and validate persisted state after concurrent operations.",
                affected_entity=", ".join(tools),
            )
        ]

    def _check_unsafe_persistence_default(self, server_meta: ServerMeta) -> list[Finding]:
        findings: list[Finding] = []
        locations = dict(server_meta.persistence.paths)
        if server_meta.persistence.working_directory:
            locations.setdefault("working_directory", server_meta.persistence.working_directory)

        for source, raw_path in locations.items():
            resolved_path = _expand_path(raw_path)
            reason = _unsafe_persistence_reason(
                resolved_path,
                package_directory=server_meta.persistence.package_directory,
            )
            if not reason:
                continue

            findings.append(
                Finding(
                    check_id="reliability.unsafe-persistence-default",
                    severity=Severity.MEDIUM,
                    title=f"Persistence path `{source}` is {reason}",
                    description=(
                        f"`{source}` resolves to `{resolved_path}`. Paths in package, cache, or temp "
                        "directories can be removed during package updates, cache cleanup, or reboot cleanup."
                    ),
                    evidence={
                        "source": source,
                        "path": raw_path,
                        "resolved_path": resolved_path,
                        "reason": reason,
                    },
                    remediation=(
                        "Configure persistence to a durable user data directory outside node_modules, npx cache, "
                        "and temp directories."
                    ),
                    affected_entity=source,
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


def _response_error_text(resp: McpEvent) -> str:
    parts: list[str] = []
    if resp.error:
        message = resp.error.get("message")
        if isinstance(message, str):
            parts.append(message)
        data = resp.error.get("data")
        if isinstance(data, str):
            parts.append(data)
    if resp.result:
        if isinstance(resp.result.get("error"), str):
            parts.append(resp.result["error"])
        content = resp.result.get("content", [])
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
    return "\n".join(parts)


def _contains_json_corruption_signature(text: str) -> bool:
    lowered = text.lower()
    return any(signature in lowered for signature in JSON_CORRUPTION_SIGNATURES)


def _is_success_shaped_empty(response: McpEvent) -> bool:
    if response.is_error or not response.result or response.result.get("isError"):
        return False
    content = response.result.get("content")
    if not content:
        return True
    if not isinstance(content, list):
        return False
    text_items = [item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"]
    if not text_items:
        return False
    return all(_is_default_empty_text(str(text)) for text in text_items)


def _is_default_empty_text(text: str) -> bool:
    stripped = text.strip()
    if stripped.lower() in {"", "[]", "{}", "null"}:
        return True
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return False
    return _is_default_empty_value(parsed)


def _is_default_empty_value(value) -> bool:
    if value in (None, "", [], {}):
        return True
    if isinstance(value, dict):
        result_keys = {"results", "items", "data", "records", "entries"}
        return bool(value) and all(
            key in result_keys and _is_default_empty_value(nested) for key, nested in value.items()
        )
    return False


def _max_true_streak(values: list[bool]) -> int:
    max_streak = 0
    current = 0
    for value in values:
        if value:
            current += 1
            max_streak = max(max_streak, current)
        else:
            current = 0
    return max_streak


def _expand_path(path: str) -> str:
    return os.path.abspath(os.path.expandvars(os.path.expanduser(path)))


def _unsafe_persistence_reason(path: str, package_directory: str = "") -> str | None:
    parts = Path(path).parts
    if _is_npx_cache_path(path):
        return "inside the npx cache"
    if "node_modules" in parts:
        return "inside node_modules"
    if _is_temp_path(path):
        return "inside a temp directory"
    if package_directory and _is_relative_to(Path(path), Path(_expand_path(package_directory))):
        return "inside the installed package directory"
    return None


def _is_npx_cache_path(path: str) -> bool:
    parts = Path(path).parts
    for index, part in enumerate(parts[:-1]):
        if part == ".npm" and parts[index + 1] == "_npx":
            return True
    return _is_relative_to(Path(path), Path(_expand_path("~/.npm/_npx")))


def _is_temp_path(path: str) -> bool:
    temp_roots = ["/tmp", "/var/tmp"]
    tmpdir = os.environ.get("TMPDIR")
    if tmpdir:
        temp_roots.append(tmpdir)
    return any(_is_relative_to(Path(path), Path(_expand_path(root))) for root in temp_roots)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True
