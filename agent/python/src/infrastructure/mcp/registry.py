from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Mapping, Sequence

from framework import ToolCall, ToolDefinition, ToolProvider, ToolResult
from .client import McpHttpToolClient
from .config import McpServerConfig


@dataclass(frozen=True, slots=True)
class McpServerStatus:
    id: str
    required: bool
    state: str
    tool_count: int = 0
    error: str | None = None


class McpServerRegistry:
    """Aggregate multiple MCP servers into the single ToolProvider seen by Runtime.

    Call chain: Runtime -> ToolProvider -> registry route -> one MCP client -> MCP server.
    The registry owns discovery and routing only; Agent/Skill allowlists remain Runtime policy.
    """

    def __init__(
        self,
        configs: Sequence[McpServerConfig],
        clients: Mapping[str, ToolProvider] | None = None,
    ) -> None:
        if not configs:
            raise ValueError("At least one MCP server is required.")
        if len({item.id for item in configs}) != len(configs):
            raise ValueError("MCP server ids must be unique.")
        self._configs = tuple(configs)
        self._clients: dict[str, ToolProvider] = dict(clients or {
            item.id: McpHttpToolClient(item.url, timeout_seconds=item.timeout_seconds)
            for item in configs
        })
        missing = {item.id for item in configs} - self._clients.keys()
        if missing:
            raise ValueError(f"Missing MCP clients: {', '.join(sorted(missing))}")
        self._tools: tuple[ToolDefinition, ...] | None = None
        self._routes: dict[str, str] = {}
        self._statuses: dict[str, McpServerStatus] = {
            item.id: McpServerStatus(item.id, item.required, "not_initialized")
            for item in configs
        }
        self._discovery_lock = asyncio.Lock()

    @property
    def statuses(self) -> tuple[McpServerStatus, ...]:
        return tuple(self._statuses[item.id] for item in self._configs)

    async def list_tools(self) -> Sequence[ToolDefinition]:
        if self._tools is None:
            await self._discover()
        return self._tools or ()

    async def invoke(self, call: ToolCall) -> ToolResult:
        if self._tools is None:
            await self._discover()
        server_id = self._routes.get(call.name)
        if server_id is None:
            raise OSError(f"No MCP server exposes tool: {call.name}")
        return await self._clients[server_id].invoke(call)

    async def refresh(self) -> Sequence[ToolDefinition]:
        async with self._discovery_lock:
            self._tools = None
            self._routes.clear()
        return await self.list_tools()

    async def close(self) -> None:
        """Close transport clients owned by this registry during API shutdown."""

        closers = []
        for client in self._clients.values():
            close = getattr(client, "close", None)
            if callable(close):
                closers.append(close())
        if closers:
            await asyncio.gather(*closers, return_exceptions=True)

    async def _discover(self) -> None:
        async with self._discovery_lock:
            if self._tools is not None:
                return
            results = await asyncio.gather(
                *(self._clients[item.id].list_tools() for item in self._configs),
                return_exceptions=True,
            )
            tools: list[ToolDefinition] = []
            routes: dict[str, str] = {}
            required_errors: list[str] = []
            for config, result in zip(self._configs, results, strict=True):
                if isinstance(result, asyncio.CancelledError):
                    raise result
                if isinstance(result, Exception):
                    message = f"{type(result).__name__}: {result}"
                    self._statuses[config.id] = McpServerStatus(
                        config.id, config.required, "unavailable", error=message
                    )
                    if config.required:
                        required_errors.append(f"{config.id} ({message})")
                    continue
                if isinstance(result, BaseException):
                    raise result
                server_tools = tuple(result)
                for tool in server_tools:
                    previous = routes.get(tool.name)
                    if previous is not None:
                        raise ValueError(
                            f"MCP tool name collision: {tool.name} from {previous} and {config.id}"
                        )
                    routes[tool.name] = config.id
                    tools.append(tool)
                self._statuses[config.id] = McpServerStatus(
                    config.id, config.required, "ready", tool_count=len(server_tools)
                )
            if required_errors:
                raise OSError("Required MCP server discovery failed: " + "; ".join(required_errors))
            self._routes = routes
            self._tools = tuple(sorted(tools, key=lambda item: item.name))
