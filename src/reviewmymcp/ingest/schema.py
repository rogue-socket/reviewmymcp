"""Canonical event schema and server metadata models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Transport(StrEnum):
    STDIO = "stdio"
    HTTP = "http"


class Direction(StrEnum):
    CLIENT_TO_SERVER = "client_to_server"
    SERVER_TO_CLIENT = "server_to_client"


class McpEvent(BaseModel):
    """Normalized MCP JSON-RPC event. All ingestion paths produce these."""

    event_id: str
    timestamp: datetime
    session_id: str | None = None
    transport: Transport
    direction: Direction

    jsonrpc_id: int | str | None = None
    method: str | None = None
    is_request: bool = False
    is_response: bool = False
    is_notification: bool = False
    is_error: bool = False

    params: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    http_method: str | None = None
    http_status: int | None = None
    http_headers: dict[str, str] | None = None
    content_type: str | None = None
    sse_event_id: str | None = None

    latency_ms: float | None = None
    request_event_id: str | None = None
    task_id: str | None = None

    raw_size_bytes: int = 0
    redacted_fields: list[str] = Field(default_factory=list)
    raw_message: dict[str, Any] | None = None

    is_probe: bool = False
    probe_type: str | None = None
    is_stress: bool = False


class ToolDefinition(BaseModel):
    """A single tool from tools/list response."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    annotations: dict[str, Any] = Field(default_factory=dict)
    execution: dict[str, Any] = Field(default_factory=dict)
    required_scopes: list[str] = Field(default_factory=list)


class AuthMetadata(BaseModel):
    """Authentication envelope observed during the audit run."""

    scopes_used: list[str] = Field(default_factory=list)
    scopes_required: dict[str, list[str]] = Field(default_factory=dict)


class PersistenceMetadata(BaseModel):
    """Persistence locations known for the audited server."""

    paths: dict[str, str] = Field(default_factory=dict)
    working_directory: str = ""
    package_directory: str = ""


class RuntimeMetadata(BaseModel):
    """Runtime command and package resolution details for reproducible audits."""

    command: list[str] = Field(default_factory=list)
    package_manager: str = ""
    package_name: str = ""
    package_version: str = ""
    reproducible_command: str = ""
    executable_sha256: str = ""


class ProvenanceMetadata(BaseModel):
    """Supply-chain metadata for the audited server artifact."""

    repository_url: str = ""
    source_reachable: bool | None = None
    artifact_files: list[str] = Field(default_factory=list)
    license_declared: str = ""
    last_activity_days: int | None = None
    version_publish_times: list[str] = Field(default_factory=list)


class ServerCapabilities(BaseModel):
    """Parsed server capabilities from initialize response."""

    logging: bool = False
    prompts: bool = False
    prompts_list_changed: bool = False
    resources: bool = False
    resources_subscribe: bool = False
    resources_list_changed: bool = False
    tools: bool = False
    tools_list_changed: bool = False
    tasks: bool = False
    tasks_list: bool = False
    tasks_cancel: bool = False
    raw: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_capabilities_dict(cls, caps: dict[str, Any]) -> ServerCapabilities:
        return cls(
            logging="logging" in caps,
            prompts="prompts" in caps,
            prompts_list_changed=caps.get("prompts", {}).get("listChanged", False),
            resources="resources" in caps,
            resources_subscribe=caps.get("resources", {}).get("subscribe", False),
            resources_list_changed=caps.get("resources", {}).get("listChanged", False),
            tools="tools" in caps,
            tools_list_changed=caps.get("tools", {}).get("listChanged", False),
            tasks="tasks" in caps,
            tasks_list="list" in caps.get("tasks", {}),
            tasks_cancel="cancel" in caps.get("tasks", {}),
            raw=caps,
        )


class ClientCapabilities(BaseModel):
    """Parsed client capabilities from initialize request."""

    roots: bool = False
    sampling: bool = False
    elicitation_form: bool = False
    elicitation_url: bool = False
    tasks: bool = False
    raw: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_capabilities_dict(cls, caps: dict[str, Any]) -> ClientCapabilities:
        elicitation = caps.get("elicitation", {})
        return cls(
            roots="roots" in caps,
            sampling="sampling" in caps,
            elicitation_form="form" in elicitation,
            elicitation_url="url" in elicitation,
            tasks="tasks" in caps,
            raw=caps,
        )


class ServerMeta(BaseModel):
    """Metadata about the MCP server, extracted from the initialize handshake and tools/list."""

    server_name: str = ""
    server_version: str = ""
    protocol_version: str = ""
    instructions: str = ""
    server_capabilities: ServerCapabilities = Field(default_factory=ServerCapabilities)
    client_capabilities: ClientCapabilities = Field(default_factory=ClientCapabilities)
    tools: list[ToolDefinition] = Field(default_factory=list)
    auth: AuthMetadata = Field(default_factory=AuthMetadata)
    persistence: PersistenceMetadata = Field(default_factory=PersistenceMetadata)
    runtime: RuntimeMetadata = Field(default_factory=RuntimeMetadata)
    provenance: ProvenanceMetadata = Field(default_factory=ProvenanceMetadata)
