"""OpenAI judge implementation."""

from __future__ import annotations

import os

from openai import AsyncOpenAI

from reviewmymcp.judge.base import JudgeRequest, JudgeResponse, parse_json_response

DEFAULT_MODEL = "gpt-4o-mini"


class OpenAIJudge:
    provider_name: str = "openai"

    def __init__(self, api_key: str | None = None, model: str = ""):
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._model = model or DEFAULT_MODEL
        self._client = AsyncOpenAI(api_key=self._api_key)

    async def complete(self, request: JudgeRequest) -> JudgeResponse:
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                messages=[
                    {"role": "system", "content": request.system},
                    {"role": "user", "content": request.user},
                ],
            )
            raw_text = response.choices[0].message.content or ""
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
