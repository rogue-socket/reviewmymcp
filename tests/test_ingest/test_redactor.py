"""Tests for PII/secret redaction."""

from reviewmymcp.ingest.redactor import BUILTIN_PATTERNS, redact_dict, redact_string


def test_redact_jwt():
    text = "token: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.abc123def456ghi789"
    result, types = redact_string(text, BUILTIN_PATTERNS)
    assert "[REDACTED:jwt]" in result
    assert "jwt" in types


def test_redact_aws_key():
    text = "key: AKIAIOSFODNN7EXAMPLE"
    result, types = redact_string(text, BUILTIN_PATTERNS)
    assert "[REDACTED:aws_key]" in result


def test_redact_github_token():
    text = "token: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"
    result, types = redact_string(text, BUILTIN_PATTERNS)
    assert "[REDACTED:github_token]" in result


def test_redact_email():
    text = "contact: user@example.com for help"
    result, types = redact_string(text, BUILTIN_PATTERNS)
    assert "[REDACTED:email]" in result


def test_redact_ssn():
    text = "SSN: 123-45-6789"
    result, types = redact_string(text, BUILTIN_PATTERNS)
    assert "[REDACTED:ssn]" in result


def test_redact_connection_string():
    text = "url: postgres://admin:s3cret@db.host:5432/mydb"
    result, types = redact_string(text, BUILTIN_PATTERNS)
    assert "[REDACTED:connection_string]" in result


def test_redact_bearer_token():
    text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.test.signature"
    result, types = redact_string(text, BUILTIN_PATTERNS)
    assert "[REDACTED:" in result


def test_redact_dict_recursive():
    data = {
        "name": "test",
        "config": {"database_url": "postgres://user:pass@host:5432/db"},
        "items": [{"email": "test@example.com"}],
    }
    redacted, fields = redact_dict(data)
    assert "[REDACTED:connection_string]" in redacted["config"]["database_url"]
    assert "[REDACTED:email]" in redacted["items"][0]["email"]
    assert len(fields) == 2


def test_redact_dict_sensitive_keys():
    data = {"authorization": "Bearer secret123456789012345", "name": "safe"}
    redacted, fields = redact_dict(data)
    assert redacted["authorization"] == "[REDACTED:header]"
    assert redacted["name"] == "safe"
    assert "authorization" in fields


def test_redact_dict_sensitive_key_variants_and_nested_credentials():
    data = {
        "access_token": "short-token",
        "refreshToken": "another-token",
        "client_secret": "client-secret",
        "cookies": {"session": "session-secret"},
        "credentials": {"password": "password-secret"},
    }

    redacted, fields = redact_dict(data)

    assert all(value == "[REDACTED:header]" for value in redacted.values())
    assert set(fields) == set(data)


def test_redact_dict_extra_patterns():
    data = {"employee_id": "EMP-AB1234"}
    redacted, fields = redact_dict(data, extra_patterns=[r"EMP-[A-Z]{2}\d{4}"])
    assert "[REDACTED:custom]" in redacted["employee_id"]


def test_no_redaction_on_clean_data():
    data = {"name": "John", "count": 42}
    redacted, fields = redact_dict(data)
    assert redacted == data
    assert fields == []


def test_redact_preserves_non_string_values():
    data = {"count": 42, "active": True, "tags": [1, 2, 3]}
    redacted, fields = redact_dict(data)
    assert redacted["count"] == 42
    assert redacted["active"] is True
    assert redacted["tags"] == [1, 2, 3]
