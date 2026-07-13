"""PII and secret redaction at ingest time."""

from __future__ import annotations

import re
from typing import Any

BUILTIN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("aws_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_token", re.compile(r"ghp_[A-Za-z0-9]{36}")),
    ("github_token", re.compile(r"gho_[A-Za-z0-9]{36}")),
    ("github_token", re.compile(r"ghs_[A-Za-z0-9]{36}")),
    ("slack_token", re.compile(r"xox[bpsar]-[A-Za-z0-9-]{10,}")),
    ("api_key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("bearer_token", re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    (
        "connection_string",
        re.compile(r"(?:postgres|mysql|mongodb|redis)://[^\s\"']+:[^\s\"']+@[^\s\"']+"),
    ),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")),
]

SENSITIVE_KEY_NAMES = {
    "apikey",
    "auth",
    "authorization",
    "clientsecret",
    "cookie",
    "cookies",
    "credential",
    "credentials",
    "password",
    "privatekey",
    "refreshtoken",
    "secret",
    "token",
}


def _compile_extra_patterns(extra: list[str]) -> list[tuple[str, re.Pattern[str]]]:
    compiled = []
    for pattern_str in extra:
        try:
            compiled.append(("custom", re.compile(pattern_str)))
        except re.error:
            pass
    return compiled


def redact_string(
    value: str,
    patterns: list[tuple[str, re.Pattern[str]]],
) -> tuple[str, list[str]]:
    """Redact secrets from a string. Returns (redacted_string, list_of_redaction_types)."""
    redaction_types: list[str] = []
    for label, pattern in patterns:
        if pattern.search(value):
            value = pattern.sub(f"[REDACTED:{label}]", value)
            redaction_types.append(label)
    return value, redaction_types


def _is_sensitive_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    return normalized in SENSITIVE_KEY_NAMES or normalized.endswith(("token", "secret", "password"))


def redact_dict(
    data: dict[str, Any],
    patterns: list[tuple[str, re.Pattern[str]]] | None = None,
    extra_patterns: list[str] | None = None,
    _path: str = "",
) -> tuple[dict[str, Any], list[str]]:
    """Recursively redact secrets from a dict. Returns (redacted_dict, redacted_field_paths)."""
    if patterns is None:
        patterns = list(BUILTIN_PATTERNS)
        if extra_patterns:
            patterns.extend(_compile_extra_patterns(extra_patterns))

    redacted_fields: list[str] = []
    result: dict[str, Any] = {}

    for key, value in data.items():
        current_path = f"{_path}.{key}" if _path else key

        if _is_sensitive_key(key) and value is not None:
            result[key] = "[REDACTED:header]"
            redacted_fields.append(current_path)
            continue

        if isinstance(value, str):
            redacted_value, types = redact_string(value, patterns)
            if types:
                redacted_fields.append(current_path)
            result[key] = redacted_value
        elif isinstance(value, dict):
            redacted_sub, sub_fields = redact_dict(value, patterns, _path=current_path)
            result[key] = redacted_sub
            redacted_fields.extend(sub_fields)
        elif isinstance(value, list):
            new_list = []
            for i, item in enumerate(value):
                item_path = f"{current_path}[{i}]"
                if isinstance(item, dict):
                    redacted_item, item_fields = redact_dict(item, patterns, _path=item_path)
                    new_list.append(redacted_item)
                    redacted_fields.extend(item_fields)
                elif isinstance(item, str):
                    redacted_item, types = redact_string(item, patterns)
                    if types:
                        redacted_fields.append(item_path)
                    new_list.append(redacted_item)
                else:
                    new_list.append(item)
            result[key] = new_list
        else:
            result[key] = value

    return result, redacted_fields
