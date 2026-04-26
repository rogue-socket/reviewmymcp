"""Efficiency dimension evaluators."""

from __future__ import annotations

import json
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


def _build_request_map(events: list[McpEvent]) -> dict[str, McpEvent]:
    return {e.event_id: e for e in events if e.is_request and e.method == "tools/call"}


def _tool_name(request: McpEvent) -> str:
    if request.params:
        return request.params.get("name", "unknown")
    return "unknown"


class EfficiencyEvaluator:
    dimension: str = "efficiency"

    def evaluate(
        self,
        events: list[McpEvent],
        server_meta: ServerMeta,
        config: EvaluatorConfig,
    ) -> EvaluatorResult:
        findings: list[Finding] = []
        stats: dict[str, object] = {}
        request_map = _build_request_map(events)

        f, s = self._check_description_bloat(server_meta, config)
        findings.extend(f)
        stats.update(s)
        findings.extend(self._check_response_bloat(events, request_map, config))
        findings.extend(self._check_redundant_calls(events, config))
        findings.extend(self._check_latency_cliff(events, request_map, config))
        findings.extend(self._check_token_cost(events, request_map, config))

        return EvaluatorResult(
            dimension=self.dimension,
            checks_run=[
                "efficiency.description-bloat",
                "efficiency.response-payload-bloat",
                "efficiency.redundant-calls",
                "efficiency.latency-cliff",
                "efficiency.token-cost-per-task",
            ],
            findings=findings,
            stats=stats,
        )

    def _check_description_bloat(self, server_meta: ServerMeta, config: EvaluatorConfig) -> tuple[list[Finding], dict]:
        findings: list[Finding] = []
        max_single = config.thresholds.get("description_bloat_single", 500)
        max_total = config.thresholds.get("description_bloat_total", 5000)

        tool_tokens: dict[str, int] = {}
        total = 0
        for tool in server_meta.tools:
            est_tokens = len(tool.description) // 4
            tool_tokens[tool.name] = est_tokens
            total += est_tokens
            if est_tokens > max_single:
                findings.append(
                    Finding(
                        check_id="efficiency.description-bloat",
                        severity=Severity.HIGH,
                        title=f"Tool `{tool.name}` description is ~{est_tokens} tokens",
                        description=f"Single description exceeds {max_single} token threshold. This consumes context window budget.",
                        evidence={
                            "tool": tool.name,
                            "estimated_tokens": est_tokens,
                            "char_count": len(tool.description),
                        },
                        remediation="Shorten description to <300 tokens. Move examples to a separate resource.",
                        affected_entity=tool.name,
                    )
                )

        if total > max_total:
            findings.append(
                Finding(
                    check_id="efficiency.description-bloat",
                    severity=Severity.HIGH,
                    title=f"Total tool descriptions consume ~{total} tokens across {len(server_meta.tools)} tools",
                    description=f"Combined tool definitions exceed {max_total} token threshold.",
                    evidence={
                        "total_tokens": total,
                        "tool_count": len(server_meta.tools),
                        "per_tool": tool_tokens,
                    },
                    remediation="Reduce description sizes or implement progressive tool discovery.",
                )
            )

        return findings, {"total_tools": len(server_meta.tools), "total_description_tokens": total}

    def _check_response_bloat(
        self, events: list[McpEvent], request_map: dict[str, McpEvent], config: EvaluatorConfig
    ) -> list[Finding]:
        findings: list[Finding] = []
        threshold = config.thresholds.get("response_bloat_p95_bytes", 16000)

        tool_sizes: dict[str, list[int]] = defaultdict(list)
        for event in events:
            if event.is_response and event.request_event_id in request_map:
                req = request_map[event.request_event_id]
                tool_sizes[_tool_name(req)].append(event.raw_size_bytes)

        for tool_name, sizes in tool_sizes.items():
            p95 = _percentile(sizes, 95)
            if p95 > threshold:
                findings.append(
                    Finding(
                        check_id="efficiency.response-payload-bloat",
                        severity=Severity.HIGH,
                        title=f"Tool `{tool_name}` response p95 is {p95:.0f} bytes (~{p95 // 4:.0f} tokens)",
                        description=f"Responses are too large. p95={p95:.0f}B across {len(sizes)} calls.",
                        evidence={"tool": tool_name, "p95_bytes": p95, "call_count": len(sizes)},
                        remediation="Return summaries by default, paginate large results, or use resources instead.",
                        affected_entity=tool_name,
                    )
                )
        return findings

    def _check_redundant_calls(self, events: list[McpEvent], config: EvaluatorConfig) -> list[Finding]:
        findings: list[Finding] = []
        sessions: dict[str | None, list[McpEvent]] = defaultdict(list)
        for event in events:
            if event.is_request and event.method == "tools/call":
                sessions[event.session_id].append(event)

        for sid, reqs in sessions.items():
            seen: dict[str, int] = defaultdict(int)
            for req in reqs:
                if not req.params:
                    continue
                key = json.dumps(
                    {
                        "name": req.params.get("name"),
                        "arguments": req.params.get("arguments"),
                    },
                    sort_keys=True,
                )
                seen[key] += 1

            for key, count in seen.items():
                if count > 1:
                    call_info = json.loads(key)
                    findings.append(
                        Finding(
                            check_id="efficiency.redundant-calls",
                            severity=Severity.MEDIUM,
                            title=f"Tool `{call_info.get('name')}` called {count} times with identical arguments",
                            description=f"In session {sid or '(unnamed)'}, same call repeated {count} times.",
                            evidence={
                                "tool": call_info.get("name"),
                                "count": count,
                                "session": sid,
                            },
                            remediation="Consider caching, or use a resource instead of repeated tool calls.",
                            affected_entity=call_info.get("name", ""),
                        )
                    )
        return findings

    def _check_latency_cliff(
        self, events: list[McpEvent], request_map: dict[str, McpEvent], config: EvaluatorConfig
    ) -> list[Finding]:
        findings: list[Finding] = []
        p99_threshold = config.thresholds.get("latency_cliff_p99_ms", 30000)
        ratio_threshold = config.thresholds.get("latency_cliff_ratio", 10)

        tool_latencies: dict[str, list[float]] = defaultdict(list)
        for event in events:
            if event.is_response and event.latency_ms is not None and event.request_event_id in request_map:
                req = request_map[event.request_event_id]
                tool_latencies[_tool_name(req)].append(event.latency_ms)

        for tool_name, latencies in tool_latencies.items():
            if len(latencies) < 3:
                continue
            p50 = _percentile(latencies, 50)
            p99 = _percentile(latencies, 99)
            ratio = p99 / p50 if p50 > 0 else 0

            if p99 > p99_threshold or ratio > ratio_threshold:
                findings.append(
                    Finding(
                        check_id="efficiency.latency-cliff",
                        severity=Severity.HIGH,
                        title=f"Tool `{tool_name}` has p99={p99:.0f}ms (p50={p50:.0f}ms, ratio={ratio:.1f}x)",
                        description=f"High-variance latency detected across {len(latencies)} calls.",
                        evidence={
                            "tool": tool_name,
                            "p50_ms": p50,
                            "p99_ms": p99,
                            "ratio": ratio,
                            "call_count": len(latencies),
                        },
                        remediation="Use task-based async for long operations, or add a timeout parameter.",
                        affected_entity=tool_name,
                    )
                )
        return findings

    def _check_token_cost(
        self, events: list[McpEvent], request_map: dict[str, McpEvent], config: EvaluatorConfig
    ) -> list[Finding]:
        findings: list[Finding] = []
        threshold = config.thresholds.get("token_cost_bytes_per_call", 40000)

        sessions: dict[str | None, dict] = defaultdict(lambda: {"bytes": 0, "successes": 0})
        for event in events:
            sessions[event.session_id]["bytes"] += event.raw_size_bytes
            if (
                event.is_response
                and event.request_event_id in request_map
                and not event.is_error
                and event.result
                and not event.result.get("isError")
            ):
                sessions[event.session_id]["successes"] += 1

        total_bytes = 0
        total_successes = 0
        for sid, stats in sessions.items():
            total_bytes += stats["bytes"]
            total_successes += stats["successes"]

        if total_successes > 0:
            avg = total_bytes / total_successes
            if avg > threshold:
                findings.append(
                    Finding(
                        check_id="efficiency.token-cost-per-task",
                        severity=Severity.MEDIUM,
                        title=f"Average {avg:.0f} bytes (~{avg // 4:.0f} tokens) per successful tool call",
                        description=f"Total: {total_bytes} bytes across {total_successes} successful calls in {len(sessions)} sessions.",
                        evidence={
                            "avg_bytes_per_success": avg,
                            "total_bytes": total_bytes,
                            "successful_calls": total_successes,
                        },
                        remediation="Reduce tool definition sizes and response payloads to improve cost efficiency.",
                    )
                )
        return findings
