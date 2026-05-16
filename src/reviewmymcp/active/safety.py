"""Active-audit safety helpers.

Two concerns covered here:

1. ``classify_mutators`` / ``filter_readonly`` — a conservative tool-name
   heuristic that flags tools whose names suggest they mutate external state.
   The active-audit CLI uses this to refuse-by-default against servers that
   expose write-capable tools, since an LLM-driven agent loop against a live
   credentialed server can otherwise create / delete / overwrite real assets
   (the GitHub PAT incident, see DECISIONS.md and curitiba/runs/REPORT.md).

2. ``write_trace`` — per-turn JSONL of agent actions so a post-hoc audit can
   reconstruct exactly what the agent did. The scored ``AuditReport`` is
   lossy; this is the raw record.

Kept in one module because both are operator-safety features around the same
command and share no other home.
"""

from __future__ import annotations

import json
from pathlib import Path

from reviewmymcp.active.models import TaskExecution
from reviewmymcp.ingest.schema import ToolDefinition

# Conservative prefix list. False positives (e.g. ``set_logging_level``) are
# expected and acceptable — the gate's job is to surface a list the operator
# must look at, not to perfectly classify. Mirrors the read-only sampling
# heuristic in the harare collector's edge_probes.py; keep the two in sync.
WRITE_PREFIXES: tuple[str, ...] = (
    "create_", "push_", "update_", "delete_", "merge_", "fork_",
    "add_", "write_", "move_", "edit_", "remove_", "set_",
    "post_", "put_", "patch_", "upload_", "publish_", "send_",
    "commit_", "rename_", "archive_",
)


def classify_mutators(tools: list[ToolDefinition]) -> list[str]:
    """Return names of tools whose names match WRITE_PREFIXES, in input order."""
    return [t.name for t in tools if t.name.startswith(WRITE_PREFIXES)]


def filter_readonly(tools: list[ToolDefinition]) -> list[ToolDefinition]:
    """Drop tools whose names match WRITE_PREFIXES."""
    return [t for t in tools if not t.name.startswith(WRITE_PREFIXES)]


def write_trace(executions: list[TaskExecution], path: Path) -> None:
    """Write one JSONL record per (task, turn) to ``path``.

    Each record carries the agent's text response, stop reason, and every
    tool call's name / arguments / result / error status. Values are whatever
    the driver captured — the redactor in StdioAgentDriver already sanitizes
    secrets at capture time when ``redact=True``.
    """
    with path.open("w", encoding="utf-8") as f:
        for execution in executions:
            for turn in execution.turns:
                record = {
                    "task_category": str(execution.task.category),
                    "task_description": execution.task.description,
                    "turn_number": turn.turn_number,
                    "text_response": turn.text_response,
                    "stop_reason": turn.stop_reason,
                    "tool_calls": [
                        {
                            "tool_name": tc.tool_name,
                            "arguments": tc.arguments,
                            "result": tc.result,
                            "is_error": tc.is_error,
                            "call_id": tc.call_id,
                        }
                        for tc in turn.tool_calls
                    ],
                }
                f.write(json.dumps(record, default=str) + "\n")
