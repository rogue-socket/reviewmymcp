"""Agent loop — drives an LLM agent that uses MCP tools to complete tasks.

The agent receives a task instruction and a list of available tools. It decides
which tools to call and how, while we record every decision and observation.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from reviewmymcp.active.task_library import AgentTask
from reviewmymcp.ingest.schema import ToolDefinition
from reviewmymcp.judge.base import JudgeProvider, JudgeRequest, parse_json_response


class ToolCallRecord(BaseModel):
    """Records a single tool call attempt by the agent."""

    turn: int
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    is_error: bool = False
    latency_ms: float | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AgentTurn(BaseModel):
    """One turn of agent reasoning + action."""

    turn_number: int
    reasoning: str = ""
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    agent_response: str = ""
    chose_to_stop: bool = False


class TaskSession(BaseModel):
    """Complete record of an agent working on a task."""

    session_id: str = Field(default_factory=lambda: str(uuid4()))
    task: AgentTask
    tools_available: list[ToolDefinition] = Field(default_factory=list)
    turns: list[AgentTurn] = Field(default_factory=list)
    completed: bool = False
    agent_gave_up: bool = False
    total_tool_calls: int = 0
    unique_tools_used: set[str] = Field(default_factory=set)
    errors_encountered: int = 0
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None

    model_config = {"arbitrary_types_allowed": True}


AGENT_SYSTEM = """\
You are an AI assistant with access to tools on an MCP server. Your job is to \
complete the task given to you using the available tools.

Available tools:
{tools_description}

IMPORTANT RULES:
- Think step by step about which tool(s) to use.
- If a tool call fails, try to understand why and adjust.
- If you cannot complete the task, explain why.
- When you are done (success or failure), set "done" to true.

Respond in JSON only:
{{
  "reasoning": "Your step-by-step thinking about what to do next",
  "tool_calls": [
    {{"tool": "tool_name", "arguments": {{"key": "value"}}}}
  ],
  "response": "Your response/summary after tool results (or if done)",
  "done": false
}}\
"""

AGENT_USER_INITIAL = """\
Task: {instruction}

Begin working on this task. Use the available tools as needed.\
"""

AGENT_USER_FOLLOWUP = """\
Tool results from your previous calls:
{tool_results}

Continue working on the task. If you're done, set "done" to true and provide your final response.\
"""


class AgentLoop:
    """Runs an LLM agent through a task, recording all interactions."""

    def __init__(
        self,
        judge: JudgeProvider,
        call_tool_fn,
        tools: list[ToolDefinition],
    ):
        self._judge = judge
        self._call_tool = call_tool_fn
        self._tools = tools

    async def run_task(self, task: AgentTask) -> TaskSession:
        session = TaskSession(task=task, tools_available=self._tools)
        tools_desc = self._format_tools()
        system = AGENT_SYSTEM.format(tools_description=tools_desc)

        user_msg = AGENT_USER_INITIAL.format(instruction=task.instruction)

        for turn_num in range(1, task.max_turns + 1):
            request = JudgeRequest(
                system=system,
                user=user_msg,
                max_tokens=2048,
                temperature=0.2,
            )

            response = await self._judge.complete(request)
            parsed = response.parsed
            if not parsed:
                parsed = parse_json_response(response.raw_text)
            if not parsed:
                parsed = {"reasoning": response.raw_text, "tool_calls": [], "response": "", "done": True}

            turn = AgentTurn(
                turn_number=turn_num,
                reasoning=parsed.get("reasoning", ""),
                agent_response=parsed.get("response", ""),
            )

            tool_results: list[dict[str, Any]] = []
            for tc in parsed.get("tool_calls", []):
                tool_name = tc.get("tool", "")
                arguments = tc.get("arguments", {})

                start = datetime.now(UTC)
                result = await self._call_tool(tool_name, arguments)
                elapsed = (datetime.now(UTC) - start).total_seconds() * 1000

                is_error = False
                if result and isinstance(result, dict):
                    is_error = "error" in result or (
                        "result" in result
                        and isinstance(result.get("result"), dict)
                        and result["result"].get("isError", False)
                    )

                record = ToolCallRecord(
                    turn=turn_num,
                    tool_name=tool_name,
                    arguments=arguments,
                    result=result,
                    is_error=is_error,
                    latency_ms=elapsed,
                )
                turn.tool_calls.append(record)
                session.total_tool_calls += 1
                session.unique_tools_used.add(tool_name)
                if is_error:
                    session.errors_encountered += 1

                tool_results.append({
                    "tool": tool_name,
                    "arguments": arguments,
                    "result": result,
                    "is_error": is_error,
                })

            done = parsed.get("done", False)
            turn.chose_to_stop = done
            session.turns.append(turn)

            if done:
                session.completed = True
                break

            if not tool_results:
                session.agent_gave_up = True
                break

            results_text = json.dumps(tool_results, indent=2, default=str)
            user_msg = AGENT_USER_FOLLOWUP.format(tool_results=results_text)

        session.finished_at = datetime.now(UTC)
        return session

    def _format_tools(self) -> str:
        parts = []
        for t in self._tools:
            schema_str = json.dumps(t.input_schema, indent=2) if t.input_schema else "{}"
            parts.append(f"- {t.name}: {t.description}\n  Input schema: {schema_str}")
        return "\n\n".join(parts)
