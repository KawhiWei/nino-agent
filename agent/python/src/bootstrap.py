from __future__ import annotations

import os
from dataclasses import dataclass

from demo import DemoChatModel, DemoToolClient
from framework import AgentHarness, ChatModel, ToolProvider
from harness import (
    AgentRegistry, HarnessConfig, LangGraphReActHarness, OrchestratorHarness, ReActHarness,
    ReferenceProvider, SkillRegistry,
)
from infrastructure.langchain_model import LangChainChatModel
from infrastructure.mcp import McpServerConfig, McpServerRegistry, load_mcp_server_configs
from infrastructure.openai_compatible import OpenAICompatibleChatModel


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    mode: str = "demo"
    engine: str = "lightweight"
    model_adapter: str = "native"
    model_name: str = "gpt-5.4"
    model_api_key: str = ""
    model_base_url: str = ""
    model_thinking: str = ""
    model_reasoning_effort: str = ""
    model_timeout_seconds: float = 150.0
    mcp_url: str = "http://127.0.0.1:8091/mcp"
    mcp_servers: tuple[McpServerConfig, ...] = ()
    loop_hard_max_steps: int = 8
    loop_hard_max_actions: int = 32
    loop_hard_timeout_seconds: int = 3600
    loop_hard_max_consecutive_failures: int = 3
    loop_hard_max_no_progress_steps: int = 3
    graph_max_parallel_nodes: int = 4

    @classmethod
    def from_env(cls) -> "RuntimeSettings":
        fallback_mcp_url = os.getenv("NINO_MCP_URL", "http://127.0.0.1:8091/mcp")
        return cls(
            mode=os.getenv("NINO_RUNTIME_MODE", "demo").lower(),
            engine=os.getenv("NINO_AGENT_ENGINE", "lightweight").lower(),
            model_adapter=os.getenv("NINO_MODEL_ADAPTER", "native").lower(),
            model_name="gpt-5.4",
            model_api_key=os.getenv("OPENAI_API_KEY", ""),
            model_base_url=os.getenv("INCERRY_OPENAI_BASE_URL", ""),
            model_thinking=os.getenv("NINO_MODEL_THINKING", "").lower(),
            model_reasoning_effort=os.getenv("NINO_MODEL_REASONING_EFFORT", "").lower(),
            model_timeout_seconds=float(os.getenv("NINO_MODEL_TIMEOUT_SECONDS", "150")),
            mcp_url=fallback_mcp_url,
            mcp_servers=load_mcp_server_configs(
                os.getenv("NINO_MCP_SERVERS", ""), fallback_mcp_url
            ),
            loop_hard_max_steps=int(os.getenv("NINO_LOOP_HARD_MAX_STEPS", "8")),
            loop_hard_max_actions=int(os.getenv("NINO_LOOP_HARD_MAX_ACTIONS", "32")),
            loop_hard_timeout_seconds=int(os.getenv("NINO_LOOP_HARD_TIMEOUT_SECONDS", "3600")),
            loop_hard_max_consecutive_failures=int(
                os.getenv("NINO_LOOP_HARD_MAX_CONSECUTIVE_FAILURES", "3")
            ),
            loop_hard_max_no_progress_steps=int(
                os.getenv("NINO_LOOP_HARD_MAX_NO_PROGRESS_STEPS", "3")
            ),
            graph_max_parallel_nodes=int(
                os.getenv("NINO_GRAPH_MAX_PARALLEL_NODES", "4")
            ),
        )


@dataclass(frozen=True, slots=True)
class HarnessAssembly:
    """Composition-root result connecting Runtime/Harness Ports to infrastructure."""

    harness: AgentHarness
    tools: ToolProvider

def build_harness(
    skills: SkillRegistry, agents: AgentRegistry, settings: RuntimeSettings
) -> AgentHarness:
    return build_harness_assembly(skills, agents, settings).harness


