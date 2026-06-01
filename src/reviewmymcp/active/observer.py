"""Observer — analyzes agent sessions to produce structured observations.

Watches how an agent interacts with MCP tools and identifies quality signals:
- Did the agent find the right tool?
- Did it struggle with arguments?
- Were error messages helpful enough for recovery?
- Did tool descriptions match actual behavior?
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from reviewmymcp.active.agent_loop import TaskSession
from reviewmymcp.active.task_library import TaskCategory


class SignalType(StrEnum):
    TOOL_FOUND = "tool_found"
    TOOL_NOT_FOUND = "tool_not_found"
    WRONG_TOOL_CHOSEN = "wrong_tool_chosen"
    CORRECT_ARGS_FIRST_TRY = "correct_args_first_try"
    ARG_STRUGGLE = "arg_struggle"
    ERROR_RECOVERED = "error_recovered"
    ERROR_NOT_RECOVERED = "error_not_recovered"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    GAVE_UP = "gave_up"
    EXCESSIVE_TURNS = "excessive_turns"
    TOOL_CONFUSION = "tool_confusion"
    DESCRIPTION_MISMATCH = "description_mismatch"
    CHAINING_SUCCESS = "chaining_success"
    CHAINING_FAILURE = "chaining_failure"
    UNHELPFUL_ERROR = "unhelpful_error"
    INJECTION_IN_DESCRIPTION = "injection_in_description"
    INJECTION_IN_OUTPUT = "injection_in_output"
    UNTRUSTED_CONTENT_NO_PROVENANCE = "untrusted_content_no_provenance"


class Observation(BaseModel):
    """A single behavioral observation from an agent session."""

    signal: SignalType
    task_id: str
    category: TaskCategory
    details: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    affected_tools: list[str] = Field(default_factory=list)


class SessionAnalysis(BaseModel):
    """Aggregated analysis of a single task session."""

    session_id: str
    task_id: str
    category: TaskCategory
    observations: list[Observation] = Field(default_factory=list)
    turns_used: int = 0
    tools_called: list[str] = Field(default_factory=list)
    success: bool = False


def analyze_session(session: TaskSession) -> SessionAnalysis:
    """Analyze a completed task session and extract observations."""
    observations: list[Observation] = []
    task = session.task

    tools_used = sorted(session.unique_tools_used)
    expected = set(task.expected_tools)

    observations.extend(_check_tool_discovery(session, expected))
    observations.extend(_check_argument_handling(session))
    observations.extend(_check_error_recovery(session))
    observations.extend(_check_completion(session))
    observations.extend(_check_efficiency(session))

    if task.category == TaskCategory.MULTI_STEP:
        observations.extend(_check_chaining(session))

    observations.extend(_check_security(session))

    return SessionAnalysis(
        session_id=session.session_id,
        task_id=task.task_id,
        category=task.category,
        observations=observations,
        turns_used=len(session.turns),
        tools_called=tools_used,
        success=session.completed and not session.agent_gave_up,
    )


def _check_tool_discovery(session: TaskSession, expected: set[str]) -> list[Observation]:
    obs: list[Observation] = []
    used = session.unique_tools_used

    if not expected:
        return obs

    for tool in expected:
        if tool in used:
            obs.append(Observation(
                signal=SignalType.TOOL_FOUND,
                task_id=session.task.task_id,
                category=session.task.category,
                details=f"Agent found and used expected tool `{tool}`.",
                affected_tools=[tool],
            ))
        else:
            obs.append(Observation(
                signal=SignalType.TOOL_NOT_FOUND,
                task_id=session.task.task_id,
                category=session.task.category,
                details=f"Agent did not use expected tool `{tool}`. Tools used: {sorted(used)}.",
                evidence={"expected": tool, "used": sorted(used)},
                affected_tools=[tool],
            ))

    unexpected = used - expected
    if unexpected and expected:
        obs.append(Observation(
            signal=SignalType.WRONG_TOOL_CHOSEN,
            task_id=session.task.task_id,
            category=session.task.category,
            details=f"Agent used unexpected tools: {sorted(unexpected)} (expected: {sorted(expected)}).",
            evidence={"unexpected": sorted(unexpected), "expected": sorted(expected)},
            affected_tools=sorted(unexpected),
        ))

    return obs


def _check_argument_handling(session: TaskSession) -> list[Observation]:
    obs: list[Observation] = []
    tool_attempts: dict[str, list[bool]] = {}

    for turn in session.turns:
        for tc in turn.tool_calls:
            tool_attempts.setdefault(tc.tool_name, []).append(tc.is_error)

    for tool, results in tool_attempts.items():
        if results and not results[0]:
            obs.append(Observation(
                signal=SignalType.CORRECT_ARGS_FIRST_TRY,
                task_id=session.task.task_id,
                category=session.task.category,
                details=f"Agent called `{tool}` with correct arguments on first try.",
                affected_tools=[tool],
            ))
        elif len(results) >= 2 and results[0] and not all(results):
            obs.append(Observation(
                signal=SignalType.ARG_STRUGGLE,
                task_id=session.task.task_id,
                category=session.task.category,
                details=f"Agent needed {results.index(False) + 1} attempts to call `{tool}` correctly.",
                evidence={"attempts_before_success": results.index(False) + 1, "total_attempts": len(results)},
                affected_tools=[tool],
            ))
        elif all(results) and len(results) >= 2:
            obs.append(Observation(
                signal=SignalType.ARG_STRUGGLE,
                task_id=session.task.task_id,
                category=session.task.category,
                details=f"Agent failed all {len(results)} attempts to call `{tool}` correctly.",
                evidence={"total_attempts": len(results), "all_failed": True},
                affected_tools=[tool],
            ))

    return obs


def _check_error_recovery(session: TaskSession) -> list[Observation]:
    obs: list[Observation] = []
    if session.errors_encountered == 0:
        return obs

    error_turns = []
    recovery_after_error = False

    for turn in session.turns:
        had_error = any(tc.is_error for tc in turn.tool_calls)
        error_turns.append(had_error)

        if had_error:
            for tc in turn.tool_calls:
                if tc.is_error and tc.result:
                    error_msg = _extract_error_message(tc.result)
                    if error_msg and _is_unhelpful_error(error_msg):
                        obs.append(Observation(
                            signal=SignalType.UNHELPFUL_ERROR,
                            task_id=session.task.task_id,
                            category=session.task.category,
                            details=f"Tool `{tc.tool_name}` returned an unhelpful error: {error_msg[:200]}",
                            evidence={"tool": tc.tool_name, "error": error_msg[:500]},
                            affected_tools=[tc.tool_name],
                        ))

    for i in range(len(error_turns) - 1):
        if error_turns[i]:
            later_turns = session.turns[i + 1 :]
            any_later_success = any(
                not tc.is_error for t in later_turns for tc in t.tool_calls
            )
            if any_later_success:
                recovery_after_error = True
                break

    if recovery_after_error:
        obs.append(Observation(
            signal=SignalType.ERROR_RECOVERED,
            task_id=session.task.task_id,
            category=session.task.category,
            details="Agent recovered from errors and continued making successful calls.",
        ))
    elif session.errors_encountered > 0 and not session.completed:
        obs.append(Observation(
            signal=SignalType.ERROR_NOT_RECOVERED,
            task_id=session.task.task_id,
            category=session.task.category,
            details=f"Agent encountered {session.errors_encountered} errors and could not recover.",
            evidence={"errors": session.errors_encountered},
        ))

    return obs


def _check_completion(session: TaskSession) -> list[Observation]:
    obs: list[Observation] = []

    if session.completed and not session.agent_gave_up:
        obs.append(Observation(
            signal=SignalType.TASK_COMPLETED,
            task_id=session.task.task_id,
            category=session.task.category,
            details=f"Agent completed the task in {len(session.turns)} turns.",
            evidence={"turns": len(session.turns), "tool_calls": session.total_tool_calls},
        ))
    elif session.agent_gave_up:
        obs.append(Observation(
            signal=SignalType.GAVE_UP,
            task_id=session.task.task_id,
            category=session.task.category,
            details="Agent gave up without completing the task.",
            evidence={"turns_before_giving_up": len(session.turns)},
        ))
    else:
        obs.append(Observation(
            signal=SignalType.TASK_FAILED,
            task_id=session.task.task_id,
            category=session.task.category,
            details=f"Agent exhausted max turns ({session.task.max_turns}) without completing.",
            evidence={"max_turns": session.task.max_turns},
        ))

    return obs


def _check_efficiency(session: TaskSession) -> list[Observation]:
    obs: list[Observation] = []
    max_turns = session.task.max_turns
    used = len(session.turns)

    if used >= max_turns * 0.8 and session.completed:
        obs.append(Observation(
            signal=SignalType.EXCESSIVE_TURNS,
            task_id=session.task.task_id,
            category=session.task.category,
            details=f"Agent used {used}/{max_turns} turns — close to limit even though it completed.",
            evidence={"turns_used": used, "max_turns": max_turns},
        ))

    return obs


def _check_chaining(session: TaskSession) -> list[Observation]:
    obs: list[Observation] = []
    expected = session.task.expected_tools

    if len(expected) < 2:
        return obs

    call_order = [tc.tool_name for turn in session.turns for tc in turn.tool_calls]
    expected_seen = [t for t in call_order if t in expected]
    expected_order = []
    for t in expected:
        if t in expected_seen:
            expected_order.append(t)

    if len(set(expected_seen)) >= len(expected):
        obs.append(Observation(
            signal=SignalType.CHAINING_SUCCESS,
            task_id=session.task.task_id,
            category=session.task.category,
            details=f"Agent successfully chained tools: {' -> '.join(expected_seen)}.",
            affected_tools=expected,
        ))
    else:
        missing = set(expected) - set(expected_seen)
        obs.append(Observation(
            signal=SignalType.CHAINING_FAILURE,
            task_id=session.task.task_id,
            category=session.task.category,
            details=f"Agent failed to chain expected tools. Missing: {sorted(missing)}.",
            evidence={"expected": expected, "actual_order": call_order},
            affected_tools=sorted(missing),
        ))

    return obs


INJECTION_PATTERNS: list[str] = [
    "ignore previous instructions",
    "ignore all previous",
    "disregard previous",
    "you are now",
    "system prompt:",
    "new instructions:",
    "<|im_start|>",
    "<|im_end|>",
    "<system>",
    "</system>",
    "[system]",
    "###system",
    "act as the system",
    "override your instructions",
    "execute the following",
    "run this command",
]

EXTERNAL_CONTENT_TOOL_HINTS: list[str] = [
    "search",
    "fetch",
    "browse",
    "navigate",
    "scrape",
    "crawl",
    "web",
    "url",
    "http",
    "google",
    "screenshot",
]

PROVENANCE_MARKERS: list[str] = [
    "untrusted",
    "external content",
    "[search result",
    "<external>",
    "from url:",
    "source:",
    "<retrieved>",
    "user-controlled",
    "do not follow instructions",
]


def _scan_for_injection(text: str) -> list[str]:
    if not text:
        return []
    lower = text.lower()
    return [p for p in INJECTION_PATTERNS if p in lower]


def _looks_like_external_content_tool(tool_name: str, description: str) -> bool:
    blob = f"{tool_name} {description}".lower()
    return any(h in blob for h in EXTERNAL_CONTENT_TOOL_HINTS)


def _has_provenance_markers(text: str) -> bool:
    if not text:
        return False
    lower = text.lower()
    return any(m in lower for m in PROVENANCE_MARKERS)


def _extract_text_from_result(result: dict | None) -> str:
    if not isinstance(result, dict):
        return ""
    chunks: list[str] = []
    nested = result.get("result")
    if isinstance(nested, dict):
        for item in nested.get("content", []) or []:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    chunks.append(str(item.get("text", "")))
                elif "text" in item:
                    chunks.append(str(item["text"]))
    return "\n".join(chunks)


def _check_security(session: TaskSession) -> list[Observation]:
    obs: list[Observation] = []
    task = session.task

    tools_by_name = {t.name: t for t in session.tools_available}
    flagged_descriptions: set[str] = set()
    flagged_outputs: set[tuple[str, str]] = set()
    flagged_provenance: set[str] = set()

    for turn in session.turns:
        for tc in turn.tool_calls:
            tool = tools_by_name.get(tc.tool_name)

            if tool and tc.tool_name not in flagged_descriptions:
                hits = _scan_for_injection(tool.description or "")
                if hits:
                    flagged_descriptions.add(tc.tool_name)
                    obs.append(Observation(
                        signal=SignalType.INJECTION_IN_DESCRIPTION,
                        task_id=task.task_id,
                        category=task.category,
                        details=(
                            f"Tool `{tc.tool_name}` description contains prompt-injection-like "
                            f"phrasing: {hits}."
                        ),
                        evidence={"tool": tc.tool_name, "patterns": hits, "description_excerpt": (tool.description or "")[:300]},
                        affected_tools=[tc.tool_name],
                    ))

            if tc.is_error:
                continue
            text = _extract_text_from_result(tc.result)
            if not text:
                continue

            inj_hits = _scan_for_injection(text)
            if inj_hits and (tc.tool_name, "inj") not in flagged_outputs:
                flagged_outputs.add((tc.tool_name, "inj"))
                obs.append(Observation(
                    signal=SignalType.INJECTION_IN_OUTPUT,
                    task_id=task.task_id,
                    category=task.category,
                    details=(
                        f"Output of `{tc.tool_name}` contained prompt-injection-like patterns "
                        f"({inj_hits}). An agent could be hijacked by attacker-controlled content."
                    ),
                    evidence={"tool": tc.tool_name, "patterns": inj_hits, "output_excerpt": text[:500]},
                    affected_tools=[tc.tool_name],
                ))

            if tool and _looks_like_external_content_tool(tc.tool_name, tool.description or ""):
                if not _has_provenance_markers(text) and tc.tool_name not in flagged_provenance:
                    flagged_provenance.add(tc.tool_name)
                    obs.append(Observation(
                        signal=SignalType.UNTRUSTED_CONTENT_NO_PROVENANCE,
                        task_id=task.task_id,
                        category=task.category,
                        details=(
                            f"Tool `{tc.tool_name}` appears to return external/untrusted content "
                            f"but the response carries no provenance markers (e.g. 'source:', "
                            f"'[external]', 'do not follow instructions')."
                        ),
                        evidence={"tool": tc.tool_name, "output_excerpt": text[:500]},
                        affected_tools=[tc.tool_name],
                    ))

    return obs


def _extract_error_message(result: dict) -> str:
    if "error" in result and isinstance(result["error"], dict):
        return result["error"].get("message", str(result["error"]))
    if "error" in result:
        return str(result["error"])
    nested = result.get("result", {})
    if isinstance(nested, dict) and nested.get("isError"):
        content = nested.get("content", [])
        if content and isinstance(content, list):
            return content[0].get("text", str(content[0])) if content else ""
    return ""


def _is_unhelpful_error(msg: str) -> bool:
    unhelpful_patterns = [
        "internal error",
        "something went wrong",
        "an error occurred",
        "unknown error",
        "error processing request",
        "null",
        "undefined",
    ]
    lower = msg.lower().strip()
    if len(lower) < 10:
        return True
    return any(p in lower for p in unhelpful_patterns)
