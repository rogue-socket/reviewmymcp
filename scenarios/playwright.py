"""Scenarios for @playwright/mcp — long-running browser tasks, async lifecycle."""

SCENARIOS = [
    {
        "name": "navigate_and_snapshot",
        "description": "Navigate to a page and take a snapshot — basic flow",
        "steps": [
            {
                "tool": "browser_navigate",
                "arguments": {"url": "https://example.com"},
            },
            {
                "tool": "browser_snapshot",
                "arguments": {},
            },
        ],
    },
    {
        "name": "multi_step_interaction",
        "description": "Navigate, snapshot, click, snapshot — composability chain",
        "steps": [
            {
                "tool": "browser_navigate",
                "arguments": {"url": "https://example.com"},
            },
            {
                "tool": "browser_snapshot",
                "arguments": {},
            },
            {
                "tool": "browser_click",
                "arguments": {"element": "More information...", "ref": "a"},
            },
            {
                "tool": "browser_snapshot",
                "arguments": {},
            },
        ],
    },
    {
        "name": "navigate_multiple_sites",
        "description": "Visit different sites sequentially — varied response shapes",
        "steps": [
            {
                "tool": "browser_navigate",
                "arguments": {"url": "https://httpbin.org/html"},
            },
            {
                "tool": "browser_snapshot",
                "arguments": {},
            },
            {
                "tool": "browser_navigate",
                "arguments": {"url": "https://httpbin.org/json"},
            },
            {
                "tool": "browser_snapshot",
                "arguments": {},
            },
        ],
    },
    {
        "name": "repeated_identical_navigate",
        "description": "Navigate to same URL 3x — redundant calls",
        "steps": [
            {
                "tool": "browser_navigate",
                "arguments": {"url": "https://example.com"},
            },
            {
                "tool": "browser_navigate",
                "arguments": {"url": "https://example.com"},
            },
            {
                "tool": "browser_navigate",
                "arguments": {"url": "https://example.com"},
            },
        ],
    },
    {
        "name": "browser_tabs",
        "description": "Open new tab, navigate, list tabs — tab lifecycle",
        "steps": [
            {
                "tool": "browser_navigate",
                "arguments": {"url": "https://example.com"},
            },
            {
                "tool": "browser_tab_new",
                "arguments": {"url": "https://httpbin.org/get"},
            },
            {
                "tool": "browser_tab_list",
                "arguments": {},
            },
        ],
    },
]
