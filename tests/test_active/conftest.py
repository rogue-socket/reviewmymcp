"""Mocks and fixtures for active audit tests."""

from __future__ import annotations

from typing import Any

from reviewmymcp.judge.base import AgentTurnResponse


class MockAgentProvider:
    """Deterministic mock that returns pre-scripted responses."""

    provider_name: str = "mock"

    def __init__(self, responses: list[AgentTurnResponse]):
        self._responses = list(responses)
        self._call_index = 0
        self.calls: list[dict[str, Any]] = []

    async def agent_turn(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str = "",
        max_tokens: int = 4096,
    ) -> AgentTurnResponse:
        self.calls.append({"messages": messages, "tools": tools, "system": system})
        if self._call_index >= len(self._responses):
            return AgentTurnResponse(text="No more scripted responses.", stop_reason="end_turn")
        response = self._responses[self._call_index]
        self._call_index += 1
        return response


class MockCallTool:
    """Mock for MCP tool calls that returns pre-scripted results."""

    def __init__(self, results: dict[str, dict[str, Any]] | None = None):
        self._results = results or {}
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        self.calls.append((name, arguments))
        if name in self._results:
            return self._results[name]
        return {"result": {"content": [{"type": "text", "text": '{"ok": true}'}], "isError": False}}
