"""Accuracy / correctness dimension evaluators."""

from __future__ import annotations

import json
import re
from collections import defaultdict

import jsonschema

from reviewmymcp.evaluators.base import (
    EvaluatorConfig,
    EvaluatorResult,
    Finding,
    Severity,
    SkippedCheck,
)
from reviewmymcp.ingest.schema import McpEvent, ServerMeta


def _build_request_map(events: list[McpEvent]) -> dict[str, McpEvent]:
    return {e.event_id: e for e in events if e.is_request and e.method == "tools/call"}


def _response_map(events: list[McpEvent]) -> dict[str, McpEvent]:
    return {e.request_event_id: e for e in events if e.is_response and e.request_event_id}


def _is_success(resp: McpEvent) -> bool:
    return not resp.is_error and (resp.result is None or not resp.result.get("isError"))


GENERIC_ERRORS = {
    "error",
    "failed",
    "internal error",
    "something went wrong",
    "unknown error",
    "an error occurred",
    "unexpected error",
    "internal server error",
    "server error",
}

ERROR_AS_CONTENT_RE = re.compile(
    r"^\s*(?:error\b|failed\b|exception\b|http\s+[45]\d\d\b|unable to\b|cannot\b|"
    r"错误|失败|无法|erro\b|falha\b|fallo\b|fehler\b|エラー|失敗)",
    re.IGNORECASE,
)


