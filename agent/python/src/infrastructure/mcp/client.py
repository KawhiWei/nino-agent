from __future__ import annotations

import asyncio
import json
from itertools import count
from typing import Any, Sequence

import httpx

from framework import ToolCall, ToolDefinition, ToolResult


class McpHttpToolClient:
    """One stateless MCP Streamable HTTP connection behind the ToolProvider Port."""

    def __init__(
        self,
        url: str,
        *,
        timeout_seconds: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = url
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None
        self._ids = count(1)
        self._initialize_lock = asyncio.Lock()
        self._initialized = False

    async def list_tools(self) -> Sequence[ToolDefinition]:
        await self._initialize()
        result = await self._request("tools/list", {})
        return tuple(
            ToolDefinition(
                name=item["name"],
                description=item.get("description", ""),
                input_schema=item.get("inputSchema", {"type": "object"}),
            )
            for item in result.get("tools", ())
        )

    async def invoke(self, call: ToolCall) -> ToolResult:
        await self._initialize()
        result = await self._request(
            "tools/call", {"name": call.name, "arguments": dict(call.arguments)}
        )
        parts: list[str] = []
        for item in result.get("content", ()):
            parts.append(item.get("text", "") if item.get("type") == "text" else json.dumps(item))
        return ToolResult("\n".join(parts), bool(result.get("isError", False)))

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _initialize(self) -> None:
        if self._initialized:
            return
        async with self._initialize_lock:
            if self._initialized:
                return
            await self._request(
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "nino-agent-runtime", "version": "0.10.0"},
                },
            )
            await self._notify("notifications/initialized", {})
            self._initialized = True

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        try:
            response = await self._client.post(
                self._url,
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json",
                },
                json={"jsonrpc": "2.0", "method": method, "params": params},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OSError(f"MCP notification failed: {method}.") from exc

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = next(self._ids)
        try:
            response = await self._client.post(
                self._url,
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json",
                },
                json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OSError(f"MCP request failed: {method}.") from exc
        payload = self._decode(response)
        if payload.get("error"):
            error = payload["error"]
            raise OSError(f"MCP {method} failed ({error.get('code')}): {error.get('message')}")
        if payload.get("id") != request_id or "result" not in payload:
            raise OSError(f"MCP {method} returned an invalid JSON-RPC response.")
        return payload["result"]

    @staticmethod
    def _decode(response: httpx.Response) -> dict[str, Any]:
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" not in content_type:
            return response.json()
        for line in response.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        raise OSError("MCP Streamable HTTP response did not contain a data event.")
