"""Anthropic agent provider — native tool-use for the active audit agent loop."""

from __future__ import annotations

import os
from typing import Any

from anthropic import AsyncAnthropic

from reviewmymcp.judge.base import AgentToolCall, AgentTurnResponse

DEFAULT_MODEL = "claude-haiku-4-5-20251001"


class AnthropicAgentProvider:
    """Uses Anthropic's native tool-use API for the agent loop."""

    provider_name: str = "anthropic"

    def __init__(self, api_key: str | None = None, model: str = ""):
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._model = model or DEFAULT_MODEL
        self._client = AsyncAnthropic(api_key=self._api_key)

    async def agent_turn(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str = "",
        max_tokens: int = 4096,
    ) -> AgentTurnResponse:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        response = await self._client.messages.create(**kwargs)

        text_parts = []
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    AgentToolCall(
                        tool_name=block.name,
                        arguments=block.input if isinstance(block.input, dict) else {},
                        call_id=block.id,
                    )
                )

        return AgentTurnResponse(
            text="\n".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=response.stop_reason or "",
            model=self._model,
        )