def build_harness_assembly(
    skills: SkillRegistry, agents: AgentRegistry, settings: RuntimeSettings
) -> HarnessAssembly:
    """Wire Framework Ports to concrete model, MCP, and ReAct adapters."""

    _validate_agent_permissions(skills, agents)
    references = ReferenceProvider()
    loop_config = HarnessConfig(
        hard_max_steps=settings.loop_hard_max_steps,
        hard_max_actions=settings.loop_hard_max_actions,
        hard_timeout_seconds=settings.loop_hard_timeout_seconds,
        hard_max_consecutive_failures=settings.loop_hard_max_consecutive_failures,
        hard_max_no_progress_steps=settings.loop_hard_max_no_progress_steps,
        hard_max_parallel_nodes=settings.graph_max_parallel_nodes,
    )
    if settings.mode == "demo":
        model = DemoChatModel()
        tools = DemoToolClient()
        return HarnessAssembly(
            OrchestratorHarness(
                model, skills, agents,
                lambda agent: ReActHarness(
                    model, tools, skills, loop_config, references=references,
                    agents=agents, agent=agent
                ),
                loop_config,
            ),
            tools,
        )
    if settings.mode != "live":
        raise ValueError("NINO_RUNTIME_MODE must be demo or live.")

    model = _build_model(settings)
    configs = settings.mcp_servers or load_mcp_server_configs("", settings.mcp_url)
    tools = McpServerRegistry(configs)
    if settings.engine == "lightweight":
        worker_factory = lambda agent: ReActHarness(
            model, tools, skills, loop_config, references=references,
            agents=agents, agent=agent
        )
    elif settings.engine == "langgraph":
        worker_factory = lambda agent: LangGraphReActHarness(
            model, tools, skills, loop_config, references=references,
            agents=agents, agent=agent
        )
    else:
        raise ValueError("NINO_AGENT_ENGINE must be lightweight or langgraph.")
    return HarnessAssembly(
        OrchestratorHarness(model, skills, agents, worker_factory, loop_config), tools
    )


def _validate_agent_permissions(skills: SkillRegistry, agents: AgentRegistry) -> None:
    skill_ids = {skill.id for skill in skills.skills}
    tools_by_skill = {skill.id: skill.allowed_tools for skill in skills.skills}
    for agent in agents.agents:
        missing = agent.allowed_skills - skill_ids
        if missing:
            raise ValueError(
                f"Agent {agent.id} allows unknown skills: {', '.join(sorted(missing))}"
            )
        available_tools = frozenset().union(
            *(tools_by_skill[skill_id] for skill_id in agent.allowed_skills)
        )
        unknown_tools = agent.allowed_tools - available_tools
        if unknown_tools:
            raise ValueError(
                f"Agent {agent.id} allows tools outside its skills: "
                f"{', '.join(sorted(unknown_tools))}"
            )
    specialists = tuple(agent for agent in agents.agents if agent.mode == "specialist")
    for skill in skills.skills:
        for kind in skill.required_evaluators:
            found = any(
                agent.accepts_skill(skill)
                and agent.role == "evaluator" and agent.evaluator_kind == kind
                for agent in specialists
            )
            if not found:
                raise ValueError(
                    f"Skill {skill.id} requires unavailable evaluator role: {kind}"
                )


def _build_model(settings: RuntimeSettings) -> ChatModel:
    common = {
        "model": settings.model_name,
        "api_key": settings.model_api_key,
        "base_url": settings.model_base_url,
    }
    if settings.model_adapter == "native":
        return OpenAICompatibleChatModel(
            **common,
            timeout_seconds=settings.model_timeout_seconds,
            thinking_mode=settings.model_thinking,
            reasoning_effort=settings.model_reasoning_effort,
        )
    if settings.model_adapter == "langchain":
        return LangChainChatModel(**common)
    raise ValueError("NINO_MODEL_ADAPTER must be native or langchain.")
