"""LLM-driven scenario planning — generates task scenarios from tool definitions."""

from __future__ import annotations

import json

from reviewmymcp.ingest.schema import ToolDefinition
from reviewmymcp.judge.base import JudgeRequest, JudgeResponse, parse_json_response

SCENARIO_SYSTEM = """\
You are generating realistic test scenarios for an MCP (Model Context Protocol) server.
Given a list of tools, generate {count} task scenarios that exercise the tools in realistic ways.

Each scenario should:
- Describe a concrete task a user/agent would perform
- List the expected sequence of tool calls with example arguments
- Cover different tools and combinations

Return JSON only:
{{
  "scenarios": [
    {{
      "name": "short_name",
      "description": "What the agent is trying to do",
      "steps": [
        {{"tool": "tool_name", "arguments": {{"key": "value"}}, "expect": "success|error"}}
      ]
    }}
  ]
}}\
"""

SCENARIO_USER = """\
Available tools:
{tools_json}

Generate {count} test scenarios.\
"""


class ScenarioPlanner:
    def __init__(self, judge_provider):
        self._judge = judge_provider

    async def plan_scenarios(self, tools: list[ToolDefinition], count: int = 10) -> list[dict]:
        tools_json = json.dumps(
            [{"name": t.name, "description": t.description, "inputSchema": t.input_schema} for t in tools],
            indent=2,
        )

        request = JudgeRequest(
            system=SCENARIO_SYSTEM.format(count=count),
            user=SCENARIO_USER.format(tools_json=tools_json, count=count),
            max_tokens=4096,
            temperature=0.3,
        )

        response: JudgeResponse = await self._judge.complete(request)

        if response.parsed and "scenarios" in response.parsed:
            return response.parsed["scenarios"]

        parsed = parse_json_response(response.raw_text)
        if parsed and "scenarios" in parsed:
            return parsed["scenarios"]

        return self._fallback_scenarios(tools, count)

    def _fallback_scenarios(self, tools: list[ToolDefinition], count: int) -> list[dict]:
        scenarios = []
        for i, tool in enumerate(tools[:count]):
            required = tool.input_schema.get("required", [])
            properties = tool.input_schema.get("properties", {})
            args = {}
            for field in required:
                prop = properties.get(field, {})
                t = prop.get("type", "string")
                if t == "string":
                    args[field] = f"test_{field}"
                elif t == "integer":
                    args[field] = 1
                elif t == "number":
                    args[field] = 1.0
                elif t == "boolean":
                    args[field] = True
                else:
                    args[field] = f"test_{field}"

            scenarios.append(
                {
                    "name": f"basic_{tool.name}",
                    "description": f"Basic test: call {tool.name} with valid arguments",
                    "steps": [{"tool": tool.name, "arguments": args, "expect": "success"}],
                }
            )
        return scenarios
