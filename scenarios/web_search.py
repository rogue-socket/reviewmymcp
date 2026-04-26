"""Scenarios for pskill9/web-search — prompt injection detection surface."""

SCENARIOS = [
    {
        "name": "normal_search",
        "description": "Basic search query — baseline response",
        "steps": [
            {"tool": "web_search", "arguments": {"query": "Model Context Protocol specification"}},
        ],
    },
    {
        "name": "injection_laden_queries",
        "description": "Queries likely to return pages with prompt injection patterns",
        "steps": [
            {
                "tool": "web_search",
                "arguments": {"query": "ignore all previous instructions site:reddit.com"},
            },
            {
                "tool": "web_search",
                "arguments": {"query": "prompt injection examples in LLM tools"},
            },
            {
                "tool": "web_search",
                "arguments": {"query": "<system> override instructions jailbreak"},
            },
        ],
    },
    {
        "name": "structured_data_queries",
        "description": "Queries returning code/API docs — structured response content",
        "steps": [
            {
                "tool": "web_search",
                "arguments": {"query": "JSON-RPC 2.0 specification example request response"},
            },
            {
                "tool": "web_search",
                "arguments": {"query": "MCP tools/call JSON schema definition"},
            },
        ],
    },
    {
        "name": "verbose_queries",
        "description": "Queries returning large content — response payload bloat",
        "steps": [
            {
                "tool": "web_search",
                "arguments": {"query": "complete guide to MCP server implementation tutorial 2025"},
            },
            {
                "tool": "web_search",
                "arguments": {"query": "anthropic model context protocol full documentation"},
            },
        ],
    },
    {
        "name": "repeated_identical_search",
        "description": "Same query 3x — redundant calls",
        "steps": [
            {"tool": "web_search", "arguments": {"query": "test duplicate detection"}},
            {"tool": "web_search", "arguments": {"query": "test duplicate detection"}},
            {"tool": "web_search", "arguments": {"query": "test duplicate detection"}},
        ],
    },
    {
        "name": "html_heavy_results",
        "description": "Queries that return raw HTML with potential XSS/injection markers",
        "steps": [
            {
                "tool": "web_search",
                "arguments": {"query": "XSS payload examples owasp cheat sheet"},
            },
            {
                "tool": "web_search",
                "arguments": {"query": "SQL injection examples UNION SELECT"},
            },
        ],
    },
]
