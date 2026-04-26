"""Scenarios for @modelcontextprotocol/server-github — large surface, auth, rate limits."""

SCENARIOS = [
    {
        "name": "search_repositories",
        "description": "Search repos by keyword — large response payloads",
        "steps": [
            {
                "tool": "search_repositories",
                "arguments": {"query": "language:python stars:>10000"},
            },
        ],
    },
    {
        "name": "get_file_contents",
        "description": "Read a known file from a public repo",
        "steps": [
            {
                "tool": "get_file_contents",
                "arguments": {
                    "owner": "modelcontextprotocol",
                    "repo": "servers",
                    "path": "README.md",
                },
            },
        ],
    },
    {
        "name": "list_issues",
        "description": "List issues from a public repo — large text content",
        "steps": [
            {
                "tool": "list_issues",
                "arguments": {
                    "owner": "modelcontextprotocol",
                    "repo": "servers",
                    "state": "open",
                    "per_page": 10,
                },
            },
        ],
    },
    {
        "name": "search_then_read_chain",
        "description": "Search repos then get file — composability chain",
        "steps": [
            {
                "tool": "search_repositories",
                "arguments": {"query": "mcp-server language:typescript"},
            },
            {
                "tool": "get_file_contents",
                "arguments": {
                    "owner": "modelcontextprotocol",
                    "repo": "servers",
                    "path": "package.json",
                },
            },
        ],
    },
    {
        "name": "rapid_fire_same_endpoint",
        "description": "10 quick calls to same endpoint — retry semantics if rate limited",
        "steps": [
            {"tool": "search_repositories", "arguments": {"query": "mcp"}}
            for _ in range(10)
        ],
    },
    {
        "name": "nonexistent_resource",
        "description": "Request a repo that doesn't exist — error message quality",
        "steps": [
            {
                "tool": "get_file_contents",
                "arguments": {
                    "owner": "nonexistent-user-abc123xyz",
                    "repo": "nonexistent-repo",
                    "path": "README.md",
                },
            },
        ],
    },
    {
        "name": "search_code",
        "description": "Search code content — exercises different response structure",
        "steps": [
            {
                "tool": "search_code",
                "arguments": {"q": "JsonRpcMessage language:typescript"},
            },
        ],
    },
    {
        "name": "repeated_identical",
        "description": "Same search 3x — redundant calls",
        "steps": [
            {
                "tool": "search_repositories",
                "arguments": {"query": "duplicate-check-mcp"},
            },
            {
                "tool": "search_repositories",
                "arguments": {"query": "duplicate-check-mcp"},
            },
            {
                "tool": "search_repositories",
                "arguments": {"query": "duplicate-check-mcp"},
            },
        ],
    },
]
