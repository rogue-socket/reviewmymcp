"""Anthropic Claude judge implementation."""

from __future__ import annotations

import os

from anthropic import AsyncAnthropic

from reviewmymcp.judge.base import JudgeRequest, JudgeResponse, parse_json_response

DEFAULT_MODEL = "claude-haiku-4-5-20251001"


class AnthropicJudge:
    provider_name: str = "anthropic"

    def __init__(self, api_key: str | None = None, model: str = ""):
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._model = model or DEFAULT_MODEL
        self._client = AsyncAnthropic(api_key=self._api_key)

    async def complete(self, request: JudgeRequest) -> JudgeResponse:
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
