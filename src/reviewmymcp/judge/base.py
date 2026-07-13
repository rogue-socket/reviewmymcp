"""LLM judge provider abstraction."""

from __future__ import annotations

import asyncio
import threading
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


class SyncJudgeAdapter:
    """Synchronous wrapper around an async JudgeProvider for use in sync evaluate() methods."""

    def __init__(self, provider: JudgeProvider) -> None:
        self._provider = provider
        self._loop = asyncio.new_event_loop()
        self._loop_ready = threading.Event()
        self._lock = threading.Lock()
        self._closed = False
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._loop_ready.wait()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop_ready.set()
        self._loop.run_forever()
        self._loop.close()

    def complete(self, request: JudgeRequest) -> JudgeResponse:
        with self._lock:
            if self._closed:
                raise RuntimeError("Judge adapter is closed")
            future = asyncio.run_coroutine_threadsafe(self._provider.complete(request), self._loop)
            return future.result()

    def close(self) -> None:
        """Release provider resources and stop the private async runtime."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            aclose = getattr(self._provider, "aclose", None)
            if aclose:
                asyncio.run_coroutine_threadsafe(aclose(), self._loop).result()
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join()

    @property
    def provider_name(self) -> str:
        return self._provider.provider_name


class AgentToolCall(BaseModel):
    """A tool call decided by the LLM agent."""

    tool_name: str
    arguments: dict[str, Any] = {}
    call_id: str = ""


class AgentTurnResponse(BaseModel):
    """Response from one agent turn (may contain tool calls and/or text)."""

    text: str = ""
    tool_calls: list[AgentToolCall] = []
    stop_reason: str = ""  # "end_turn", "tool_use", "max_tokens"
    model: str = ""


@runtime_checkable
class AgentProvider(Protocol):
    """Protocol for LLM providers that support native tool-use for the agent loop."""

    provider_name: str

    async def agent_turn(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str = "",
        max_tokens: int = 4096,
    ) -> AgentTurnResponse: ...


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
