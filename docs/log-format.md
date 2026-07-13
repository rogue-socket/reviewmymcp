# Log Format Specification

`reviewmymcp` ingests MCP traffic logs in NDJSON (newline-delimited JSON) or JSON array format.

## Supported Formats

### NDJSON (preferred)

One JSON object per line:

```jsonl
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{...}}
{"jsonrpc":"2.0","id":1,"result":{...}}
```

### JSON Array

A single JSON array containing all messages:

```json
[
  {"jsonrpc":"2.0","id":1,"method":"initialize","params":{...}},
  {"jsonrpc":"2.0","id":1,"result":{...}}
]
```

## Message Structure

Each line can be either a **raw JSON-RPC message** or a **wrapper object** with metadata.

### Raw JSON-RPC

Standard JSON-RPC 2.0 messages are accepted directly:

```json
{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"search","arguments":{"q":"test"}}}
```

When using raw messages, direction is inferred from the message structure (requests = client-to-server, responses = server-to-client). Timestamps are set to the time of ingestion.

### Wrapper Object (recommended)

For richer metadata, wrap the JSON-RPC message with direction, timestamp, and session information:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {"name": "search", "arguments": {"q": "test"}},
  "direction": "client_to_server",
  "timestamp": "2025-01-15T10:00:00Z",
  "session_id": "sess-abc123"
}
```

Or with the message nested under a `message` key:

```json
{
  "direction": "client_to_server",
  "timestamp": "2025-01-15T10:00:00Z",
  "session_id": "sess-abc123",
  "message": {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {"name": "search", "arguments": {"q": "test"}}
  }
}
```

### Wrapper Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `direction` | string | No | `"client_to_server"` or `"server_to_client"`. Also accepts hyphenated forms. Defaults to `"client_to_server"`. |
| `timestamp` | string or number | No | ISO 8601 string (e.g., `"2025-01-15T10:00:00Z"`) or Unix timestamp. Defaults to ingestion time. |
| `session_id` | string | No | Groups messages into sessions for per-session analysis. |
| `message` | object | No | If present, the nested JSON-RPC message. Otherwise the top-level object is treated as the message. |

## Canonical Internal Schema

All ingested messages are normalized into `McpEvent` objects:

```
McpEvent {
  event_id: str               # UUID, assigned at ingest
  timestamp: datetime          # From log or capture time
  session_id: str | null       # Groups events into sessions
  transport: "stdio" | "http"
  direction: "client_to_server" | "server_to_client"

  # JSON-RPC envelope
  jsonrpc_id: int | str | null # null for notifications
  method: str | null           # null for responses (back-filled from matched request)
  is_request: bool
  is_response: bool
  is_notification: bool
  is_error: bool               # true if JSON-RPC error response

  # Parsed content
  params: dict | null
  result: dict | null
  error: dict | null

  # HTTP metadata (null for stdio)
  http_method: str | null
  http_status: int | null
  http_headers: dict | null
  content_type: str | null
  sse_event_id: str | null

  # Computed fields
  latency_ms: float | null     # Time between matched request and response
  request_event_id: str | null # Links responses back to their request
  task_id: str | null          # From _meta.io.modelcontextprotocol/related-task

  # Audit metadata
  raw_size_bytes: int          # Wire size of the original message
  redacted_fields: list[str]   # Field paths that were redacted
  raw_message: dict | null     # The original message (after redaction)
}
```

## Request-Response Correlation

The ingestion pipeline automatically:

1. **Matches requests to responses** by `(session_id, jsonrpc_id)` pairs
2. **Computes `latency_ms`** as the time delta between request and response
3. **Sets `request_event_id`** on response events, linking back to the request
4. **Back-fills `method`** on response events from the matched request
5. **Extracts `task_id`** from `_meta.io.modelcontextprotocol/related-task` fields

## PII Redaction

By default, the following patterns are redacted at ingest time (before events reach evaluators):

| Type | Pattern | Replacement |
|------|---------|-------------|
| JWT | `eyJ...` (three dot-separated base64 segments) | `[REDACTED:jwt]` |
| AWS Key | `AKIA` + 16 chars | `[REDACTED:aws_key]` |
| GitHub Token | `ghp_`/`gho_`/`ghs_` + 36 chars | `[REDACTED:github_token]` |
| Slack Token | `xox[bpsar]-` + 10+ chars | `[REDACTED:slack_token]` |
| API Key | `sk-` + 20+ chars | `[REDACTED:api_key]` |
| Bearer Token | `Bearer` + 20+ chars | `[REDACTED:bearer_token]` |
| Connection String | `postgres://user:pass@...` etc. | `[REDACTED:connection_string]` |
| SSN | `XXX-XX-XXXX` | `[REDACTED:ssn]` |
| Email | `user@domain.tld` | `[REDACTED:email]` |

Additionally, fields named `authorization`, `auth`, `token`, `password`, `secret`, `api_key`, or `apikey` are automatically redacted.

Custom patterns can be added via config:

```json
{
  "redaction": {
    "extra_patterns": ["INTERNAL-\\d{6}", "EMP-[A-Z]{2}\\d{4}"]
  }
}
```

Disable redaction with `--no-redact`.

## Generating Logs

### From the proxy

```bash
# Stdio proxy — logs all traffic between client and server
reviewmymcp watch "python -m my_server" --output-dir ./logs

# HTTP proxy — logs all traffic between client and upstream
reviewmymcp watch http://localhost:3000/mcp --transport http --output-dir ./logs
```

### From MCP SDK debug logging

Most MCP SDKs support debug-level logging that dumps wire-format messages. Enable this and capture to a file, then feed it to `reviewmymcp replay`.

### Manual capture

Use any tool that can capture JSON-RPC messages (e.g., `tee`, `mitmproxy`, custom middleware) and format them as NDJSON with the wrapper fields above.
