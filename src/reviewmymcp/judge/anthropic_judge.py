"""Anthropic Claude judge implementation.

When ANTHROPIC_API_KEY is set, uses the standard Anthropic API.
Otherwise routes through claude-agent-sdk, which talks to an authenticated
local `claude` CLI subprocess — letting the user spend their Claude Code
subscription instead of pay-per-token API credits.
"""

from __future__ import annotations

import os
from typing import Any
from uuid import uuid4

from anthropic import AsyncAnthropic

from reviewmymcp.judge.base import JudgeRequest, JudgeResponse, parse_json_response

DEFAULT_MODEL = "claude-haiku-4-5-20251001"


class AnthropicJudge:
    provider_name: str = "anthropic"

    def __init__(self, api_key: str | None = None, model: str = ""):
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._model = model or DEFAULT_MODEL
        self._use_sdk = not self._api_key
        self._client = None if self._use_sdk else AsyncAnthropic(api_key=self._api_key)
        self._sdk_clients: dict[str, Any] = {}

    async def complete(self, request: JudgeRequest) -> JudgeResponse:
        if self._use_sdk:
            return await self._complete_via_sdk(request)
        return await self._complete_via_api(request)

    async def _complete_via_api(self, request: JudgeRequest) -> JudgeResponse:
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                system=request.system,
                messages=[{"role": "user", "content": request.user}],
            )
            raw_text = response.content[0].text
            return JudgeResponse(
                raw_text=raw_text,
                parsed=parse_json_response(raw_text),
                model=self._model,
                provider=self.provider_name,
            )
        except Exception as e:
            return JudgeResponse(
                raw_text=str(e),
                parsed=None,
                model=self._model,
                provider=self.provider_name,
            )

    async def _complete_via_sdk(self, request: JudgeRequest) -> JudgeResponse:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ClaudeSDKClient,
            TextBlock,
        )

        text_parts: list[str] = []
        try:
            client = self._sdk_clients.get(request.system)
            if client is None:
                client = ClaudeSDKClient(ClaudeAgentOptions(system_prompt=request.system, max_turns=1))
                await client.connect()
                self._sdk_clients[request.system] = client
            await client.query(request.user, session_id=uuid4().hex)
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for blk in msg.content:
                        if isinstance(blk, TextBlock):
                            text_parts.append(blk.text)
            raw_text = "".join(text_parts)
            return JudgeResponse(
                raw_text=raw_text,
                parsed=parse_json_response(raw_text),
                model="claude-agent-sdk",
                provider=self.provider_name,
            )
        except Exception as e:
            return JudgeResponse(
                raw_text=str(e),
                parsed=None,
                model="claude-agent-sdk",
                provider=self.provider_name,
            )

    async def aclose(self) -> None:
        """Disconnect persistent SDK clients once an audit completes."""
        for client in self._sdk_clients.values():
            try:
                await client.disconnect()
            except Exception:
                pass
        self._sdk_clients.clear()
        if self._client is not None:
            await self._client.close()
