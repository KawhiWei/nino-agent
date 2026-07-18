from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from framework import Message, ToolCall, ToolDefinition

from .agents import AgentDefinition
from .react import CLARIFICATION_TOOL_NAME


PLAN_NODE_TOOL_NAME = "nino_runtime_submit_task_graph_node"
REJECT_TOOL_NAME = "nino_runtime_reject_request"


@dataclass(frozen=True, slots=True)
class PlanningDecision:
    kind: str
    calls: tuple[ToolCall, ...] = ()
    message: str = ""


class PlannerHarness:
    """Advisory model boundary that can only propose a candidate TaskGraph revision."""

    def __init__(self, model: Any, agent: AgentDefinition) -> None:
        if agent.role != "planner":
            raise ValueError(f"PlannerHarness requires a planner Agent: {agent.id}")
        self._model = model
        self._agent = agent

    async def plan(
        self,
        user_input: str,
        candidates: Sequence[tuple[AgentDefinition, Any]],
        *,
        revision: int,
        history: Sequence[Message] = (),
        node_results: Mapping[str, Mapping[str, Any]] | None = None,
        semantic_fallback: bool = False,
    ) -> PlanningDecision:
        tools = (
            self.plan_node_tool(candidates),
            self.clarification_tool(),
            *((self.reject_tool(),) if semantic_fallback else ()),
        )
        state = {
            node_id: {
                "status": result.get("status"),
                "summary": result.get("summary"),
                "concerns": result.get("concerns", []),
                "recommended_next": result.get("recommended_next", []),
            }
            for node_id, result in (node_results or {}).items()
        }
        messages = (
            Message(role="system", content=self._agent.instructions),
            Message(role="system", content=self.catalog_prompt(candidates)),
            Message(
                role="system",
                content=(
                    f"Propose TaskGraph revision {revision}. Existing compact node state:\n"
                    f"{json.dumps(state, ensure_ascii=False)}\n"
                    "Submit only new pending work. Never repeat completed nodes."
                ),
            ),
            *history,
            Message(role="user", content=user_input.strip()),
        )
        turn = await self._model.complete(messages, tools)
        if not turn.tool_calls:
            return PlanningDecision("invalid", message=turn.text.strip())
        control = tuple(call for call in turn.tool_calls if call.name != PLAN_NODE_TOOL_NAME)
        if control:
            if len(control) != 1 or len(turn.tool_calls) != 1:
                return PlanningDecision("invalid", message="A control action must be exclusive.")
            call = control[0]
            if call.name == CLARIFICATION_TOOL_NAME:
                return PlanningDecision(
                    "clarification", message=str(call.arguments.get("message", "")).strip()
                )
            if call.name == REJECT_TOOL_NAME and semantic_fallback:
                return PlanningDecision(
                    "reject", message=str(call.arguments.get("reason", "")).strip()
                )
            return PlanningDecision("invalid", message=f"Unsupported planner action: {call.name}")
        return PlanningDecision("plan", tuple(turn.tool_calls))

    @staticmethod
    def catalog_prompt(candidates: Sequence[tuple[AgentDefinition, Any]]) -> str:
        catalog = [{
            "agent_id": agent.id,
            "agent_name": agent.name,
            "agent_description": agent.description,
            "agent_capabilities": list(agent.capabilities),
            "skill_id": skill.id,
            "skill_name": skill.name,
            "skill_description": skill.description,
            "skill_capabilities": list(skill.capabilities),
            "risk_level": skill.risk_level,
            "workflow_id": skill.workflow_id,
            "workflow_execution_shape": skill.workflow_execution_shape,
            "assurance_mode": skill.assurance_mode,
        } for agent, skill in candidates]
        return (
            "Candidate capability catalog (metadata only):\n"
            + json.dumps(catalog, ensure_ascii=False)
            + "\nPropose nodes using only listed Agent + Skill pairs. Business instructions and "
            "MCP tools are unavailable to the Planner."
        )

    @staticmethod
    def plan_node_tool(candidates: Sequence[tuple[AgentDefinition, Any]]) -> ToolDefinition:
        from .orchestrator import OrchestratorHarness

        definition = OrchestratorHarness._dispatch_tool(candidates)
        return ToolDefinition(
            PLAN_NODE_TOOL_NAME,
            "Submit one node in the candidate TaskGraph revision. Multiple independent or "
            "dependent nodes may be submitted in the same turn.",
            definition.input_schema,
        )

    @staticmethod
    def clarification_tool() -> ToolDefinition:
        from .orchestrator import OrchestratorHarness

        return OrchestratorHarness._clarification_tool()

    @staticmethod
    def reject_tool() -> ToolDefinition:
        from .orchestrator import OrchestratorHarness

        return OrchestratorHarness._reject_tool()
