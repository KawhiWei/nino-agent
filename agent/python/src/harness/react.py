from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Sequence
from uuid import uuid4

from framework import (
    AgentEvent, ChatModel, EventHandler, HarnessStepState, LoopBudget, LoopKind,
    LoopStatus, LoopStopReason, Message, ModelTurn, RunResult, RunStatus,
    ToolCall, ToolDefinition, ToolProvider, ToolResult,
)
from .agents import AgentDefinition, AgentRegistry
from .loop import LoopController, LoopViolation, strictest_budget
from .references import REFERENCE_TOOL_NAME, ReferenceProvider
from .skills import Skill, SkillConfigurationError, SkillRegistry


DELEGATE_TOOL_NAME = "nino_runtime_delegate_agent"


@dataclass(frozen=True, slots=True)
class HarnessConfig:
    hard_max_steps: int = 8
    hard_max_actions: int = 32
    hard_timeout_seconds: int = 300
    hard_max_consecutive_failures: int = 3
    hard_max_no_progress_steps: int = 3
    max_tool_result_chars: int = 50_000

    def __post_init__(self) -> None:
        if self.hard_max_steps < 1:
            raise ValueError("hard_max_steps must be positive.")
        LoopBudget(
            self.hard_max_actions,
            self.hard_timeout_seconds,
            self.hard_max_consecutive_failures,
            self.hard_max_no_progress_steps,
        )
        if self.max_tool_result_chars < 1:
            raise ValueError("max_tool_result_chars must be positive.")

    @property
    def loop_budget(self) -> LoopBudget:
        return LoopBudget(
            self.hard_max_actions,
            self.hard_timeout_seconds,
            self.hard_max_consecutive_failures,
            self.hard_max_no_progress_steps,
        )


