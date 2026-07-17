from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping


_SERVER_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


@dataclass(frozen=True, slots=True)
class McpServerConfig:
    id: str
    url: str
    required: bool = True
    timeout_seconds: float = 30.0
    transport: str = "streamable-http"
    max_concurrency: int = 8
    failure_threshold: int = 3
    circuit_break_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not _SERVER_ID.fullmatch(self.id):
            raise ValueError(f"Invalid MCP server id: {self.id}")
        if self.transport != "streamable-http":
            raise ValueError(f"Unsupported MCP transport: {self.transport}")
        if not self.url.startswith(("http://", "https://")):
            raise ValueError(f"MCP URL must use HTTP(S): {self.id}")
        if self.timeout_seconds <= 0:
            raise ValueError(f"MCP timeout must be positive: {self.id}")
        if self.max_concurrency < 1 or self.failure_threshold < 1:
            raise ValueError(f"MCP concurrency and failure threshold must be positive: {self.id}")
        if self.circuit_break_seconds <= 0:
            raise ValueError(f"MCP circuit break duration must be positive: {self.id}")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "McpServerConfig":
        return cls(
            id=str(value.get("id", "")).strip(),
            url=str(value.get("url", "")).strip(),
            required=bool(value.get("required", True)),
            timeout_seconds=float(value.get("timeout_seconds", 30.0)),
            transport=str(value.get("transport", "streamable-http")).strip().lower(),
            max_concurrency=int(value.get("max_concurrency", 8)),
            failure_threshold=int(value.get("failure_threshold", 3)),
            circuit_break_seconds=float(value.get("circuit_break_seconds", 30.0)),
        )


def load_mcp_server_configs(raw: str, fallback_url: str) -> tuple[McpServerConfig, ...]:
    """Load a multi-server JSON catalog, retaining NINO_MCP_URL compatibility."""

    if not raw.strip():
        return (McpServerConfig(id="nino-data", url=fallback_url),)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("NINO_MCP_SERVERS must be valid JSON.") from exc
    values = payload.get("servers") if isinstance(payload, Mapping) else payload
    if not isinstance(values, list) or not values:
        raise ValueError("NINO_MCP_SERVERS must contain a non-empty server array.")
    configs = tuple(
        McpServerConfig.from_mapping(item)
        for item in values
        if isinstance(item, Mapping)
    )
    if len(configs) != len(values):
        raise ValueError("Every MCP server entry must be an object.")
    if len({item.id for item in configs}) != len(configs):
        raise ValueError("MCP server ids must be unique.")
    return configs
