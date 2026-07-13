"""Tests for request-response correlation."""

from datetime import UTC, datetime, timedelta

import pytest

from reviewmymcp.ingest.correlator import correlate, get_sessions
from tests.conftest import make_event


def test_correlate_matches_by_jsonrpc_id():
    req = make_event(
        event_id="req-1",
        method="tools/call",
        is_request=True,
        jsonrpc_id=1,
        session_id="s1",
        timestamp=datetime(2025, 1, 1, tzinfo=UTC),
    )
    resp = make_event(
        event_id="resp-1",
        is_response=True,
        jsonrpc_id=1,
        session_id="s1",
        timestamp=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(milliseconds=150),
    )

    events = correlate([req, resp])
    assert events[1].request_event_id == "req-1"
    assert events[1].latency_ms == pytest.approx(150, abs=1)
    assert events[1].method == "tools/call"


def test_correlate_backfills_method():
    req = make_event(
        event_id="req-1",
        method="tools/list",
        is_request=True,
        jsonrpc_id=5,
        timestamp=datetime(2025, 1, 1, tzinfo=UTC),
    )
    resp = make_event(
        event_id="resp-1",
        method=None,
        is_response=True,
        jsonrpc_id=5,
        timestamp=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(milliseconds=50),
    )

    events = correlate([req, resp])
    assert events[1].method == "tools/list"


def test_correlate_extracts_task_id():
    event = make_event(
        is_response=True,
        result={"_meta": {"io.modelcontextprotocol/related-task": {"taskId": "t-42"}}},
    )
    events = correlate([event])
    assert events[0].task_id == "t-42"


def test_correlate_handles_no_match():
    req = make_event(event_id="req-1", is_request=True, jsonrpc_id=1, session_id="s1")
    resp = make_event(event_id="resp-1", is_response=True, jsonrpc_id=99, session_id="s1")
    events = correlate([req, resp])
    assert events[1].request_event_id is None
    assert events[1].latency_ms is None


def test_correlate_respects_session_boundary():
    req = make_event(event_id="req-1", is_request=True, jsonrpc_id=1, session_id="s1")
    resp = make_event(event_id="resp-1", is_response=True, jsonrpc_id=1, session_id="s2")
    events = correlate([req, resp])
    assert events[1].request_event_id is None


def test_correlate_handles_sequential_reuse_of_jsonrpc_id():
    base = datetime(2025, 1, 1, tzinfo=UTC)
    events = correlate(
        [
            make_event(event_id="req-1", method="tools/list", is_request=True, jsonrpc_id=1, timestamp=base),
            make_event(event_id="resp-1", is_response=True, jsonrpc_id=1, timestamp=base + timedelta(milliseconds=10)),
            make_event(
                event_id="req-2",
                method="tools/call",
                is_request=True,
                jsonrpc_id=1,
                timestamp=base + timedelta(milliseconds=20),
            ),
            make_event(event_id="resp-2", is_response=True, jsonrpc_id=1, timestamp=base + timedelta(milliseconds=50)),
        ]
    )

    assert events[1].request_event_id == "req-1"
    assert events[1].method == "tools/list"
    assert events[1].latency_ms == pytest.approx(10, abs=1)
    assert events[3].request_event_id == "req-2"
    assert events[3].method == "tools/call"
    assert events[3].latency_ms == pytest.approx(30, abs=1)


def test_get_sessions():
    events = [
        make_event(session_id="a"),
        make_event(session_id="a"),
        make_event(session_id="b"),
        make_event(session_id=None),
    ]
    sessions = get_sessions(events)
    assert len(sessions["a"]) == 2
    assert len(sessions["b"]) == 1
    assert len(sessions[None]) == 1