class ReActHarness:
    """Framework-neutral controlled ReAct Harness.

    Call chain: AgentRuntimeService -> Harness.run -> Harness.step -> ChatModel;
    Action calls route through ToolProvider and return Observation messages to the next step.
    Internal Reference and Delegate tools are resolved here before external ToolProvider routing.
    """

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
        self._model = model
        self._tools = tools
        self._skills = skills
        self._config = config or HarnessConfig()
        self._references = references or ReferenceProvider()
        self._agents = agents
        self._agent = agent or (agents.primary if agents is not None else None)
        self._delegation_depth = delegation_depth

    async def step(self, state: HarnessStepState) -> ModelTurn:
        """Execute one model decision; Runtime-visible effects happen only after validation."""

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
        sequence = 0

        async def emit(event_type: str, **data: Any) -> None:
            nonlocal sequence
            sequence += 1
            event = AgentEvent(run_id=run_id, sequence=sequence, type=event_type, data=data)
            events.append(event)
            if on_event is not None:
                result = on_event(event)
                if inspect.isawaitable(result):
                    await result

        await emit("run_started")
        if not user_input.strip():
            return await self._failure(
                run_id, events, emit, None, 0, "INVALID_INPUT", "User input cannot be empty."
            )

        try:
            skill = (
                self._skills.get(selected_skill_id)
                if selected_skill_id is not None
                else self._skills.route(user_input)
            )
        except SkillConfigurationError as exc:
            return await self._failure(
                run_id, events, emit, None, 0, "SKILL_NOT_FOUND", str(exc)
            )

        await emit("skill_selected", skill_id=skill.id, version=skill.version)
        if self._agent is not None and skill.id not in self._agent.allowed_skills:
            return await self._failure(
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
            return await self._failure(
                run_id, events, emit, skill, loop.step,
                violation.error_code, violation.message,
            )

        try:
            # Discovery returns the global provider catalog; this method applies Skill/Agent policy.
            async with asyncio.timeout(loop.remaining_seconds):
                available_tools = await self._allowed_tools(skill)
        except asyncio.CancelledError:
            loop.stop(LoopStatus.CANCELLED, LoopStopReason.CANCELLED, "RUN_CANCELLED")
            await checkpoint("terminal")
            await emit("run_cancelled")
            return RunResult(
                run_id=run_id,
                status=RunStatus.CANCELLED,
                answer="Run was cancelled.",
                skill_id=skill.id,
                steps=len([event for event in events if event.type == "model_started"]),
                events=tuple(events),
                error_code="RUN_CANCELLED",
            )
        except TimeoutError:
            return await fail(LoopViolation(
                "LOOP_TIMEOUT",
                f"Worker loop exceeded its {loop.budget.timeout_seconds} second timeout.",
                LoopStopReason.TIMEOUT,
            ))
        except OSError as exc:
            return await fail(LoopViolation(
                "TOOL_DISCOVERY_ERROR", str(exc), LoopStopReason.DEPENDENCY_ERROR
            ))
        messages = []
        if self._agent is not None:
            messages.append(Message(role="system", content=self._agent.instructions))
        messages.extend((Message(role="system", content=skill.instructions), *history))
        messages.append(Message(role="user", content=user_input.strip()))
        invoked: set[str] = set()

        try:
            # Each iteration is one Reason/Action/Observation turn, bounded by three budgets.
            while True:
                violation = loop.begin_step()
                if violation is not None:
                    return await fail(violation)
                step = loop.step
                await checkpoint("before_model")
                await emit("model_started", step=step)
                async with asyncio.timeout(loop.remaining_seconds):
                    turn = await self.step(HarnessStepState(
                        messages=tuple(messages),
                        tools=tuple(available_tools),
                        step=step,
                        max_steps=max_steps,
                    ))
                await emit(
                    "model_completed",
                    step=step,
                    tool_call_count=len(turn.tool_calls),
                    has_text=bool(turn.text.strip()),
                )

                if not turn.tool_calls:
                    answer = turn.text.strip()
                    if not answer:
                        return await fail(LoopViolation(
                            "EMPTY_MODEL_RESPONSE",
                            "Model returned neither text nor tool calls.",
                            LoopStopReason.NO_PROGRESS,
                        ))
                    loop.stop(LoopStatus.COMPLETED, LoopStopReason.FINAL_ANSWER)
                    await checkpoint("terminal")
                    await emit("run_completed", step=step)
                    return RunResult(
                        run_id=run_id,
                        status=RunStatus.COMPLETED,
                        answer=answer,
                        skill_id=skill.id,
                        steps=step,
                        events=tuple(events),
                    )

                messages.append(
                    Message(
                        role="assistant",
                        content=turn.text,
                        tool_calls=turn.tool_calls,
                        reasoning_content=turn.reasoning_content,
                    )
                )
                for call in turn.tool_calls:
                    policy_error = self._validate_tool_call(call, available_tools, invoked)
                    if policy_error is not None:
                        reason = (
                            LoopStopReason.DUPLICATE_ACTION
                            if policy_error[0] == "DUPLICATE_TOOL_CALL"
                            else LoopStopReason.POLICY_VIOLATION
                        )
                        return await fail(LoopViolation(
                            policy_error[0], policy_error[1], reason
                        ))

                    signature = self._tool_signature(call)
                    violation = loop.register_action(signature)
                    if violation is not None:
                        return await fail(violation)
                    invoked.add(signature)
                    await emit("tool_started", step=step, tool=call.name, call_id=call.id)
                    if call.name == REFERENCE_TOOL_NAME:
                        try:
                            result, loaded = self._references.invoke(skill, call)
                            await emit(
                                "reference_loaded", step=step, reference_id=loaded.id,
                                sha256=loaded.sha256,
                            )
                        except (OSError, ValueError) as exc:
                            result = ToolResult(str(exc), is_error=True)
                    elif call.name == DELEGATE_TOOL_NAME:
                        async with asyncio.timeout(loop.remaining_seconds):
                            result = await self._delegate(call, skill, emit, step, run_id)
                    else:
                        async with asyncio.timeout(loop.remaining_seconds):
                            result = await self._tools.invoke(call)
                    content = result.content[: self._config.max_tool_result_chars]
                    await emit(
                        "tool_completed",
                        step=step,
                        tool=call.name,
                        call_id=call.id,
                        is_error=result.is_error,
                        truncated=len(result.content) > len(content),
                    )
                    messages.append(
                        Message(role="tool", content=content, tool_call_id=call.id)
                    )
                    violation = loop.record_observation(not result.is_error)
                    await checkpoint("after_observation")
                    if violation is not None:
                        return await fail(violation)
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

    async def _allowed_tools(self, skill: Skill) -> tuple[ToolDefinition, ...]:
        listed = await self._tools.list_tools()
        expected = skill.allowed_tools
        if self._agent is not None:
            expected = expected & self._agent.allowed_tools
        allowed = tuple(tool for tool in listed if tool.name in expected)
        missing = expected - {tool.name for tool in allowed}
        if missing:
            names = ", ".join(sorted(missing))
            raise OSError(f"Skill {skill.id} requires unavailable tools: {names}")
        internal: list[ToolDefinition] = []
        if skill.references:
            internal.append(self._references.tool_definition(skill))
        if self._can_delegate():
            assert self._agent is not None and self._agents is not None
            internal.append(ToolDefinition(
                DELEGATE_TOOL_NAME,
                "Delegate one bounded task to an approved specialist with a fresh context.",
                {
                    "type": "object",
                    "properties": {
                        "agent_id": {
                            "type": "string",
                            "enum": sorted(self._agent.allowed_delegates),
                        },
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
            self._agents is not None
            and self._agent is not None
            and self._agent.can_delegate
            and self._delegation_depth < self._agent.max_delegation_depth
        )

    async def _delegate(
        self,
        call: ToolCall,
        skill: Skill,
        emit: Callable[..., Awaitable[None]],
        step: int,
        parent_run_id: str,
    ) -> ToolResult:
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
            "kind": "delegation_result",
            "agent_id": agent_id,
            "child_run_id": child_run_id,
            "status": child.status.value,
            "skill_id": child.skill_id,
            "answer": child.answer,
            "error_code": child.error_code,
        }, ensure_ascii=False), is_error=child.status != RunStatus.COMPLETED)

    @staticmethod
    def _validate_tool_call(
        call: ToolCall,
        available_tools: Sequence[ToolDefinition],
        invoked: set[str],
    ) -> tuple[str, str] | None:
        available_names = {tool.name for tool in available_tools}
        if call.name not in available_names:
            return "TOOL_NOT_ALLOWED", f"Tool is not available to the active agent: {call.name}"
        signature = ReActHarness._tool_signature(call)
        if signature in invoked:
            return "DUPLICATE_TOOL_CALL", f"Duplicate tool call blocked: {call.name}"
        return None

    @staticmethod
    def _tool_signature(call: ToolCall) -> str:
        arguments = json.dumps(call.arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return f"{call.name}:{arguments}"

    @staticmethod
    async def _failure(
        run_id: str,
        events: list[AgentEvent],
        emit: Callable[..., Awaitable[None]],
        skill: Skill | None,
        steps: int,
        error_code: str,
        message: str,
    ) -> RunResult:
        await emit("run_failed", error_code=error_code, message=message)
        return RunResult(
            run_id=run_id,
            status=RunStatus.FAILED,
            answer=message,
            skill_id=skill.id if skill else None,
            steps=steps,
            events=tuple(events),
            error_code=error_code,
        )
