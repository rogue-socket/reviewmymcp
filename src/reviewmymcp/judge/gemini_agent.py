"""Google Gemini agent provider — native tool-use for the active audit agent loop."""

from __future__ import annotations

import os
from typing import Any

from google import genai
from google.genai.types import (
    Content,
    FunctionDeclaration,
    GenerateContentConfig,
    Part,
    Tool,
)

from reviewmymcp.judge.base import AgentToolCall, AgentTurnResponse

DEFAULT_MODEL = "gemini-2.0-flash"


class GeminiAgentProvider:
    """Uses Gemini's native function-calling API for the agent loop."""

    provider_name: str = "gemini"

    def __init__(self, api_key: str | None = None, model: str = ""):
        self._api_key = api_key or os.environ.get("GOOGLE_API_KEY", "")
        self._model = model or DEFAULT_MODEL
        self._client = genai.Client(api_key=self._api_key)

    async def agent_turn(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str = "",
        max_tokens: int = 4096,
    ) -> AgentTurnResponse:
        # Convert tool definitions to Gemini format
        gemini_tools = _build_gemini_tools(tools) if tools else []

        # Convert messages to Gemini Content format
        contents = _convert_messages(messages)

        config = GenerateContentConfig(
            system_instruction=system if system else None,
            max_output_tokens=max_tokens,
            tools=gemini_tools if gemini_tools else None,
        )

        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=contents,
            config=config,
        )

        text_parts = []
        tool_calls = []

        if response.candidates and response.candidates[0].content:
            for part in response.candidates[0].content.parts:
                if part.text:
                    text_parts.append(part.text)
                elif part.function_call:
                    fc = part.function_call
                    tool_calls.append(
                        AgentToolCall(
                            tool_name=fc.name,
                            arguments=dict(fc.args) if fc.args else {},
                            call_id=fc.id or fc.name,
                        )
                    )

        # Determine stop reason
        stop_reason = "end_turn"
        if tool_calls:
            stop_reason = "tool_use"
        elif response.candidates:
            fr = response.candidates[0].finish_reason
            if fr and fr.name == "MAX_TOKENS":
                stop_reason = "max_tokens"

        return AgentTurnResponse(
            text="\n".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            model=self._model,
        )


def _build_gemini_tools(tools: list[dict[str, Any]]) -> list[Tool]:
    """Convert MCP tool definitions to Gemini function declarations."""
    declarations = []
    for t in tools:
        schema = t.get("input_schema", {})
        # Gemini requires schemas without $schema and additionalProperties at top level
        params = {k: v for k, v in schema.items() if k not in ("$schema", "additionalProperties")}
        declarations.append(
            FunctionDeclaration(
                name=t["name"],
                description=t.get("description", ""),
                parameters=params if params else None,
            )
        )
    return [Tool(function_declarations=declarations)]


def _convert_messages(messages: list[dict[str, Any]]) -> list[Content]:
    """Convert Anthropic-style messages to Gemini Content format."""
    contents: list[Content] = []

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        gemini_role = "model" if role == "assistant" else "user"

        if isinstance(content, str):
            contents.append(Content(role=gemini_role, parts=[Part(text=content)]))
        elif isinstance(content, list):
            parts: list[Part] = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        parts.append(Part(text=block["text"]))
                    elif block.get("type") == "tool_use":
                        parts.append(
                            Part.from_function_call(
                                name=block["name"],
                                args=block.get("input", {}),
                            )
                        )
                    elif block.get("type") == "tool_result":
                        parts.append(
                            Part.from_function_response(
                                name=block.get("tool_use_id", "unknown"),
                                response={"result": block.get("content", "")},
                            )
                        )
                elif isinstance(block, str):
                    parts.append(Part(text=block))
            if parts:
                contents.append(Content(role=gemini_role, parts=parts))

    return contents
