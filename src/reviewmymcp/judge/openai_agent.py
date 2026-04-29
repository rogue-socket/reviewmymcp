"""OpenAI agent provider — native tool-use for the active audit agent loop."""

from __future__ import annotations

import json
import os
from typing import Any

from openai import AsyncOpenAI

from reviewmymcp.judge.base import AgentToolCall, AgentTurnResponse

DEFAULT_MODEL = "gpt-4o-mini"


class OpenAIAgentProvider:
    """Uses OpenAI's native tool-use (function calling) API for the agent loop."""

    provider_name: str = "openai"

    def __init__(self, api_key: str | None = None, model: str = ""):
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._model = model or DEFAULT_MODEL
        self._client = AsyncOpenAI(api_key=self._api_key)

    async def agent_turn(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str = "",
        max_tokens: int = 4096,
    ) -> AgentTurnResponse:
        # Convert messages to OpenAI format
        oai_messages = _convert_messages(messages, system)

        # Convert tool definitions to OpenAI function format
        oai_tools = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
                },
            }
            for t in tools
        ] if tools else []

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": oai_messages,
        }
        if oai_tools:
            kwargs["tools"] = oai_tools

        response = await self._client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        message = choice.message

        text = message.content or ""
        tool_calls = []

        if message.tool_calls:
            for tc in message.tool_calls:
                try:
                    arguments = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    arguments = {}
                tool_calls.append(
                    AgentToolCall(
                        tool_name=tc.function.name,
                        arguments=arguments,
                        call_id=tc.id,
                    )
                )

        stop_reason = ""
        if choice.finish_reason == "tool_calls":
            stop_reason = "tool_use"
        elif choice.finish_reason == "stop":
            stop_reason = "end_turn"
        elif choice.finish_reason == "length":
            stop_reason = "max_tokens"

        return AgentTurnResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            model=self._model,
        )


def _convert_messages(messages: list[dict[str, Any]], system: str) -> list[dict[str, Any]]:
    """Convert Anthropic-style messages to OpenAI format."""
    oai: list[dict[str, Any]] = []

    if system:
        oai.append({"role": "system", "content": system})

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if isinstance(content, str):
            oai.append({"role": role, "content": content})
        elif isinstance(content, list):
            if role == "assistant":
                # Convert tool_use blocks to OpenAI assistant format
                text_parts = []
                tool_calls = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block["text"])
                        elif block.get("type") == "tool_use":
                            tool_calls.append({
                                "id": block["id"],
                                "type": "function",
                                "function": {
                                    "name": block["name"],
                                    "arguments": json.dumps(block.get("input", {})),
                                },
                            })
                assistant_msg: dict[str, Any] = {"role": "assistant"}
                if text_parts:
                    assistant_msg["content"] = "\n".join(text_parts)
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls
                oai.append(assistant_msg)
            elif role == "user":
                # Convert tool_result blocks to OpenAI tool messages
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        oai.append({
                            "role": "tool",
                            "tool_call_id": block.get("tool_use_id", ""),
                            "content": block.get("content", ""),
                        })
                    elif isinstance(block, dict) and block.get("type") == "text":
                        oai.append({"role": "user", "content": block["text"]})
                    elif isinstance(block, str):
                        oai.append({"role": "user", "content": block})

    return oai
