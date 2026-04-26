"""Scenarios for @modelcontextprotocol/server-filesystem — clean baseline + security probes."""


def make_scenarios(allowed_dir: str) -> list[dict]:
    return [
        {
            "name": "write_then_read",
            "description": "Write a file then read it back — composability chain",
            "steps": [
                {
                    "tool": "write_file",
                    "arguments": {
                        "path": f"{allowed_dir}/test_write.txt",
                        "content": "Hello from the MCP log collector!",
                    },
                },
                {
                    "tool": "read_file",
                    "arguments": {"path": f"{allowed_dir}/test_write.txt"},
                },
            ],
        },
        {
            "name": "directory_operations",
            "description": "Create dir, write files, list — output structure consistency",
            "steps": [
                {
                    "tool": "create_directory",
                    "arguments": {"path": f"{allowed_dir}/subdir"},
                },
                {
                    "tool": "write_file",
                    "arguments": {
                        "path": f"{allowed_dir}/subdir/file1.txt",
                        "content": "content one",
                    },
                },
                {
                    "tool": "write_file",
                    "arguments": {
                        "path": f"{allowed_dir}/subdir/file2.py",
                        "content": "print('hello')",
                    },
                },
                {
                    "tool": "write_file",
                    "arguments": {
                        "path": f"{allowed_dir}/subdir/file3.json",
                        "content": '{"key": "value"}',
                    },
                },
                {
                    "tool": "list_directory",
                    "arguments": {"path": f"{allowed_dir}/subdir"},
                },
            ],
        },
        {
            "name": "search_pattern",
            "description": "Write files then search by pattern — composability",
            "steps": [
                {
                    "tool": "write_file",
                    "arguments": {
                        "path": f"{allowed_dir}/search_target.txt",
                        "content": "needle in a haystack",
                    },
                },
                {
                    "tool": "search_files",
                    "arguments": {"path": allowed_dir, "pattern": "needle"},
                },
            ],
        },
        {
            "name": "repeated_read",
            "description": "Read same file 3x — triggers redundant-calls",
            "steps": [
                {
                    "tool": "read_file",
                    "arguments": {"path": f"{allowed_dir}/test_write.txt"},
                },
                {
                    "tool": "read_file",
                    "arguments": {"path": f"{allowed_dir}/test_write.txt"},
                },
                {
                    "tool": "read_file",
                    "arguments": {"path": f"{allowed_dir}/test_write.txt"},
                },
            ],
        },
        {
            "name": "large_file",
            "description": "Write 50KB file — response payload bloat",
            "steps": [
                {
                    "tool": "write_file",
                    "arguments": {
                        "path": f"{allowed_dir}/large.txt",
                        "content": "x" * 50_000,
                    },
                },
                {
                    "tool": "read_file",
                    "arguments": {"path": f"{allowed_dir}/large.txt"},
                },
            ],
        },
        {
            "name": "file_info_and_move",
            "description": "Get file info then move — multi-step chain",
            "steps": [
                {
                    "tool": "write_file",
                    "arguments": {
                        "path": f"{allowed_dir}/moveme.txt",
                        "content": "to be moved",
                    },
                },
                {
                    "tool": "get_file_info",
                    "arguments": {"path": f"{allowed_dir}/moveme.txt"},
                },
                {
                    "tool": "move_file",
                    "arguments": {
                        "source": f"{allowed_dir}/moveme.txt",
                        "destination": f"{allowed_dir}/moved.txt",
                    },
                },
                {
                    "tool": "read_file",
                    "arguments": {"path": f"{allowed_dir}/moved.txt"},
                },
            ],
        },
        {
            "name": "path_traversal_probe",
            "description": "Attempt path traversal — security.excessive-permissions",
            "steps": [
                {
                    "tool": "read_file",
                    "arguments": {"path": f"{allowed_dir}/../../etc/passwd"},
                },
                {
                    "tool": "read_file",
                    "arguments": {"path": "/etc/passwd"},
                },
                {
                    "tool": "write_file",
                    "arguments": {
                        "path": f"{allowed_dir}/../outside.txt",
                        "content": "escape attempt",
                    },
                },
            ],
        },
    ]
