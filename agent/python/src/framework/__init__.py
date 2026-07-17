"""Stable, infrastructure-free contracts shared by Runtime and adapters."""

from .models import (
    AgentEvent,
    HarnessStepState,
    Message,
    ModelTurn,
    RunResult,
    RunStatus,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from .conversation import (
    AgentRun,
    Conversation,
    ConversationContext,
    ConversationMessage,
    utc_now,
)
from .ports import AgentHarness, ChatModel, EventHandler, ToolProvider
from .loop import LoopBudget, LoopKind, LoopSnapshot, LoopStatus, LoopStopReason
from .repositories import AgentRepository, ConversationRepository, RunRepository

__all__ = [
    "AgentEvent",
    "AgentHarness",
    "AgentRepository",
    "AgentRun",
    "ChatModel",
    "Conversation",
    "ConversationContext",
    "ConversationMessage",
    "ConversationRepository",
    "EventHandler",
    "HarnessStepState",
    "Message",
    "LoopBudget",
    "LoopKind",
    "LoopSnapshot",
    "LoopStatus",
    "LoopStopReason",
    "ModelTurn",
    "RunResult",
    "RunRepository",
    "RunStatus",
    "ToolCall",
    "ToolDefinition",
    "ToolProvider",
    "ToolResult",
    "utc_now",
]
