"""Google Gemini judge implementation."""

from __future__ import annotations

import os

from google import genai
from google.genai.types import GenerateContentConfig

from reviewmymcp.judge.base import JudgeRequest, JudgeResponse, parse_json_response

DEFAULT_MODEL = "gemini-2.0-flash"


class GeminiJudge:
    provider_name: str = "gemini"

    def __init__(self, api_key: str | None = None, model: str = ""):
        self._api_key = api_key or os.environ.get("GOOGLE_API_KEY", "")
        self._model = model or DEFAULT_MODEL
        self._client = genai.Client(api_key=self._api_key)

    async def complete(self, request: JudgeRequest) -> JudgeResponse:
        try:
            config = GenerateContentConfig(
                system_instruction=request.system,
                temperature=request.temperature,
                max_output_tokens=request.max_tokens,
            )
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=request.user,
                config=config,
            )
            raw_text = response.text or ""
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
