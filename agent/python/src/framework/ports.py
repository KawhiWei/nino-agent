from __future__ import annotations

from typing import Awaitable, Callable, Protocol, Sequence

from .models import (
    AgentEvent, HarnessStepState, Message, ModelTurn, RunResult,
    ToolCall, ToolDefinition, ToolResult,
)


EventHandler = Callable[[AgentEvent], Awaitable[None] | None]


class AgentHarness(Protocol):
    async def step(self, state: HarnessStepState) -> ModelTurn:
        """Execute one model decision after Harness prompt and policy assembly."""

    async def run(
        self,
        user_input: str,
        history: Sequence[Message] = (),
        on_event: EventHandler | None = None,
        run_id: str | None = None,
    ) -> RunResult:
        """Execute a bounded sequence of Harness steps and observations."""


class ChatModel(Protocol):
    async def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
    ) -> ModelTurn:
        """Return final text or structured calls against the supplied tool catalog."""


class ToolProvider(Protocol):
    """Runtime-facing tool catalog; implementations may aggregate any number of MCP servers."""

    async def list_tools(self) -> Sequence[ToolDefinition]:
        """Return the global tool catalog available before Agent/Skill filtering."""

    async def invoke(self, call: ToolCall) -> ToolResult:
        """Route and execute one structured call without exposing its transport to Runtime."""
