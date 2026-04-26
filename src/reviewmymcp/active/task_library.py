"""Task generation for active MCP checking.

Generates realistic tasks that an LLM agent should attempt using the MCP's tools.
Tasks are designed to probe usability, discoverability, error handling, and composability.
"""

from __future__ import annotations

import json
from enum import StrEnum
from pydantic import BaseModel, Field

from reviewmymcp.ingest.schema import ToolDefinition
from reviewmymcp.judge.base import JudgeProvider, JudgeRequest, parse_json_response


class TaskCategory(StrEnum):
    DISCOVERY = "discovery"
    SINGLE_TOOL = "single_tool"
    MULTI_STEP = "multi_step"
    ERROR_RECOVERY = "error_recovery"
    AMBIGUOUS = "ambiguous"
    EDGE_CASE = "edge_case"


class AgentTask(BaseModel):
    """A task to be given to the test agent."""

    task_id: str
    category: TaskCategory
    instruction: str
    expected_tools: list[str] = Field(default_factory=list)
    success_criteria: str = ""
    max_turns: int = 10


TASK_GENERATION_SYSTEM = """\
You are generating test tasks for evaluating an MCP (Model Context Protocol) server's usability.
An LLM agent will receive these tasks and try to accomplish them using the server's tools.
We are testing how well the MCP server supports agent workflows — not the agent itself.

Generate tasks across these categories:
1. DISCOVERY: Ask the agent to figure out what the server can do. No specific tool mentioned.
2. SINGLE_TOOL: A task that requires exactly one tool call with correct arguments.
3. MULTI_STEP: A task requiring 2-4 chained tool calls where output of one feeds into another.
4. ERROR_RECOVERY: A task where the agent will likely hit an error and must recover.
5. AMBIGUOUS: A task description that could map to multiple tools — tests naming/description clarity.
6. EDGE_CASE: A task that pushes tool boundaries (large inputs, optional params, unusual combos).

Each task should be a realistic thing a user would ask an agent to do with these tools.

Return JSON only:
{{
  "tasks": [
    {{
      "task_id": "short_id",
      "category": "discovery|single_tool|multi_step|error_recovery|ambiguous|edge_case",
      "instruction": "The natural-language instruction given to the agent",
      "expected_tools": ["tool1", "tool2"],
      "success_criteria": "How to tell if the task was completed successfully",
      "max_turns": 10
    }}
  ]
}}\
"""

TASK_GENERATION_USER = """\
Available tools on this MCP server:
{tools_json}

Server name: {server_name}
Server description: {server_instructions}

Generate {count} test tasks (at least one per category where possible).\
"""


class TaskLibrary:
    """Generates and manages test tasks for active checking."""

    def __init__(self, judge: JudgeProvider | None = None):
        self._judge = judge

    async def generate_tasks(
        self,
        tools: list[ToolDefinition],
        server_name: str = "",
        server_instructions: str = "",
        count: int = 8,
    ) -> list[AgentTask]:
        if self._judge:
            try:
                return await self._llm_generate(tools, server_name, server_instructions, count)
            except Exception:
                pass
        return self._fallback_tasks(tools, count)

    async def _llm_generate(
        self,
        tools: list[ToolDefinition],
        server_name: str,
        server_instructions: str,
        count: int,
    ) -> list[AgentTask]:
        tools_json = json.dumps(
            [{"name": t.name, "description": t.description, "inputSchema": t.input_schema} for t in tools],
            indent=2,
        )

        request = JudgeRequest(
            system=TASK_GENERATION_SYSTEM,
            user=TASK_GENERATION_USER.format(
                tools_json=tools_json,
                server_name=server_name or "unknown",
                server_instructions=server_instructions or "none provided",
                count=count,
            ),
            max_tokens=4096,
            temperature=0.4,
        )

        response = await self._judge.complete(request)

        parsed = response.parsed
        if not parsed or "tasks" not in parsed:
            parsed = parse_json_response(response.raw_text)
        if not parsed or "tasks" not in parsed:
            return self._fallback_tasks(tools, count)

        tasks = []
        for t in parsed["tasks"]:
            try:
                tasks.append(AgentTask(**t))
            except Exception:
                continue
        return tasks or self._fallback_tasks(tools, count)

    def _fallback_tasks(self, tools: list[ToolDefinition], count: int) -> list[AgentTask]:
        tasks: list[AgentTask] = []

        tasks.append(
            AgentTask(
                task_id="discover_capabilities",
                category=TaskCategory.DISCOVERY,
                instruction="Explore what this server can do. List all available capabilities and describe each one briefly.",
                expected_tools=[],
                success_criteria="Agent successfully discovers and describes the available tools.",
                max_turns=5,
            )
        )

        for i, tool in enumerate(tools[:count - 2]):
            tasks.append(
                AgentTask(
                    task_id=f"use_{tool.name}",
                    category=TaskCategory.SINGLE_TOOL,
                    instruction=f"Use the server to {tool.description.lower().rstrip('.')}." if tool.description else f"Call the {tool.name} tool with appropriate arguments.",
                    expected_tools=[tool.name],
                    success_criteria=f"Agent calls {tool.name} with valid arguments and gets a successful response.",
                    max_turns=5,
                )
            )

        if len(tools) >= 2:
            t1, t2 = tools[0], tools[1]
            tasks.append(
                AgentTask(
                    task_id="multi_step_chain",
                    category=TaskCategory.MULTI_STEP,
                    instruction=f"First use {t1.name}, then use the result to inform a call to {t2.name}.",
                    expected_tools=[t1.name, t2.name],
                    success_criteria="Agent chains the two tool calls, using output from the first to inform the second.",
                    max_turns=8,
                )
            )

        tasks.append(
            AgentTask(
                task_id="handle_bad_input",
                category=TaskCategory.ERROR_RECOVERY,
                instruction="Try to use the server's tools with incomplete or incorrect inputs and see how it responds.",
                expected_tools=[tools[0].name] if tools else [],
                success_criteria="Agent encounters an error, understands it, and attempts a corrected call.",
                max_turns=6,
            )
        )

        return tasks[:count]
