from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any, Callable, Sequence
from uuid import uuid4

from framework import (
    AgentEvent, EventHandler, HarnessStepState, LoopKind, LoopStatus, LoopStopReason,
    Message, ModelTurn, RunResult, RunStatus, ToolCall, ToolDefinition, ToolResult,
)

from .agents import AgentDefinition, AgentRegistry
from .loop import LoopController, LoopViolation, strictest_budget
from .react import HarnessConfig
from .skills import SkillRegistry


DISPATCH_TOOL_NAME = "nino_runtime_dispatch_agent"


class OrchestratorHarness:
    """Business-neutral control plane over isolated specialist ReAct workers.

    The primary model sees capability summaries, never business instructions or MCP tools. Strict
    scope policy rejects unmatched input before a model call and requires an approved dispatch
    before accepting model-generated output.
    """

    OUT_OF_SCOPE_ANSWER = (
        "当前请求不在已注册 Skill 的能力范围内，无法执行或自由扩展回答。"
        "请改为使用当前已注册的数据查询、统计、异常分析或报表分析能力。"
    )

    def __init__(
        self,
        model: Any,
        skills: SkillRegistry,
        agents: AgentRegistry,
        worker_factory: Callable[[AgentDefinition], Any],
        config: HarnessConfig | None = None,
    ) -> None:
        self._model = model
        self._skills = skills
        self._agents = agents
        self._primary = agents.primary
        self._worker_factory = worker_factory
        self._config = config or HarnessConfig()

    async def step(self, state: HarnessStepState) -> ModelTurn:
        return await self._model.complete(state.messages, state.tools)

    async def run(
        self,
        user_input: str,
        history: Sequence[Message] = (),
        on_event: EventHandler | None = None,
        run_id: str | None = None,
    ) -> RunResult:
        run_id = run_id or str(uuid4())
        events: list[AgentEvent] = []
        sequence = 0

        async def emit(event_type: str, **data: Any) -> None:
            nonlocal sequence
            sequence += 1
            event = AgentEvent(run_id, sequence, event_type, data)
            events.append(event)
            if on_event is not None:
                result = on_event(event)
                if inspect.isawaitable(result):
                    await result

        await emit("run_started", agent_id=self._primary.id)
        max_steps = min(self._primary.max_steps, self._config.hard_max_steps)
        loop = LoopController(
            run_id, LoopKind.ORCHESTRATION, max_steps,
            strictest_budget(self._config.loop_budget, self._primary.loop_budget),
        )

        async def checkpoint(phase: str) -> None:
            await emit("loop_checkpoint", phase=phase, state=loop.snapshot().to_data())

        async def fail(violation: LoopViolation) -> RunResult:
            loop.stop(LoopStatus.FAILED, violation.stop_reason, violation.error_code)
            await checkpoint("terminal")
            return await self._failure(
                run_id, events, emit, loop.step, violation.error_code, violation.message
            )

        if not user_input.strip():
            return await fail(LoopViolation(
                "INVALID_INPUT", "User input cannot be empty.",
                LoopStopReason.POLICY_VIOLATION,
            ))

        matched_skill_ids = {skill.id for skill in self._skills.matches(user_input)}
        candidates = self._candidates(matched_skill_ids)
        if not candidates:
            loop.stop(
                LoopStatus.COMPLETED,
                LoopStopReason.POLICY_VIOLATION,
                "OUT_OF_SCOPE",
            )
            await emit(
                "policy_rejected", error_code="OUT_OF_SCOPE",
                policy="registered_skill_scope",
            )
            await checkpoint("terminal")
            await emit(
                "run_completed", step=0, agent_id=self._primary.id,
                outcome="out_of_scope",
            )
            return RunResult(
                run_id, RunStatus.COMPLETED, self.OUT_OF_SCOPE_ANSWER,
                None, 0, tuple(events),
            )
        tools = (self._dispatch_tool(candidates),) if candidates else ()
        messages = [
            Message(role="system", content=self._primary.instructions),
            Message(role="system", content=self._catalog_prompt(candidates)),
            *history,
            Message(role="user", content=user_input.strip()),
        ]
        used_skills: set[str] = set()
        successful_dispatches = 0

        try:
            while True:
                violation = loop.begin_step()
                if violation is not None:
                    return await fail(violation)
                step = loop.step
                await checkpoint("before_model")
                await emit("model_started", step=step, phase="orchestration")
                async with asyncio.timeout(loop.remaining_seconds):
                    turn = await self.step(HarnessStepState(
                        messages=tuple(messages), tools=tools, step=step, max_steps=max_steps,
                    ))
                await emit(
                    "model_completed", step=step, phase="orchestration",
                    tool_call_count=len(turn.tool_calls), has_text=bool(turn.text.strip()),
                )
                if not turn.tool_calls:
                    answer = turn.text.strip()
                    if not answer:
                        return await fail(LoopViolation(
                            "EMPTY_MODEL_RESPONSE",
                            "Orchestrator returned neither text nor dispatch calls.",
                            LoopStopReason.NO_PROGRESS,
                        ))
                    if successful_dispatches == 0:
                        code = (
                            "DISPATCH_REQUIRED"
                            if not used_skills
                            else "SUCCESSFUL_DISPATCH_REQUIRED"
                        )
                        await emit(
                            "policy_rejected", error_code=code,
                            policy="registered_skill_dispatch",
                        )
                        return await fail(LoopViolation(
                            code,
                            "A matched request requires at least one successful registered Agent "
                            "and Skill dispatch before a final answer.",
                            LoopStopReason.POLICY_VIOLATION,
                        ))
                    loop.stop(LoopStatus.COMPLETED, LoopStopReason.FINAL_ANSWER)
                    await checkpoint("terminal")
                    await emit("run_completed", step=step, agent_id=self._primary.id)
                    return RunResult(
                        run_id, RunStatus.COMPLETED, answer,
                        next(iter(used_skills)) if len(used_skills) == 1 else None,
                        step, tuple(events),
                    )

                messages.append(Message(
                    role="assistant", content=turn.text, tool_calls=turn.tool_calls,
                    reasoning_content=turn.reasoning_content,
                ))
                for call in turn.tool_calls:
                    error = self._validate_dispatch(call, candidates)
                    if error is not None:
                        return await fail(LoopViolation(
                            error[0], error[1], LoopStopReason.POLICY_VIOLATION
                        ))
                    violation = loop.register_action(self._signature(call))
                    if violation is not None:
                        return await fail(violation)
                    agent = self._agents.get(str(call.arguments["agent_id"]))
                    skill_id = str(call.arguments["skill_id"])
                    task = str(call.arguments["task"]).strip()
                    context = str(call.arguments.get("context", "")).strip()
                    child_run_id = str(uuid4())
                    used_skills.add(skill_id)
                    await emit(
                        "tool_started", step=step, tool=call.name, call_id=call.id,
                    )
                    await emit(
                        "agent_started", step=step, parent_run_id=run_id,
                        child_run_id=child_run_id, agent_id=agent.id, skill_id=skill_id,
                    )
                    worker = self._worker_factory(agent)
                    child_input = task if not context else f"{task}\n\nDelegated context:\n{context}"

                    async def forward_child_event(child_event: AgentEvent) -> None:
                        if child_event.type in {
                            "run_started", "run_completed", "run_failed", "run_cancelled"
                        }:
                            return
                        await emit(child_event.type, **{
                            **dict(child_event.data),
                            "parent_step": step,
                            "child_run_id": child_run_id,
                            "agent_id": agent.id,
                            "skill_id": skill_id,
                        })

                    async with asyncio.timeout(loop.remaining_seconds):
                        child = await worker.run(
                            child_input, run_id=child_run_id, selected_skill_id=skill_id,
                            on_event=forward_child_event,
                        )
                    if child.status == RunStatus.CANCELLED:
                        raise asyncio.CancelledError
                    if child.status == RunStatus.COMPLETED:
                        successful_dispatches += 1
                    await emit(
                        "agent_completed" if child.status == RunStatus.COMPLETED else "agent_failed",
                        step=step, parent_run_id=run_id, child_run_id=child_run_id,
                        agent_id=agent.id, skill_id=skill_id, status=child.status.value,
                        child_steps=child.steps,
                    )
                    result = ToolResult(json.dumps({
                        "kind": "dispatch_result",
                        "status": child.status.value,
                        "agent_id": agent.id,
                        "skill_id": skill_id,
                        "child_run_id": child_run_id,
                        "summary": child.answer,
                        "deliverables": [],
                        "findings": [],
                        "concerns": [child.error_code] if child.error_code else [],
                    }, ensure_ascii=False), child.status != RunStatus.COMPLETED)
                    await emit(
                        "tool_completed", step=step, tool=call.name, call_id=call.id,
                        is_error=result.is_error, truncated=False,
                    )
                    messages.append(Message(
                        role="tool", content=result.content, tool_call_id=call.id
                    ))
                    violation = loop.record_observation(not result.is_error)
                    await checkpoint("after_observation")
                    if violation is not None:
                        return await fail(violation)
        except asyncio.CancelledError:
            loop.stop(LoopStatus.CANCELLED, LoopStopReason.CANCELLED, "RUN_CANCELLED")
            await checkpoint("terminal")
            await emit("run_cancelled")
            return RunResult(
                run_id, RunStatus.CANCELLED, "Run was cancelled.", None,
                len([event for event in events if event.type == "model_started"]),
                tuple(events), "RUN_CANCELLED",
            )
        except TimeoutError:
            return await fail(LoopViolation(
                "LOOP_TIMEOUT",
                f"Orchestration loop exceeded its {loop.budget.timeout_seconds} second timeout.",
                LoopStopReason.TIMEOUT,
            ))
        except OSError as exc:
            return await fail(LoopViolation(
                "DEPENDENCY_ERROR", str(exc), LoopStopReason.DEPENDENCY_ERROR
            ))

    def _candidates(
        self, matched_skill_ids: set[str] | None = None,
    ) -> tuple[tuple[AgentDefinition, Any], ...]:
        candidates: list[tuple[AgentDefinition, Any]] = []
        for agent in self._agents.delegates_for(self._primary):
            for skill_id in sorted(agent.allowed_skills):
                if matched_skill_ids is not None and skill_id not in matched_skill_ids:
                    continue
                candidates.append((agent, self._skills.get(skill_id)))
        return tuple(candidates)

    @staticmethod
    def _catalog_prompt(candidates: Sequence[tuple[AgentDefinition, Any]]) -> str:
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
        } for agent, skill in candidates]
        return (
            "Dynamic capability catalog (metadata only):\n"
            + json.dumps(catalog, ensure_ascii=False)
            + "\nThis request already matched the listed Skills. You must dispatch one listed "
            "Agent + Skill pair before answering. Never answer the request directly. Business "
            "instructions are loaded by the worker."
        )

    @staticmethod
    def _dispatch_tool(
        candidates: Sequence[tuple[AgentDefinition, Any]],
    ) -> ToolDefinition:
        return ToolDefinition(
            DISPATCH_TOOL_NAME,
            "Dispatch one bounded task to a compatible specialist Agent and Skill.",
            {
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string", "enum": sorted({a.id for a, _ in candidates})
                    },
                    "skill_id": {
                        "type": "string", "enum": sorted({s.id for _, s in candidates})
                    },
                    "task": {"type": "string", "minLength": 1},
                    "context": {"type": "string"},
                },
                "required": ["agent_id", "skill_id", "task"],
                "additionalProperties": False,
            },
        )

    @classmethod
    def _validate_dispatch(
        cls,
        call: ToolCall,
        candidates: Sequence[tuple[AgentDefinition, Any]],
    ) -> tuple[str, str] | None:
        if call.name != DISPATCH_TOOL_NAME:
            return "TOOL_NOT_ALLOWED", f"Orchestrator cannot call tool: {call.name}"
        agent_id = str(call.arguments.get("agent_id", ""))
        skill_id = str(call.arguments.get("skill_id", ""))
        task = str(call.arguments.get("task", "")).strip()
        if not task or (agent_id, skill_id) not in {
            (agent.id, skill.id) for agent, skill in candidates
        }:
            return "DISPATCH_NOT_ALLOWED", "Agent, Skill, or task is not an allowed candidate."
        return None

    @staticmethod
    def _signature(call: ToolCall) -> str:
        return f"{call.name}:{json.dumps(call.arguments, ensure_ascii=False, sort_keys=True)}"

    @staticmethod
    async def _failure(
        run_id: str,
        events: list[AgentEvent],
        emit: Any,
        steps: int,
        code: str,
        message: str,
    ) -> RunResult:
        await emit("run_failed", error_code=code, message=message)
        return RunResult(
            run_id, RunStatus.FAILED, message, None, steps, tuple(events), code
        )