class AccuracyEvaluator:
    dimension: str = "accuracy"

    def evaluate(
        self,
        events: list[McpEvent],
        server_meta: ServerMeta,
        config: EvaluatorConfig,
    ) -> EvaluatorResult:
        findings: list[Finding] = []
        skipped: list[SkippedCheck] = []
        req_map = _build_request_map(events)
        resp_map = _response_map(events)
        tool_lookup = {t.name: t for t in server_meta.tools}

        findings.extend(self._check_schema_misuse(req_map, resp_map, tool_lookup))
        findings.extend(self._check_output_drift(events, req_map, skipped))
        findings.extend(self._check_validation_gap(req_map, resp_map, tool_lookup))
        findings.extend(self._check_error_channel_correctness(events, req_map))
        findings.extend(self._check_error_message_quality(events, req_map))
        findings.extend(self._check_description_accuracy(events, req_map, resp_map, server_meta, config, skipped))

        return EvaluatorResult(
            dimension=self.dimension,
            checks_run=[
                "accuracy.schema-misuse",
                "accuracy.output-schema-drift",
                "accuracy.argument-validation-gap",
                "accuracy.error-channel-correctness",
                "accuracy.error-message-quality",
                "accuracy.description-accuracy",
            ],
            findings=findings,
            checks_skipped=skipped,
        )

    def _check_schema_misuse(
        self,
        req_map: dict[str, McpEvent],
        resp_map: dict[str, McpEvent],
        tool_lookup: dict,
    ) -> list[Finding]:
        findings: list[Finding] = []
        misuse_counts: dict[str, dict[str, int]] = defaultdict(
            lambda: {"invalid_accepted": 0, "valid_rejected": 0, "total": 0}
        )

        for eid, req in req_map.items():
            if not req.params:
                continue
            name = req.params.get("name", "")
            args = req.params.get("arguments", {})
            tool = tool_lookup.get(name)
            if not tool or not tool.input_schema:
                continue

            resp = resp_map.get(eid)
            if not resp:
                continue

            misuse_counts[name]["total"] += 1
            schema_valid = True
            try:
                jsonschema.validate(instance=args, schema=tool.input_schema)
            except jsonschema.ValidationError:
                schema_valid = False

            is_success = _is_success(resp)

            if not schema_valid and is_success:
                misuse_counts[name]["invalid_accepted"] += 1
            elif schema_valid and not is_success:
                misuse_counts[name]["valid_rejected"] += 1

        for name, counts in misuse_counts.items():
            if counts["invalid_accepted"] > 0:
                findings.append(
                    Finding(
                        check_id="accuracy.schema-misuse",
                        severity=Severity.HIGH,
                        title=f"Tool `{name}`: {counts['invalid_accepted']} schema-invalid calls accepted",
                        description=f"Server accepted {counts['invalid_accepted']} of {counts['total']} calls that violated the declared input schema.",
                        evidence={"tool": name, **counts},
                        remediation="Either enforce schema validation or update the schema to match actual acceptance.",
                        affected_entity=name,
                    )
                )
            if counts["valid_rejected"] > 0:
                findings.append(
                    Finding(
                        check_id="accuracy.schema-misuse",
                        severity=Severity.MEDIUM,
                        title=f"Tool `{name}`: {counts['valid_rejected']} schema-valid calls rejected",
                        description=f"Server rejected {counts['valid_rejected']} of {counts['total']} calls despite valid arguments.",
                        evidence={"tool": name, **counts},
                        remediation="Schema may be overly permissive — add constraints that reflect actual validation.",
                        affected_entity=name,
                    )
                )
        return findings

    def _check_output_drift(
        self, events: list[McpEvent], req_map: dict[str, McpEvent], skipped: list[SkippedCheck]
    ) -> list[Finding]:
        findings: list[Finding] = []
        tool_outputs: dict[str, list[list[str]]] = defaultdict(list)

        for event in events:
            if event.is_probe:
                continue
            if not (event.is_response and event.request_event_id in req_map and _is_success(event)):
                continue
            req = req_map[event.request_event_id]
            if not req.params:
                continue
            name = req.params.get("name", "")
            content = event.result.get("content", []) if event.result else []
            keys: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    keys.extend(sorted(item.keys()))
            tool_outputs[name].append(keys)

        skipped_tools = 0
        for name, all_keys in tool_outputs.items():
            if len(all_keys) < 3:
                skipped_tools += 1
                continue
            key_strs = [json.dumps(k) for k in all_keys]
            from collections import Counter

            counter = Counter(key_strs)
            majority_key, majority_count = counter.most_common(1)[0]
            drift_count = len(all_keys) - majority_count
            if drift_count > 0 and drift_count / len(all_keys) > 0.1:
                findings.append(
                    Finding(
                        check_id="accuracy.output-schema-drift",
                        severity=Severity.MEDIUM,
                        title=f"Tool `{name}` output structure varies across calls",
                        description=f"{drift_count} of {len(all_keys)} responses have different structure than the majority.",
                        evidence={
                            "tool": name,
                            "majority_structure": majority_key,
                            "drift_count": drift_count,
                            "total": len(all_keys),
                        },
                        remediation="Ensure consistent response structure across all calls.",
                        affected_entity=name,
                    )
                )
        if skipped_tools:
            skipped.append(
                SkippedCheck(
                    check_id="accuracy.output-schema-drift",
                    reason=f"fewer than 3 responses for {skipped_tools} tool(s)",
                )
            )
        return findings

    def _check_error_channel_correctness(
        self,
        events: list[McpEvent],
        req_map: dict[str, McpEvent],
    ) -> list[Finding]:
        findings: list[Finding] = []
        for event in events:
            if not (
                event.is_response
                and not event.is_probe
                and event.request_event_id in req_map
                and event.result
                and not event.is_error
                and not event.result.get("isError")
            ):
                continue

            content = event.result.get("content", [])
            if not content or not isinstance(content[0], dict) or content[0].get("type") != "text":
                continue
            text = content[0].get("text", "")
            if not isinstance(text, str) or not ERROR_AS_CONTENT_RE.search(text):
                continue

            req = req_map[event.request_event_id]
            tool_name = req.params.get("name", "unknown") if req.params else "unknown"
            findings.append(
                Finding(
                    check_id="accuracy.error-channel-correctness",
                    severity=Severity.MEDIUM,
                    title=f"Tool `{tool_name}` returned error text on the success channel",
                    description="The response looks successful but its text content appears to communicate a failure.",
                    evidence={"tool": tool_name, "sample": text[:300]},
                    remediation="Set `isError: true` for tool-level failures instead of returning error text as successful content.",
                    affected_entity=tool_name,
                )
            )
        return findings

    def _check_validation_gap(
        self,
        req_map: dict[str, McpEvent],
        resp_map: dict[str, McpEvent],
        tool_lookup: dict,
    ) -> list[Finding]:
        findings: list[Finding] = []
        gaps: dict[str, int] = defaultdict(int)

        for eid, req in req_map.items():
            if not req.params:
                continue
            name = req.params.get("name", "")
            args = req.params.get("arguments", {})
            tool = tool_lookup.get(name)
            if not tool or not tool.input_schema:
                continue

            required = tool.input_schema.get("required", [])
            if not required:
                continue

            missing = [r for r in required if r not in args]
            if not missing:
                continue

            resp = resp_map.get(eid)
            if resp and _is_success(resp):
                gaps[name] += 1

        for name, count in gaps.items():
            findings.append(
                Finding(
                    check_id="accuracy.argument-validation-gap",
                    severity=Severity.HIGH,
                    title=f"Tool `{name}` accepted {count} calls with missing required arguments",
                    description="Server is not validating required fields, accepting incomplete input.",
                    evidence={"tool": name, "gap_count": count},
                    remediation="Validate all required arguments and return an error when they are missing.",
                    affected_entity=name,
                )
            )
        return findings

    def _check_description_accuracy(
        self,
        events: list[McpEvent],
        req_map: dict[str, McpEvent],
        resp_map: dict[str, McpEvent],
        server_meta: ServerMeta,
        config: EvaluatorConfig,
        skipped: list[SkippedCheck],
    ) -> list[Finding]:
        if config.judge is None:
            skipped.append(
                SkippedCheck(
                    check_id="accuracy.description-accuracy",
                    reason="LLM judge not enabled",
                )
            )
            return []

        from reviewmymcp.judge.base import JudgeRequest
        from reviewmymcp.judge.prompts import DESCRIPTION_ACCURACY_SYSTEM, DESCRIPTION_ACCURACY_USER

        findings: list[Finding] = []
        for tool in server_meta.tools:
            tool_calls = [
                (eid, req)
                for eid, req in req_map.items()
                if req.params and req.params.get("name") == tool.name and not req.is_probe
            ]
            if not tool_calls:
                continue

            total = len(tool_calls)
            successes = sum(1 for eid, _ in tool_calls if eid in resp_map and _is_success(resp_map[eid]))
            errors = total - successes

            success_outputs: list[str] = []
            error_messages: list[str] = []
            latencies: list[float] = []
            for eid, _req in tool_calls:
                resp = resp_map.get(eid)
                if not resp:
                    continue
                if resp.latency_ms:
                    latencies.append(resp.latency_ms)
                if _is_success(resp) and resp.result:
                    for item in resp.result.get("content", []):
                        if isinstance(item, dict) and item.get("type") == "text":
                            success_outputs.append(item["text"][:200])
                elif resp.error:
                    error_messages.append(resp.error.get("message", "")[:100])

            avg_latency = sum(latencies) / len(latencies) if latencies else 0

            user_prompt = DESCRIPTION_ACCURACY_USER.format(
                name=tool.name,
                description=tool.description,
                total_calls=total,
                successful_calls=successes,
                error_calls=errors,
                success_outputs=success_outputs[:3],
                error_messages=error_messages[:3],
                avg_latency_ms=f"{avg_latency:.0f}",
            )

            response = config.judge.complete(
                JudgeRequest(
                    system=DESCRIPTION_ACCURACY_SYSTEM,
                    user=user_prompt,
                )
            )

            if response.parsed is None:
                skipped.append(
                    SkippedCheck(
                        check_id="accuracy.description-accuracy",
                        reason=f"judge call failed for tool '{tool.name}'",
                    )
                )
                continue

            score = response.parsed.get("score", 5)
            if score <= 2:
                findings.append(
                    Finding(
                        check_id="accuracy.description-accuracy",
                        severity=Severity.MEDIUM,
                        title=f"Tool `{tool.name}` description inaccurate (score {score}/5)",
                        description=response.parsed.get("rationale", ""),
                        evidence={
                            "score": score,
                            "discrepancies": response.parsed.get("discrepancies", []),
                        },
                        remediation="Update the tool description to match its observed behavior.",
                        affected_entity=tool.name,
                    )
                )
        return findings

    def _check_error_message_quality(self, events: list[McpEvent], req_map: dict[str, McpEvent]) -> list[Finding]:
        findings: list[Finding] = []
        tool_errors: dict[str, list[str]] = defaultdict(list)

        for event in events:
            if not event.is_response or event.is_probe:
                continue
            is_err = event.is_error or (event.result and event.result.get("isError"))
            if not is_err:
                continue

            text = ""
            if event.error:
                text = event.error.get("message", "")
            elif event.result:
                content = event.result.get("content", [])
                if content and isinstance(content[0], dict):
                    text = content[0].get("text", "")

            tool_name = "unknown"
            if event.request_event_id in req_map:
                req = req_map[event.request_event_id]
                if req.params:
                    tool_name = req.params.get("name", "unknown")

            tool_errors[tool_name].append(text)

        for name, errors in tool_errors.items():
            poor = [e for e in errors if len(e.strip()) < 20 or e.strip().lower() in GENERIC_ERRORS]
            if poor and len(poor) / len(errors) > 0.3:
                findings.append(
                    Finding(
                        check_id="accuracy.error-message-quality",
                        severity=Severity.MEDIUM,
                        title=f"Tool `{name}`: {len(poor)} of {len(errors)} errors are too generic",
                        description="Generic error messages prevent the model from recovering or adjusting its approach.",
                        evidence={
                            "tool": name,
                            "poor_count": len(poor),
                            "total": len(errors),
                            "samples": poor[:3],
                        },
                        remediation="Include specific failure reasons in error messages.",
                        affected_entity=name,
                    )
                )
        return findings
