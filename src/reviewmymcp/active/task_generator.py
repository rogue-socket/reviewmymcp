"""Generate tasks across 6 categories from MCP tool definitions."""

from __future__ import annotations

from reviewmymcp.active.models import ActiveTask, TaskCategory
from reviewmymcp.ingest.schema import ToolDefinition
from reviewmymcp.judge.base import JudgeRequest, JudgeResponse, parse_json_response

TASK_SYSTEM = """\
You generate test tasks for an MCP server's tools. Each task is a natural-language \
instruction that an LLM agent should try to accomplish using the available tools.

Return JSON only:
{{"tasks": [
  {{"category": "<category>", "description": "<what the agent should do>", \
"expected_tools": ["tool1"], "success_criteria": "<how to judge success>"}}
]}}\
"""

TASK_USER = """\
Available tools:
{tools_json}

Generate one task for each of these categories: {categories}
Each task should be a realistic instruction a user might give an agent.\
"""


class TaskGenerator:
    """Generates active-audit tasks, optionally using an LLM for richer tasks."""

    def __init__(self, judge_provider=None):
        self._judge = judge_provider

    async def generate_tasks(
        self,
        tools: list[ToolDefinition],
        categories: list[TaskCategory] | None = None,
    ) -> list[ActiveTask]:
        if categories is None:
            categories = list(TaskCategory)

        if self._judge and tools:
            try:
                return await self._llm_generate(tools, categories)
            except Exception:
                pass

        return self._fallback_generate(tools, categories)

    async def _llm_generate(
        self, tools: list[ToolDefinition], categories: list[TaskCategory]
    ) -> list[ActiveTask]:
        import json

        tools_json = json.dumps(
            [{"name": t.name, "description": t.description, "inputSchema": t.input_schema} for t in tools],
            indent=2,
        )
        request = JudgeRequest(
            system=TASK_SYSTEM,
            user=TASK_USER.format(
                tools_json=tools_json,
                categories=", ".join(c.value for c in categories),
            ),
            max_tokens=4096,
            temperature=0.3,
        )
        response: JudgeResponse = await self._judge.complete(request)

        parsed = response.parsed or parse_json_response(response.raw_text)
        if not parsed or "tasks" not in parsed:
            return self._fallback_generate(tools, categories)

        tasks = []
        for t in parsed["tasks"]:
            cat_str = t.get("category", "")
            try:
                cat = TaskCategory(cat_str)
            except ValueError:
                continue
            tasks.append(
                ActiveTask(
                    category=cat,
                    description=t.get("description", ""),
                    expected_tools=t.get("expected_tools", []),
                    success_criteria=t.get("success_criteria", ""),
                )
            )
        return tasks or self._fallback_generate(tools, categories)

    def _fallback_generate(
        self, tools: list[ToolDefinition], categories: list[TaskCategory]
    ) -> list[ActiveTask]:
        tasks: list[ActiveTask] = []
        tool_names = [t.name for t in tools]

        for cat in categories:
            if cat == TaskCategory.DISCOVERY:
                tasks.append(
                    ActiveTask(
                        category=cat,
                        description="List all available tools and describe what each one does.",
                        expected_tools=[],
                        success_criteria="Agent identifies the available tools correctly.",
                    )
                )
            elif cat == TaskCategory.SINGLE_TOOL and tools:
                t = tools[0]
                required = t.input_schema.get("required", [])
                args_hint = ", ".join(required[:2]) if required else "appropriate arguments"
                tasks.append(
                    ActiveTask(
                        category=cat,
                        description=f"Use the '{t.name}' tool with valid {args_hint}.",
                        expected_tools=[t.name],
                        success_criteria="Agent calls the tool with correct arguments and gets a result.",
                    )
                )
            elif cat == TaskCategory.MULTI_STEP and len(tools) >= 2:
                tasks.append(
                    ActiveTask(
                        category=cat,
                        description=(
                            f"First use '{tools[0].name}' to get some data, "
                            f"then use '{tools[1].name}' with that data."
                        ),
                        expected_tools=[tools[0].name, tools[1].name],
                        success_criteria="Agent chains two tools using output from the first as input to the second.",
                    )
                )
            elif cat == TaskCategory.ERROR_RECOVERY and tools:
                tasks.append(
                    ActiveTask(
                        category=cat,
                        description=f"Call '{tools[0].name}' with intentionally wrong arguments, then correct them.",
                        expected_tools=[tools[0].name],
                        success_criteria="Agent recovers from the error by adjusting arguments.",
                    )
                )
            elif cat == TaskCategory.AMBIGUOUS and tools:
                tasks.append(
                    ActiveTask(
                        category=cat,
                        description="Find information about a user. Figure out which tool(s) to use.",
                        expected_tools=tool_names[:2],
                        success_criteria="Agent selects an appropriate tool despite ambiguous instructions.",
                    )
                )
            elif cat == TaskCategory.EDGE_CASE and tools:
                tasks.append(
                    ActiveTask(
                        category=cat,
                        description=f"Call '{tools[0].name}' with empty or minimal arguments.",
                        expected_tools=[tools[0].name],
                        success_criteria="Agent handles edge case input gracefully.",
                    )
                )

        return tasks
