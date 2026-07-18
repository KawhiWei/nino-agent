from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any, Sequence, TypedDict
from uuid import uuid4

from framework import (
    AgentEvent, ChatModel, EventHandler, HarnessStepState, LoopKind, LoopStatus,
    LoopStopReason, Message, ModelTurn, RunResult, RunStatus,
    ToolCall, ToolDefinition, ToolProvider, ToolResult,
)
from .agents import AgentDefinition, AgentRegistry
from .loop import LoopController, LoopViolation, stop_reason_for_error, strictest_budget
from .react import (
    CLARIFICATION_TOOL_NAME, DELEGATE_TOOL_NAME, EVALUATOR_VERDICT_TOOL_NAME,
    STRICT_WORKER_POLICY,
    HarnessConfig, is_concise_clarification,
)
from .references import REFERENCE_TOOL_NAME, ReferenceProvider
from .skills import Skill, SkillConfigurationError, SkillRegistry


class _GraphState(TypedDict):
    messages: tuple[Message, ...]
    step: int
    calls: tuple[ToolCall, ...]
    invoked: frozenset[str]
    answer: str
    error_code: str | None
    error_message: str
    successful_evidence_actions: int


class LangGraphReActHarness:
    """LangGraph Harness with explicit model/tool nodes and the shared Framework Ports."""

    def __init__(
        self,
        model: ChatModel,
        tools: ToolProvider,
        skills: SkillRegistry,
        config: HarnessConfig | None = None,
        references: ReferenceProvider | None = None,
        agents: AgentRegistry | None = None,
        agent: AgentDefinition | None = None,
        delegation_depth: int = 0,
    ) -> None:
        try:
            import langgraph.graph  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "LangGraph engine is not installed. Install the project with [langgraph]."
            ) from exc
        self._model = model
        self._tools = tools
        self._skills = skills
        self._config = config or HarnessConfig()
        self._references = references or ReferenceProvider()
        self._agents = agents
        self._agent = agent or (agents.primary if agents is not None else None)
        self._delegation_depth = delegation_depth

    async def step(self, state: HarnessStepState) -> ModelTurn:
        return await self._model.complete(state.messages, state.tools)

    async def run(
        self,
        user_input: str,
        history: Sequence[Message] = (),
        on_event: EventHandler | None = None,
        run_id: str | None = None,
        selected_skill_id: str | None = None,
    ) -> RunResult:
        run_id = run_id or str(uuid4())
        events: list[AgentEvent] = []

        async def emit(event_type: str, **data: Any) -> None:
            event = AgentEvent(run_id, len(events) + 1, event_type, data)
            events.append(event)
            if on_event is not None:
                value = on_event(event)
                if inspect.isawaitable(value):
                    await value

        await emit("run_started", engine="langgraph")
        if not user_input.strip():
            return await self._failed(
                run_id, events, emit, None, 0, "INVALID_INPUT", "User input cannot be empty."
            )
        try:
            skill = (
                self._skills.get(selected_skill_id)
                if selected_skill_id is not None
                else self._skills.route(user_input)
            )
        except SkillConfigurationError as exc:
            return await self._failed(
                run_id, events, emit, None, 0, "SKILL_NOT_FOUND", str(exc)
            )

        await emit("skill_selected", skill_id=skill.id, version=skill.version)
        if self._agent is not None and not self._agent.accepts_skill(skill):
            return await self._failed(
                run_id, events, emit, skill, 0, "SKILL_NOT_ALLOWED",
                f"Skill is not allowed by agent {self._agent.id}: {skill.id}",
            )
        max_steps = min(
            skill.max_steps,
            self._agent.max_steps if self._agent is not None else skill.max_steps,
            self._config.hard_max_steps,
        )
        budget = strictest_budget(
            self._config.loop_budget,
            skill.loop_budget,
            self._agent.loop_budget if self._agent is not None else skill.loop_budget,
        )
        loop = LoopController(run_id, LoopKind.WORKER_REACT, max_steps, budget)

        async def checkpoint(phase: str) -> None:
            await emit("loop_checkpoint", phase=phase, state=loop.snapshot().to_data())

        async def fail(violation: LoopViolation) -> RunResult:
            loop.stop(LoopStatus.FAILED, violation.stop_reason, violation.error_code)
            await checkpoint("terminal")
            return await self._failed(
                run_id, events, emit, skill, loop.step,
                violation.error_code, violation.message,
            )

        try:
            async with asyncio.timeout(loop.remaining_seconds):
                available = await self._allowed_tools(skill)
            graph = self._build_graph(skill, available, emit, run_id, loop)
            system_messages = () if self._agent is None else (
                Message(role="system", content=self._agent.instructions),
            )
            initial: _GraphState = {
                "messages": (
                    *system_messages,
                    Message(role="system", content=skill.instructions),
                    Message(role="system", content=STRICT_WORKER_POLICY),
                    *history,
                    Message(role="user", content=user_input.strip()),
                ),
                "step": 0,
                "calls": (),
                "invoked": frozenset(),
                "answer": "",
                "error_code": None,
                "error_message": "",
                "successful_evidence_actions": 0,
            }
            async with asyncio.timeout(loop.remaining_seconds):
                state = await graph.ainvoke(
                    initial, config={"recursion_limit": max_steps * 2 + 2}
                )
        except asyncio.CancelledError:
            loop.stop(LoopStatus.CANCELLED, LoopStopReason.CANCELLED, "RUN_CANCELLED")
            await checkpoint("terminal")
            await emit("run_cancelled")
            return RunResult(
                run_id, RunStatus.CANCELLED, "Run was cancelled.", skill.id,
                len([event for event in events if event.type == "model_started"]),
                tuple(events), "RUN_CANCELLED"
            )
        except TimeoutError:
            return await fail(LoopViolation(
                "LOOP_TIMEOUT",
                f"Worker loop exceeded its {loop.budget.timeout_seconds} second timeout.",
                LoopStopReason.TIMEOUT,
            ))
        except OSError as exc:
            return await fail(LoopViolation(
                "DEPENDENCY_ERROR", str(exc), LoopStopReason.DEPENDENCY_ERROR
            ))

        if state["error_code"]:
            return await fail(LoopViolation(
                state["error_code"], state["error_message"],
                stop_reason_for_error(state["error_code"]),
            ))
        loop.stop(LoopStatus.COMPLETED, LoopStopReason.FINAL_ANSWER)
        await checkpoint("terminal")
        await emit("run_completed", step=state["step"])
        return RunResult(
            run_id, RunStatus.COMPLETED, state["answer"], skill.id,
            state["step"], tuple(events)
        )

    def _build_graph(
        self,
        skill: Skill,
        available: tuple[ToolDefinition, ...],
        emit: Any,
        run_id: str,
        loop: LoopController,
    ) -> Any:
        from langgraph.graph import END, START, StateGraph

        max_steps = loop.max_steps

        async def model_node(state: _GraphState) -> dict[str, Any]:
            if state["error_code"]:
                return {}
            violation = loop.begin_step()
            if violation is not None:
                return {
                    "error_code": violation.error_code,
                    "error_message": violation.message,
                }
            step = loop.step
            await emit("loop_checkpoint", phase="before_model", state=loop.snapshot().to_data())
            await emit("model_started", step=step)
            turn = await self.step(HarnessStepState(
                messages=state["messages"],
                tools=available,
                step=step,
                max_steps=max_steps,
            ))
            await emit(
                "model_completed", step=step, tool_call_count=len(turn.tool_calls),
                has_text=bool(turn.text.strip())
            )
            if not turn.tool_calls:
                answer = turn.text.strip()
                if not answer:
                    return {
                        "step": step,
                        "error_code": "EMPTY_MODEL_RESPONSE",
                        "error_message": "Model returned neither text nor tool calls.",
                    }
                if state["successful_evidence_actions"] == 0:
                    await emit(
                        "policy_rejected", error_code="EVIDENCE_REQUIRED",
                        policy="skill_observation_required",
                    )
                    return {
                        "step": step,
                        "error_code": "EVIDENCE_REQUIRED",
                        "error_message": (
                            "A Skill answer requires a successful tool Observation. Missing input "
                            "must use nino_runtime_request_clarification."
                        ),
                    }
                return {"step": step, "answer": answer, "calls": ()}
            return {
                "step": step,
                "calls": turn.tool_calls,
                "messages": (*state["messages"], Message(
                    role="assistant",
                    content=turn.text,
                    tool_calls=turn.tool_calls,
                    reasoning_content=turn.reasoning_content,
                )),
            }

        async def tool_node(state: _GraphState) -> dict[str, Any]:
            messages = list(state["messages"])
            invoked = set(state["invoked"])
            successful_evidence_actions = state["successful_evidence_actions"]
            allowed_names = {tool.name for tool in available}
            for call in state["calls"]:
                if call.name not in allowed_names:
                    return {
                        "error_code": "TOOL_NOT_ALLOWED",
                        "error_message": f"Tool is not available to the active agent: {call.name}",
                    }
                signature = self._signature(call)
                if signature in invoked:
                    return {
                        "error_code": "DUPLICATE_TOOL_CALL",
                        "error_message": f"Duplicate tool call blocked: {call.name}",
                    }
                violation = loop.register_action(signature)
                if violation is not None:
                    return {
                        "error_code": violation.error_code,
                        "error_message": violation.message,
                    }
                invoked.add(signature)
                await emit("tool_started", step=state["step"], tool=call.name, call_id=call.id)
                if call.name == REFERENCE_TOOL_NAME:
                    try:
                        result, loaded = self._references.invoke(skill, call)
                        await emit(
                            "reference_loaded", step=state["step"], reference_id=loaded.id,
                            sha256=loaded.sha256,
                        )
                    except (OSError, ValueError) as exc:
                        result = ToolResult(str(exc), is_error=True)
                elif call.name == CLARIFICATION_TOOL_NAME:
                    message = str(call.arguments.get("message", "")).strip()
                    if not is_concise_clarification(message):
                        result = ToolResult(
                            "Clarification must be a concise request for missing input.",
                            is_error=True,
                        )
                    else:
                        result = ToolResult(message)
                        await emit(
                            "clarification_requested", step=state["step"], message=message,
                        )
                elif call.name == EVALUATOR_VERDICT_TOOL_NAME:
                    verdict = str(call.arguments.get("verdict", ""))
                    if verdict == "passed" and successful_evidence_actions == 0:
                        result = ToolResult(
                            "A passed evaluator verdict requires successful Tool evidence.",
                            is_error=True,
                        )
                    else:
                        payload = {
                            "verdict": verdict,
                            "evidence_level": str(call.arguments.get("evidence_level", "")),
                            "checked_requirements": list(call.arguments.get("checked_requirements", ())),
                            "failed_requirements": list(call.arguments.get("failed_requirements", ())),
                            "concerns": list(call.arguments.get("concerns", ())),
                        }
                        result = ToolResult(json.dumps(payload, ensure_ascii=False))
                        await emit("evaluator_verdict", step=state["step"], **payload)
                elif call.name == DELEGATE_TOOL_NAME:
                    result = await self._delegate(call, emit, state["step"], run_id)
                else:
                    result = await self._tools.invoke(call)
                if (
                    not result.is_error
                    and call.name not in {
                        REFERENCE_TOOL_NAME, CLARIFICATION_TOOL_NAME,
                        EVALUATOR_VERDICT_TOOL_NAME,
                    }
                ):
                    successful_evidence_actions += 1
                content = result.content[: self._config.max_tool_result_chars]
                await emit(
                    "tool_completed", step=state["step"], tool=call.name,
                    call_id=call.id, is_error=result.is_error,
                    truncated=len(content) < len(result.content)
                )
                messages.append(Message(role="tool", content=content, tool_call_id=call.id))
                violation = loop.record_observation(not result.is_error)
                await emit(
                    "loop_checkpoint", phase="after_observation",
                    state=loop.snapshot().to_data(),
                )
                if violation is not None:
                    return {
                        "error_code": violation.error_code,
                        "error_message": violation.message,
                    }
                if call.name in {
                    CLARIFICATION_TOOL_NAME, EVALUATOR_VERDICT_TOOL_NAME
                } and not result.is_error:
                    return {
                        "messages": tuple(messages),
                        "invoked": frozenset(invoked),
                        "calls": (),
                        "answer": result.content,
                        "successful_evidence_actions": successful_evidence_actions,
                    }
            return {
                "messages": tuple(messages),
                "invoked": frozenset(invoked),
                "calls": (),
                "successful_evidence_actions": successful_evidence_actions,
            }

        def after_model(state: _GraphState) -> str:
            if state["error_code"] or state["answer"]:
                return END
            return "tools"

        def after_tools(state: _GraphState) -> str:
            if state["error_code"] or state["answer"]:
                return END
            return "model"

        builder = StateGraph(_GraphState)
        builder.add_node("model", model_node)
        builder.add_node("tools", tool_node)
        builder.add_edge(START, "model")
        builder.add_conditional_edges("model", after_model)
        builder.add_conditional_edges("tools", after_tools)
        return builder.compile()

    async def _allowed_tools(self, skill: Skill) -> tuple[ToolDefinition, ...]:
        listed = await self._tools.list_tools()
        expected = skill.allowed_tools
        if self._agent is not None:
            expected = self._agent.effective_tools(skill)
        allowed = tuple(item for item in listed if item.name in expected)
        missing = expected - {item.name for item in allowed}
        if missing:
            raise OSError(f"Skill {skill.id} requires unavailable tools: {', '.join(sorted(missing))}")
        internal: list[ToolDefinition] = []
        internal.append(ToolDefinition(
            CLARIFICATION_TOOL_NAME,
            "Request missing user input. Use only when the selected Skill cannot proceed without "
            "required parameters; provide one concise clarification question.",
            {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "minLength": 1, "maxLength": 500},
                },
                "required": ["message"],
                "additionalProperties": False,
            },
        ))
        if self._agent is not None and self._agent.role == "evaluator":
            internal.append(ToolDefinition(
                EVALUATOR_VERDICT_TOOL_NAME,
                "Submit the evaluator's structured terminal verdict after checking Tool evidence.",
                {
                    "type": "object",
                    "properties": {
                        "verdict": {
                            "type": "string",
                            "enum": ["passed", "failed", "blocked", "needs_context"],
                        },
                        "evidence_level": {
                            "type": "string", "enum": ["proved", "observed", "unproven"]
                        },
                        "checked_requirements": {
                            "type": "array", "items": {"type": "string"}
                        },
                        "failed_requirements": {
                            "type": "array", "items": {"type": "string"}
                        },
                        "concerns": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": [
                        "verdict", "evidence_level", "checked_requirements",
                        "failed_requirements", "concerns",
                    ],
                    "additionalProperties": False,
                },
            ))
        if skill.references:
            internal.append(self._references.tool_definition(skill))
        if self._can_delegate():
            assert self._agent is not None
            internal.append(ToolDefinition(
                DELEGATE_TOOL_NAME,
                "Delegate one bounded task to an approved specialist with a fresh context.",
                {
                    "type": "object",
                    "properties": {
                        "agent_id": {"type": "string", "enum": sorted(self._agent.allowed_delegates)},
                        "task": {"type": "string", "minLength": 1},
                        "context": {"type": "string"},
                    },
                    "required": ["agent_id", "task"],
                    "additionalProperties": False,
                },
            ))
        return (*allowed, *internal)

    def _can_delegate(self) -> bool:
        return (
            self._agents is not None and self._agent is not None
            and self._agent.can_delegate
            and self._delegation_depth < self._agent.max_delegation_depth
        )

    async def _delegate(self, call: ToolCall, emit: Any, step: int, parent_run_id: str) -> ToolResult:
        if not self._can_delegate() or self._agents is None or self._agent is None:
            return ToolResult("Delegation is not allowed for the active agent.", is_error=True)
        agent_id = str(call.arguments.get("agent_id", "")).strip()
        task = str(call.arguments.get("task", "")).strip()
        context = str(call.arguments.get("context", "")).strip()
        if agent_id not in self._agent.allowed_delegates or not task:
            return ToolResult("Delegate or task is not allowed.", is_error=True)
        child_agent = self._agents.get(agent_id)
        child_run_id = str(uuid4())
        await emit(
            "agent_started", step=step, parent_run_id=parent_run_id,
            child_run_id=child_run_id, agent_id=agent_id,
        )
        child_runtime = type(self)(
            self._model, self._tools, self._skills, self._config,
            references=self._references, agents=self._agents, agent=child_agent,
            delegation_depth=self._delegation_depth + 1,
        )
        child_input = task if not context else f"{task}\n\nDelegated context:\n{context}"
        child = await child_runtime.run(child_input, run_id=child_run_id)
        if child.status == RunStatus.CANCELLED:
            raise asyncio.CancelledError
        await emit(
            "agent_completed" if child.status == RunStatus.COMPLETED else "agent_failed",
            step=step, parent_run_id=parent_run_id,
            child_run_id=child_run_id, agent_id=agent_id, status=child.status.value,
            skill_id=child.skill_id, child_steps=child.steps,
        )
        return ToolResult(json.dumps({
            "kind": "delegation_result", "agent_id": agent_id,
            "child_run_id": child_run_id, "status": child.status.value,
            "skill_id": child.skill_id, "answer": child.answer,
            "error_code": child.error_code,
        }, ensure_ascii=False), is_error=child.status != RunStatus.COMPLETED)

    @staticmethod
    def _signature(call: ToolCall) -> str:
        arguments = json.dumps(call.arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return f"{call.name}:{arguments}"

    @staticmethod
    async def _failed(
        run_id: str,
        events: list[AgentEvent],
        emit: Any,
        skill: Skill | None,
        steps: int,
        code: str,
        message: str,
    ) -> RunResult:
        await emit("run_failed", error_code=code, message=message)
        return RunResult(
            run_id, RunStatus.FAILED, message, skill.id if skill else None,
            steps, tuple(events), code
        )
