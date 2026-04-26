"""LLM judge provider abstraction."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel


class JudgeRequest(BaseModel):
    """Structured request to an LLM judge."""

    system: str
    user: str
    temperature: float = 0.0
    max_tokens: int = 1024


class JudgeResponse(BaseModel):
    """Structured response from an LLM judge."""

    raw_text: str
    parsed: dict[str, Any] | None = None
    model: str = ""
    provider: str = ""


@runtime_checkable
class JudgeProvider(Protocol):
    """Protocol for LLM judge providers."""

    provider_name: str

    async def complete(self, request: JudgeRequest) -> JudgeResponse: ...


def parse_json_response(text: str) -> dict[str, Any] | None:
    """Extract JSON from judge response text, handling markdown code fences."""
    import json

    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = lines[1:]  # drop opening fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None
