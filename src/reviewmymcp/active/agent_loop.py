"""Core LLM agent loop — bridges native tool-use APIs with MCP server calls."""

from __future__ import annotations

from typing import Any

from reviewmymcp.active.models import (
    ActiveTask,
    AgentTurn,
    TaskExecution,
    ToolCallAttempt,
)
from reviewmymcp.active.signal_extractor import determine_outcome, extract_task_signals
from reviewmymcp.ingest.schema import ToolDefinition
from reviewmymcp.judge.base import AgentProvider

AGENT_SYSTEM = """\
You are an AI agent testing an MCP (Model Context Protocol) server. \
You have access to the server's tools. Complete the given task using only the available tools.

If you cannot complete the task, explain why. Do not make up data.\
"""


class AgentLoop:
    """Orchestrates the LLM agent ↔ MCP server conversation."""

    def __init__(
        self,
        agent_provider: AgentProvider,
        call_tool_fn,
        tools: list[ToolDefinition],
        max_turns: int = 15,
    ):
        self._agent = agent_provider
        self._call_tool = call_tool_fn
        self._tools = tools
        self._max_turns = max_turns
        self._tool_names = {t.name for t in tools}

    async def execute_task(self, task: ActiveTask) -> TaskExecution:
        """Run the agent loop for a single task, returning the full execution trace."""
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": f"Task: {task.description}"},
        ]
        llm_tools = self._build_tool_definitions()
        turns: list[AgentTurn] = []

        for turn_num in range(1, self._max_turns + 1):
            try:
                response = await self._agent.agent_turn(
                    messages=messages,
                    tools=llm_tools,
                    system=AGENT_SYSTEM,
                )
            except Exception as exc:
                turns.append(
                    AgentTurn(
                        turn_number=turn_num,
                        text_response=f"Cannot continue: agent provider error: {exc}",
                        stop_reason="provider_error",
                    )
                )
                break

            turn = AgentTurn(
                turn_number=turn_num,
                text_response=response.text,
                stop_reason=response.stop_reason,
            )

            if not response.tool_calls:
                # Agent is done — no tool calls, just text
                turns.append(turn)
                break

            # Execute each tool call against the MCP server
            tool_results: list[dict[str, Any]] = []
            for tc in response.tool_calls:
                attempt = ToolCallAttempt(
                    tool_name=tc.tool_name,
                    arguments=tc.arguments,
                    call_id=tc.call_id,
                )

                try:
                    result = await self._call_tool(tc.tool_name, tc.arguments)
                except Exception as exc:
                    result = {"error": f"tool call failed: {exc}"}
                if result is None:
                    attempt.is_error = True
                    attempt.result = {"error": "timeout or connection error"}
                elif "error" in result:
                    attempt.is_error = True
                    attempt.result = result
                elif result.get("result", {}).get("isError"):
                    attempt.is_error = True
                    attempt.result = result.get("result", {})
                else:
                    attempt.result = result.get("result", result)

                turn.tool_calls.append(attempt)

                # Build tool_result message for the LLM
                import json

                content = json.dumps(attempt.result) if attempt.result else '{"error": "no response"}'
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": tc.call_id, "content": content}
                )

            turns.append(turn)

            # Feed assistant response + tool results back into messages
            # Build assistant message with tool_use blocks
            assistant_content: list[dict[str, Any]] = []
            if response.text:
                assistant_content.append({"type": "text", "text": response.text})
            for tc in response.tool_calls:
                assistant_content.append({
                    "type": "tool_use",
                    "id": tc.call_id,
                    "name": tc.tool_name,
                    "input": tc.arguments,
                })
            messages.append({"role": "assistant", "content": assistant_content})
            messages.append({"role": "user", "content": tool_results})

        # Extract signals and determine outcome
        tool_descriptions = {tool.name: tool.description for tool in self._tools}
        signals = extract_task_signals(turns, task, self._tool_names, self._max_turns, tool_descriptions)
        outcome = determine_outcome(signals, task)

        return TaskExecution(
            task=task,
            turns=turns,
            signals=signals,
            outcome=outcome,
            total_turns=len(turns),
        )

    def _build_tool_definitions(self) -> list[dict[str, Any]]:
        """Convert MCP ToolDefinitions to the LLM provider's tool format."""
        return [
            {
                "name": t.name,
                "description": t.description or f"Tool: {t.name}",
                "input_schema": t.input_schema,
            }
            for t in self._tools
        ]
