"""Tests for judge base utilities."""

from reviewmymcp.judge.base import JudgeRequest, JudgeResponse, parse_json_response


def test_parse_json_response_plain():
    text = '{"score": 3, "rationale": "decent"}'
    result = parse_json_response(text)
    assert result is not None
    assert result["score"] == 3


def test_parse_json_response_with_fences():
    text = '```json\n{"score": 5, "rationale": "great"}\n```'
    result = parse_json_response(text)
    assert result is not None
    assert result["score"] == 5


def test_parse_json_response_with_plain_fences():
    text = '```\n{"key": "value"}\n```'
    result = parse_json_response(text)
    assert result is not None
    assert result["key"] == "value"


def test_parse_json_response_invalid():
    result = parse_json_response("this is not json at all")
    assert result is None


def test_parse_json_response_empty():
    result = parse_json_response("")
    assert result is None


def test_judge_request_defaults():
    req = JudgeRequest(system="You are a judge", user="Score this tool")
    assert req.temperature == 0.0
    assert req.max_tokens == 1024


def test_judge_response_model():
    resp = JudgeResponse(raw_text='{"ok": true}', parsed={"ok": True}, model="test", provider="test")
    assert resp.parsed["ok"] is True
    assert resp.provider == "test"
