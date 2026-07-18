from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from framework import LoopBudget
from .documents import DocumentConfigurationError, load_instruction_document


class AgentConfigurationError(ValueError):
    """Raised when a shared Agent definition violates Harness constraints."""

    pass


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    id: str
    name: str
    description: str
    instructions: str
    mode: str
    role: str
    evaluator_kind: str | None
    allowed_skills: frozenset[str]
    allowed_tools: frozenset[str]
    allowed_delegates: frozenset[str]
    capabilities: tuple[str, ...]
    accepted_capabilities: frozenset[str]
    accepted_risk_levels: frozenset[str]
    tool_policy: str
    discover_delegates: bool
    loop_budget: LoopBudget
    max_steps: int
    max_delegation_depth: int

    @property
    def can_delegate(self) -> bool:
        return (
            bool(self.allowed_delegates) or self.discover_delegates
        ) and self.max_delegation_depth > 0

    def accepts_skill(self, skill: Any) -> bool:
        """Apply explicit bindings first, then the generic role compatibility policy."""

        if self.allowed_skills:
            return skill.id in self.allowed_skills
        if self.role not in {"worker", "evaluator"}:
            return False
        if self.accepted_risk_levels and skill.risk_level not in self.accepted_risk_levels:
            return False
        return not self.accepted_capabilities or bool(
            self.accepted_capabilities.intersection(skill.capabilities)
        )

    def effective_tools(self, skill: Any) -> frozenset[str]:
        if self.tool_policy == "selected-skill-only":
            return skill.allowed_tools
        return skill.allowed_tools & self.allowed_tools


