"""Scenarios for @modelcontextprotocol/server-everything — protocol coverage baseline."""

SCENARIOS = [
    {
        "name": "echo_variations",
        "description": "Call echo with different message lengths for output consistency",
        "steps": [
            {"tool": "echo", "arguments": {"message": "hello"}},
            {"tool": "echo", "arguments": {"message": "a" * 100}},
            {"tool": "echo", "arguments": {"message": "a" * 1000}},
            {"tool": "echo", "arguments": {"message": "a" * 5000}},
            {"tool": "echo", "arguments": {"message": ""}},
            {"tool": "echo", "arguments": {"message": "special chars: <>&\"'\n\ttab"}},
            {"tool": "echo", "arguments": {"message": "unicode: éàüñ 你好 😀"}},
        ],
    },
    {
        "name": "echo_repeated_identical",
        "description": "Same echo call 3x — triggers redundant-calls detection",
        "steps": [
            {"tool": "echo", "arguments": {"message": "duplicate check"}},
            {"tool": "echo", "arguments": {"message": "duplicate check"}},
            {"tool": "echo", "arguments": {"message": "duplicate check"}},
        ],
    },
    {
        "name": "get_sum_arithmetic_edge_cases",
        "description": "Arithmetic with edge cases including type confusion",
        "steps": [
            {"tool": "get-sum", "arguments": {"a": 1, "b": 2}},
            {"tool": "get-sum", "arguments": {"a": 0, "b": 0}},
            {"tool": "get-sum", "arguments": {"a": -5, "b": 3}},
            {"tool": "get-sum", "arguments": {"a": 1.5, "b": 2.7}},
            {"tool": "get-sum", "arguments": {"a": 999999999, "b": 999999999}},
            {"tool": "get-sum", "arguments": {"a": "5", "b": "3"}},
        ],
    },
    {
        "name": "long_running_operation",
        "description": "Long-running op to capture progress notifications",
        "steps": [
            {"tool": "trigger-long-running-operation", "arguments": {"duration": 3, "steps": 5}},
        ],
    },
    {
        "name": "sample_llm_trigger",
        "description": "Trigger sampling request to produce orphaned sampling callback",
        "steps": [
            {"tool": "trigger-sampling-request", "arguments": {"prompt": "Say hello", "maxTokens": 50}},
        ],
    },
    {
        "name": "get_tiny_image",
        "description": "Retrieve binary-ish content to test response variety",
        "steps": [
            {"tool": "get-tiny-image", "arguments": {}},
        ],
    },
]
