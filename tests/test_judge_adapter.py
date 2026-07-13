"""Lifecycle tests for persistent asynchronous judge providers."""

import asyncio
import sys
from types import ModuleType

from reviewmymcp.judge.anthropic_judge import AnthropicJudge
from reviewmymcp.judge.base import JudgeRequest, JudgeResponse, SyncJudgeAdapter


class RecordingProvider:
    provider_name = "recording"

    def __init__(self) -> None:
        self.loop_ids: list[int] = []
        self.closed = False

    async def complete(self, request: JudgeRequest) -> JudgeResponse:
        self.loop_ids.append(id(asyncio.get_running_loop()))
        return JudgeResponse(raw_text=request.user)

    async def aclose(self) -> None:
        self.closed = True


def test_sync_adapter_uses_one_runtime_and_closes_provider():
    provider = RecordingProvider()
    adapter = SyncJudgeAdapter(provider)

    try:
        assert adapter.complete(JudgeRequest(system="system", user="first")).raw_text == "first"
        assert adapter.complete(JudgeRequest(system="system", user="second")).raw_text == "second"
    finally:
        adapter.close()

    assert provider.loop_ids[0] == provider.loop_ids[1]
    assert provider.closed


def test_sdk_clients_are_reused_per_system_prompt_and_closed(monkeypatch):
    sdk = ModuleType("claude_agent_sdk")

    class TextBlock:
        def __init__(self, text: str) -> None:
            self.text = text

    class AssistantMessage:
        def __init__(self, content) -> None:
            self.content = content

    class ClaudeAgentOptions:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    class ClaudeSDKClient:
        instances: list["ClaudeSDKClient"] = []

        def __init__(self, options) -> None:
            self.options = options
            self.calls: list[tuple[str, str]] = []
            self.connected = False
            self.disconnected = False
            self.instances.append(self)

        async def connect(self) -> None:
            self.connected = True

        async def query(self, prompt: str, session_id: str) -> None:
            self.calls.append((prompt, session_id))

        async def receive_response(self):
            yield AssistantMessage([TextBlock('{"score": 5}')])

        async def disconnect(self) -> None:
            self.disconnected = True

    sdk.TextBlock = TextBlock
    sdk.AssistantMessage = AssistantMessage
    sdk.ClaudeAgentOptions = ClaudeAgentOptions
    sdk.ClaudeSDKClient = ClaudeSDKClient
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)

    adapter = SyncJudgeAdapter(AnthropicJudge(api_key=""))
    try:
        first = adapter.complete(JudgeRequest(system="one", user="first"))
        adapter.complete(JudgeRequest(system="one", user="second"))
        adapter.complete(JudgeRequest(system="two", user="third"))
    finally:
        adapter.close()

    assert first.parsed == {"score": 5}
    assert len(ClaudeSDKClient.instances) == 2
    first_client, second_client = ClaudeSDKClient.instances
    assert first_client.options.kwargs["system_prompt"] == "one"
    assert second_client.options.kwargs["system_prompt"] == "two"
    assert len(first_client.calls) == 2
    assert first_client.calls[0][1] != first_client.calls[1][1]
    assert all(client.connected and client.disconnected for client in ClaudeSDKClient.instances)
