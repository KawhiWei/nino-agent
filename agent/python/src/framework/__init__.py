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
from .repositories import (
    ActiveRunConflictError, AgentRepository, ConversationRepository, RunRepository,
    TaskGraphRepository,
)
from .task_graph import (
    AcceptanceContract,
    AttemptStatus,
    GateStatus,
    NodeAttempt,
    TaskGate,
    TaskGraph,
    TaskGraphSnapshot,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)

__all__ = [
    "AgentEvent",
    "ActiveRunConflictError",
    "AgentHarness",
    "AgentRepository",
    "AgentRun",
    "AcceptanceContract",
    "AttemptStatus",
    "ChatModel",
    "Conversation",
    "ConversationContext",
    "ConversationMessage",
    "ConversationRepository",
    "EventHandler",
    "HarnessStepState",
    "GateStatus",
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
    "NodeAttempt",
    "TaskGate",
    "TaskGraph",
    "TaskGraphRepository",
    "TaskGraphSnapshot",
    "TaskGraphStatus",
    "TaskNode",
    "TaskNodeStatus",
    "ToolCall",
    "ToolDefinition",
    "ToolProvider",
    "ToolResult",
    "utc_now",
]