class AgentRegistry:
    def __init__(self, agents: tuple[AgentDefinition, ...]) -> None:
        if not agents:
            raise AgentConfigurationError("At least one agent is required.")
        if len({agent.id for agent in agents}) != len(agents):
            raise AgentConfigurationError("Agent ids must be unique.")
        if sum(agent.mode == "primary" for agent in agents) != 1:
            raise AgentConfigurationError("Exactly one primary agent is required.")
        if sum(agent.role == "planner" for agent in agents) != 1:
            raise AgentConfigurationError("Exactly one planner Agent is required.")
        ids = {agent.id for agent in agents}
        for agent in agents:
            missing = agent.allowed_delegates - ids
            if missing:
                raise AgentConfigurationError(
                    f"Agent {agent.id} has unknown delegates: {', '.join(sorted(missing))}"
                )
            if agent.id in agent.allowed_delegates:
                raise AgentConfigurationError(f"Agent cannot delegate to itself: {agent.id}")
            if agent.mode != "primary" and agent.discover_delegates:
                raise AgentConfigurationError(
                    f"Only the primary agent may discover delegates: {agent.id}"
                )
        self._agents = agents

    @property
    def agents(self) -> tuple[AgentDefinition, ...]:
        return self._agents

    @property
    def primary(self) -> AgentDefinition:
        return next(agent for agent in self._agents if agent.mode == "primary")

    @property
    def planner(self) -> AgentDefinition:
        planners = tuple(agent for agent in self._agents if agent.role == "planner")
        if len(planners) != 1:
            raise AgentConfigurationError("Exactly one planner Agent is required.")
        return planners[0]

    @classmethod
    def load(cls, root: Path) -> "AgentRegistry":
        if not root.is_dir():
            raise AgentConfigurationError(f"Agent directory does not exist: {root}")
        return cls(tuple(cls._load_agent(path) for path in sorted(root.glob("*/agent.json"))))

    @staticmethod
    def _load_agent(manifest_path: Path) -> AgentDefinition:
        try:
            manifest: Mapping[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AgentConfigurationError(f"Cannot read {manifest_path}: {exc}") from exc
        required = {"id", "mode", "instructions", "allowed_skills", "allowed_tools", "max_steps"}
        missing = sorted(required - manifest.keys())
        if missing:
            raise AgentConfigurationError(f"Agent manifest {manifest_path} is missing: {', '.join(missing)}")
        try:
            document = load_instruction_document(manifest_path.parent / str(manifest["instructions"]))
        except DocumentConfigurationError as exc:
            raise AgentConfigurationError(str(exc)) from exc
        mode = str(manifest["mode"])
        if mode not in {"primary", "specialist"}:
            raise AgentConfigurationError("Agent mode must be primary or specialist.")
        role = str(manifest.get("role", "orchestrator" if mode == "primary" else "worker"))
        evaluator_kind = manifest.get("evaluator_kind")
        if role not in {"orchestrator", "planner", "worker", "evaluator"}:
            raise AgentConfigurationError(
                "Agent role must be orchestrator, planner, worker, or evaluator."
            )
        if mode == "primary" and role != "orchestrator":
            raise AgentConfigurationError("Primary Agent role must be orchestrator.")
        if mode == "specialist" and role == "orchestrator":
            raise AgentConfigurationError("Specialist Agent cannot use orchestrator role.")
        if role == "evaluator" and evaluator_kind not in {"verification", "review", "critique"}:
            raise AgentConfigurationError("Evaluator Agent requires evaluator_kind.")
        if role != "evaluator" and evaluator_kind is not None:
            raise AgentConfigurationError("Only evaluator Agents may set evaluator_kind.")
        tool_policy = str(manifest.get("tool_policy", "explicit"))
        if tool_policy not in {"explicit", "selected-skill-only"}:
            raise AgentConfigurationError(
                "Agent tool_policy must be explicit or selected-skill-only."
            )
        if role in {"orchestrator", "planner"} and tool_policy != "explicit":
            raise AgentConfigurationError(
                "Control-plane Agents cannot inherit selected Skill tools."
            )
        if role == "planner" and any((manifest["allowed_skills"], manifest["allowed_tools"])):
            raise AgentConfigurationError(
                "Planner Agent cannot bind business Skills or MCP tools."
            )
        max_steps = int(manifest["max_steps"])
        max_depth = int(manifest.get("max_delegation_depth", 0))
        if not 1 <= max_steps <= 20 or not 0 <= max_depth <= 3:
            raise AgentConfigurationError("Agent max_steps or max_delegation_depth is invalid.")
        loop_config = manifest.get("loop", {})
        if not isinstance(loop_config, Mapping):
            raise AgentConfigurationError("Agent loop must be an object.")
        try:
            loop_budget = LoopBudget(**loop_config)
        except (TypeError, ValueError) as exc:
            raise AgentConfigurationError(f"Agent loop budget is invalid: {exc}") from exc
        return AgentDefinition(
            id=str(manifest["id"]), name=document.name, description=document.description,
            instructions=document.body, mode=mode, role=role,
            evaluator_kind=str(evaluator_kind) if evaluator_kind is not None else None,
            allowed_skills=frozenset(str(item) for item in manifest["allowed_skills"]),
            allowed_tools=frozenset(str(item) for item in manifest["allowed_tools"]),
            allowed_delegates=frozenset(str(item) for item in manifest.get("allowed_delegates", ())),
            capabilities=tuple(str(item) for item in manifest.get("capabilities", ())),
            accepted_capabilities=frozenset(
                str(item) for item in manifest.get("accepted_capabilities", ())
            ),
            accepted_risk_levels=frozenset(
                str(item) for item in manifest.get("accepted_risk_levels", ())
            ),
            tool_policy=tool_policy,
            discover_delegates=bool(manifest.get("discover_delegates", False)),
            loop_budget=loop_budget,
            max_steps=max_steps, max_delegation_depth=max_depth,
        )

    def get(self, agent_id: str) -> AgentDefinition:
        match = next((agent for agent in self._agents if agent.id == agent_id), None)
        if match is None:
            raise AgentConfigurationError(f"Unknown agent: {agent_id}")
        return match

    def delegates_for(self, agent: AgentDefinition) -> tuple[AgentDefinition, ...]:
        """Return the policy-filtered candidate pool visible to one control-plane agent."""

        if agent.discover_delegates:
            return tuple(
                candidate for candidate in self._agents
                if candidate.mode == "specialist" and candidate.role != "planner"
            )
        return tuple(self.get(agent_id) for agent_id in sorted(agent.allowed_delegates))
