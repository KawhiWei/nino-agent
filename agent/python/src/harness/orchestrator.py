from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

from framework import (
    AgentEvent, EventHandler, HarnessStepState, LoopKind, LoopStatus, LoopStopReason,
    Message, ModelTurn, RunResult, RunStatus, ToolCall, ToolDefinition, ToolResult,
    task_node_fingerprint,
)

from .agents import AgentDefinition, AgentRegistry
from .loop import LoopController, LoopViolation, strictest_budget
from .planning import PLAN_NODE_TOOL_NAME, PlannerHarness, REJECT_TOOL_NAME
from .react import CLARIFICATION_TOOL_NAME, HarnessConfig, is_concise_clarification
from .scheduler import TaskGraphScheduler
from .skills import SkillRegistry


DISPATCH_TOOL_NAME = PLAN_NODE_TOOL_NAME


@dataclass(frozen=True, slots=True)
class InputBinding:
    name: str
    source_node_id: str
    selector: str = "summary"


@dataclass(frozen=True, slots=True)
class PlannedDispatch:
    node_id: str
    call: ToolCall
    agent: AgentDefinition
    skill_id: str
    task: str
    context: str
    depends_on: tuple[str, ...]
    input_bindings: tuple[InputBinding, ...]
    acceptance_contract: Mapping[str, Any]
    supersedes_node_id: str | None = None
    node_fingerprint: str = ""


@dataclass(frozen=True, slots=True)
class DispatchOutcome:
    plan: PlannedDispatch
    result: ToolResult
    payload: Mapping[str, Any]
    success: bool


class OrchestratorHarness:
    """Business-neutral control plane over isolated specialist ReAct workers.

    Planner proposals are untrusted input. This control plane validates and persists Graph revisions,
    schedules workers, enforces gates, and alone reconciles accepted evidence into a final answer.
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
        self._planner = PlannerHarness(model, agents.planner)
        self._worker_factory = worker_factory
        self._config = config or HarnessConfig()
        self._node_slots = asyncio.Semaphore(self._config.hard_max_parallel_nodes)
        self._scheduler = TaskGraphScheduler()

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
        emit_lock = asyncio.Lock()

        async def emit(event_type: str, **data: Any) -> Any:
            nonlocal sequence
            async with emit_lock:
                sequence += 1
                event = AgentEvent(run_id, sequence, event_type, data)
                events.append(event)
                if on_event is not None:
                    result = on_event(event)
                    if inspect.isawaitable(result):
                        return await result
                    return result
                return None

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

        matched_skills = self._skills.matches(user_input)
        semantic_fallback = not matched_skills
        route_skills = (
            matched_skills
            if matched_skills else self._skills.semantic_candidates(user_input)
        )
        matched_skill_ids = {skill.id for skill in route_skills}
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
        used_skills: set[str] = set()
        node_outcomes: dict[str, bool] = {}
        node_results: dict[str, Mapping[str, Any]] = {}
        node_fingerprints: dict[str, str] = {}
        planned_node_ids: set[str] = set()

        try:
            while True:
                violation = loop.begin_step()
                if violation is not None:
                    return await fail(violation)
                step = loop.step
                await checkpoint("before_model")
                await emit(
                    "model_started", step=step, phase="planning",
                    agent_id=self._agents.planner.id,
                )
                async with asyncio.timeout(loop.remaining_seconds):
                    decision = await self._planner.plan(
                        user_input, candidates, revision=step, history=history,
                        node_results=node_results, semantic_fallback=semantic_fallback,
                    )
                await emit(
                    "model_completed", step=step, phase="planning",
                    agent_id=self._agents.planner.id,
                    tool_call_count=len(decision.calls), has_text=bool(decision.message),
                )
                if decision.kind != "plan":
                    if decision.kind == "clarification":
                        question = decision.message
                        if not is_concise_clarification(question):
                            return await fail(LoopViolation(
                                "INVALID_CLARIFICATION",
                                "Clarification must be one concise question for missing input.",
                                LoopStopReason.POLICY_VIOLATION,
                            ))
                        await emit("clarification_requested", step=step, message=question)
                        loop.stop(LoopStatus.COMPLETED, LoopStopReason.FINAL_ANSWER)
                        await checkpoint("terminal")
                        await emit("run_completed", step=step, outcome="clarification")
                        return RunResult(
                            run_id, RunStatus.COMPLETED, question, None,
                            step, tuple(events),
                        )
                    if decision.kind == "reject" and semantic_fallback:
                        await emit(
                            "policy_rejected", error_code="OUT_OF_SCOPE",
                            policy="semantic_capability_scope", reason=decision.message,
                        )
                        loop.stop(
                            LoopStatus.COMPLETED, LoopStopReason.POLICY_VIOLATION,
                            "OUT_OF_SCOPE",
                        )
                        await checkpoint("terminal")
                        await emit("run_completed", step=step, outcome="out_of_scope")
                        return RunResult(
                            run_id, RunStatus.COMPLETED, self.OUT_OF_SCOPE_ANSWER,
                            None, step, tuple(events),
                        )
                    await emit(
                        "policy_rejected", error_code="INVALID_PLANNER_OUTPUT",
                        policy="structured_task_graph_plan",
                    )
                    return await fail(LoopViolation(
                        "INVALID_PLANNER_OUTPUT",
                        decision.message or "Planner must submit a structured TaskGraph revision.",
                        LoopStopReason.POLICY_VIOLATION,
                    ))
                plans: list[PlannedDispatch] = []
                for index, call in enumerate(decision.calls, start=1):
                    error = self._validate_dispatch(call, candidates)
                    if error is not None:
                        return await fail(LoopViolation(
                            error[0], error[1], LoopStopReason.POLICY_VIOLATION
                        ))
                    violation = loop.register_action(self._signature(call))
                    if violation is not None:
                        return await fail(violation)
                    plan = self._make_plan(call, candidates, step, index)
                    if plan.node_id in planned_node_ids:
                        return await fail(LoopViolation(
                            "DUPLICATE_NODE_ID", f"TaskGraph node id already exists: {plan.node_id}",
                            LoopStopReason.POLICY_VIOLATION,
                        ))
                    plans.append(plan)
                plan_error = self._scheduler.validate(plans, node_outcomes)
                if plan_error is not None:
                    return await fail(LoopViolation(
                        "INVALID_TASK_GRAPH", plan_error, LoopStopReason.POLICY_VIOLATION,
                    ))
                binding_error = self._validate_input_bindings(plans)
                if binding_error is not None:
                    return await fail(LoopViolation(
                        "INVALID_INPUT_BINDING", binding_error,
                        LoopStopReason.POLICY_VIOLATION,
                    ))
                supersedes_error = self._validate_supersedes(plans, node_outcomes)
                if supersedes_error is not None:
                    return await fail(LoopViolation(
                        "INVALID_SUPERSEDES", supersedes_error,
                        LoopStopReason.POLICY_VIOLATION,
                    ))
                plans = list(self._fingerprint_plans(plans, node_fingerprints))
                node_fingerprints.update(
                    (plan.node_id, plan.node_fingerprint) for plan in plans
                )
                planned_node_ids.update(plan.node_id for plan in plans)
                await emit(
                    "graph_planned" if not node_outcomes else "graph_reconciled",
                    step=step, revision=step,
                    reason="initial_plan" if not node_outcomes else "reconcile_failed_work",
                    nodes=self._plan_event_nodes(plans),
                )
                outcomes = await self._execute_plan_batch(
                    plans, node_outcomes, node_results, emit, step, run_id, loop
                )
                for outcome in outcomes:
                    used_skills.add(outcome.plan.skill_id)
                    node_outcomes[outcome.plan.node_id] = outcome.success
                    node_results[outcome.plan.node_id] = outcome.payload
                    violation = loop.record_observation(outcome.success)
                    await checkpoint("after_observation")
                    if violation is not None:
                        return await fail(violation)
                if outcomes and all(outcome.success for outcome in outcomes):
                    violation = loop.begin_step()
                    if violation is not None:
                        return await fail(violation)
                    final_step = loop.step
                    await checkpoint("before_model")
                    await emit(
                        "model_started", step=final_step, phase="reconciliation",
                        agent_id=self._primary.id,
                    )
                    final_messages = (
                        Message(role="system", content=self._primary.instructions),
                        *history,
                        Message(role="user", content=user_input.strip()),
                        Message(
                            role="system",
                            content=(
                                "Reconcile the successful verified node results below into the "
                                "final user-facing answer. Do not propose or execute work.\n"
                                + json.dumps(node_results, ensure_ascii=False)
                            ),
                        ),
                    )
                    async with asyncio.timeout(loop.remaining_seconds):
                        turn = await self.step(HarnessStepState(
                            messages=final_messages, tools=(), step=final_step,
                            max_steps=max_steps,
                        ))
                    await emit(
                        "model_completed", step=final_step, phase="reconciliation",
                        agent_id=self._primary.id,
                        tool_call_count=len(turn.tool_calls), has_text=bool(turn.text.strip()),
                    )
                    answer = turn.text.strip()
                    if turn.tool_calls or not answer:
                        return await fail(LoopViolation(
                            "INVALID_RECONCILIATION",
                            "Orchestrator reconciliation must return final text only.",
                            LoopStopReason.POLICY_VIOLATION,
                        ))
                    loop.stop(LoopStatus.COMPLETED, LoopStopReason.FINAL_ANSWER)
                    await checkpoint("terminal")
                    await emit("run_completed", step=final_step, agent_id=self._primary.id)
                    return RunResult(
                        run_id, RunStatus.COMPLETED, answer,
                        next(iter(used_skills)) if len(used_skills) == 1 else None,
                        final_step, tuple(events),
                    )
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
            if agent.role != "worker":
                continue
            for skill in self._skills.skills:
                if not agent.accepts_skill(skill):
                    continue
                if matched_skill_ids is not None and skill.id not in matched_skill_ids:
                    continue
                candidates.append((agent, skill))
        return tuple(candidates)

    def _make_plan(
        self,
        call: ToolCall,
        candidates: Sequence[tuple[AgentDefinition, Any]],
        step: int,
        index: int,
    ) -> PlannedDispatch:
        agent_id = str(call.arguments["agent_id"])
        skill_id = str(call.arguments["skill_id"])
        agent = next(
            item for item, skill in candidates
            if item.id == agent_id and skill.id == skill_id
        )
        raw_id = str(call.arguments.get("node_id", "")).strip()
        node_id = raw_id or f"dispatch-{step}-{index}"
        depends = tuple(str(item).strip() for item in call.arguments.get("depends_on", ()))
        bindings = tuple(
            InputBinding(
                name=str(item.get("name", "")).strip(),
                source_node_id=str(item.get("source_node_id", "")).strip(),
                selector=str(item.get("selector", "summary")).strip(),
            )
            for item in call.arguments.get("input_bindings", ())
            if isinstance(item, Mapping)
        )
        raw_contract = call.arguments.get("acceptance_contract")
        contract = (
            dict(raw_contract)
            if isinstance(raw_contract, Mapping)
            else self._default_acceptance_contract(skill_id, str(call.arguments["task"]).strip())
        )
        return PlannedDispatch(
            node_id=node_id, call=call, agent=agent, skill_id=skill_id,
            task=str(call.arguments["task"]).strip(),
            context=str(call.arguments.get("context", "")).strip(),
            depends_on=depends,
            input_bindings=bindings,
            acceptance_contract=contract,
            supersedes_node_id=self._optional_string(
                call.arguments.get("supersedes_node_id")
            ),
        )

    @staticmethod
    def _optional_string(value: Any) -> str | None:
        return str(value).strip() or None if value is not None else None

    def _fingerprint_plans(
        self,
        plans: Sequence[PlannedDispatch],
        known: Mapping[str, str],
    ) -> tuple[PlannedDispatch, ...]:
        by_id = {plan.node_id: plan for plan in plans}
        resolved = dict(known)
        visiting: set[str] = set()

        def fingerprint(node_id: str) -> str:
            if node_id in resolved:
                return resolved[node_id]
            if node_id in visiting:
                raise ValueError("TaskGraph revision contains a dependency cycle.")
            visiting.add(node_id)
            plan = by_id[node_id]
            dependency_fingerprints = {
                dependency: (
                    fingerprint(dependency)
                    if dependency in by_id
                    else known.get(dependency, f"unresolved:{dependency}")
                )
                for dependency in plan.depends_on
            }
            skill = self._skills.get(plan.skill_id)
            value = task_node_fingerprint({
                "kind": "specialist",
                "agent_id": plan.agent.id,
                "skill_id": plan.skill_id,
                "skill_version": skill.version,
                "task": plan.task,
                "context": plan.context,
                "dependencies": dependency_fingerprints,
                "input_bindings": [
                    {
                        "name": item.name,
                        "source_node_id": item.source_node_id,
                        "selector": item.selector,
                    }
                    for item in plan.input_bindings
                ],
                "acceptance_contract": dict(plan.acceptance_contract),
                "supersedes_node_id": plan.supersedes_node_id,
            })
            visiting.remove(node_id)
            resolved[node_id] = value
            return value

        return tuple(
            replace(plan, node_fingerprint=fingerprint(plan.node_id))
            for plan in plans
        )

    def _plan_event_nodes(self, plans: Sequence[PlannedDispatch]) -> list[dict[str, Any]]:
        nodes: list[dict[str, Any]] = []
        for plan in plans:
            skill = self._skills.get(plan.skill_id)
            nodes.append({
                "node_id": plan.node_id, "kind": "specialist",
                "agent_id": plan.agent.id, "skill_id": plan.skill_id,
                "task": plan.task, "depends_on": list(plan.depends_on),
                "input_bindings": [
                    {
                        "name": item.name,
                        "source_node_id": item.source_node_id,
                        "selector": item.selector,
                    }
                    for item in plan.input_bindings
                ],
                "acceptance_contract": dict(plan.acceptance_contract),
                "supersedes_node_id": plan.supersedes_node_id,
                "skill_version": skill.version,
                "node_fingerprint": plan.node_fingerprint,
                "gate_kind": "evidence",
            })
            previous_node_id = plan.node_id
            previous_fingerprint = plan.node_fingerprint
            for evaluator_kind, evaluator in self._evaluators_for(plan.skill_id):
                evaluator_node_id = f"{plan.node_id}.{self._evaluator_suffix(evaluator_kind)}"
                evaluator_task = (
                    f"Independently {self._evaluator_verb(evaluator_kind)} node {plan.node_id}"
                )
                evaluator_fingerprint = self._evaluator_node_fingerprint(
                    plan, evaluator_kind, evaluator, previous_node_id,
                    previous_fingerprint,
                )
                nodes.append({
                    "node_id": evaluator_node_id, "kind": evaluator_kind,
                    "agent_id": evaluator.id, "skill_id": plan.skill_id,
                    "task": evaluator_task,
                    "depends_on": [previous_node_id],
                    "acceptance_contract": dict(plan.acceptance_contract),
                    "skill_version": skill.version,
                    "node_fingerprint": evaluator_fingerprint,
                    "gate_kind": (
                        "independent_verification"
                        if evaluator_kind == "verification" else evaluator_kind
                    ),
                })
                previous_node_id = evaluator_node_id
                previous_fingerprint = evaluator_fingerprint
        return nodes

    def _evaluator_node_fingerprint(
        self,
        plan: PlannedDispatch,
        evaluator_kind: str,
        evaluator: AgentDefinition,
        previous_node_id: str,
        previous_fingerprint: str,
    ) -> str:
        skill = self._skills.get(plan.skill_id)
        return task_node_fingerprint({
            "kind": evaluator_kind,
            "agent_id": evaluator.id,
            "skill_id": plan.skill_id,
            "skill_version": skill.version,
            "task": f"Independently {self._evaluator_verb(evaluator_kind)} node {plan.node_id}",
            "dependencies": {previous_node_id: previous_fingerprint},
            "acceptance_contract": dict(plan.acceptance_contract),
        })

    async def _execute_plan_batch(
        self,
        plans: Sequence[PlannedDispatch],
        known: dict[str, bool],
        known_results: dict[str, Mapping[str, Any]],
        emit: Any,
        step: int,
        run_id: str,
        loop: LoopController,
    ) -> tuple[DispatchOutcome, ...]:
        pending = {plan.node_id: plan for plan in plans}
        outcomes: dict[str, DispatchOutcome] = {}
        current = dict(known)
        results = dict(known_results)
        while pending:
            decision = self._scheduler.decide(tuple(pending.values()), current)
            blocked = [pending[node_id] for node_id in decision.blocked_ids]
            for plan in blocked:
                result = ToolResult(json.dumps({
                    "kind": "dispatch_result", "status": "dependency_failed",
                    "node_id": plan.node_id, "dependencies": list(plan.depends_on),
                }, ensure_ascii=False), True)
                await emit(
                    "node_skipped", step=step, plan_node_id=plan.node_id,
                    node_fingerprint=plan.node_fingerprint,
                    reason="dependency_failed", depends_on=list(plan.depends_on),
                )
                await emit(
                    "tool_completed", step=step, tool=plan.call.name,
                    call_id=plan.call.id, is_error=True, truncated=False,
                    plan_node_id=plan.node_id,
                )
                payload = json.loads(result.content)
                outcomes[plan.node_id] = DispatchOutcome(plan, result, payload, False)
                current[plan.node_id] = False
                results[plan.node_id] = payload
                pending.pop(plan.node_id)
            ready = [pending[node_id] for node_id in decision.ready_ids]
            if not ready:
                if pending:  # validated DAGs can only reach this through failed dependencies.
                    continue
                break
            executed = await asyncio.gather(*(
                self._execute_dispatch(
                    plan, self._resolve_inputs(plan, results), emit, step, run_id, loop
                )
                for plan in ready
            ))
            for outcome in executed:
                outcomes[outcome.plan.node_id] = outcome
                current[outcome.plan.node_id] = outcome.success
                results[outcome.plan.node_id] = outcome.payload
                pending.pop(outcome.plan.node_id)
        return tuple(outcomes[plan.node_id] for plan in plans)

    async def _execute_dispatch(
        self, plan: PlannedDispatch, resolved_inputs: Mapping[str, Any],
        emit: Any, step: int, run_id: str,
        loop: LoopController,
    ) -> DispatchOutcome:
        async with self._node_slots:
            return await self._execute_claimed_dispatch(
                plan, resolved_inputs, emit, step, run_id, loop
            )

    async def _execute_claimed_dispatch(
        self, plan: PlannedDispatch, resolved_inputs: Mapping[str, Any],
        emit: Any, step: int, run_id: str,
        loop: LoopController,
    ) -> DispatchOutcome:
        child_run_id = str(uuid4())
        await emit(
            "tool_started", step=step, tool=plan.call.name, call_id=plan.call.id,
            plan_node_id=plan.node_id,
        )
        claim_state = await emit(
            "agent_started", step=step, parent_run_id=run_id,
            child_run_id=child_run_id, plan_node_id=plan.node_id,
            agent_id=plan.agent.id, skill_id=plan.skill_id, task=plan.task,
            depends_on=list(plan.depends_on), node_kind="specialist",
            node_fingerprint=plan.node_fingerprint,
        )
        child_sections = [plan.task]
        if plan.context:
            child_sections.append(f"Delegated context:\n{plan.context}")
        child_sections.append(
            "Acceptance contract:\n"
            + json.dumps(plan.acceptance_contract, ensure_ascii=False)
        )
        child_sections.append(
            "Node result contract:\nReturn a JSON object when possible with status, summary, "
            "outputs, findings, concerns, and recommended_next. Keep evidence grounded in "
            "approved Tool observations; the Harness records Tool evidence separately."
        )
        if resolved_inputs:
            child_sections.append(
                "Bound upstream inputs:\n"
                + json.dumps(resolved_inputs, ensure_ascii=False)
            )
        child_input = "\n\n".join(child_sections)
        child_evidence: list[dict[str, str | None]] = []

        async def forward_child_event(child_event: AgentEvent) -> None:
            if child_event.type in {"run_started", "run_completed", "run_failed", "run_cancelled"}:
                return
            if (
                child_event.type == "tool_completed"
                and not bool(child_event.data.get("is_error", False))
                and str(child_event.data.get("tool", "")).startswith("nino_")
                and not str(child_event.data.get("tool", "")).startswith("nino_runtime_")
            ):
                child_evidence.append({
                    "tool": str(child_event.data.get("tool", "")),
                    "call_id": str(child_event.data.get("call_id", "")),
                    "result_digest": None,
                })
            await emit(child_event.type, **{
                **dict(child_event.data), "parent_step": step,
                "child_run_id": child_run_id, "plan_node_id": plan.node_id,
                "agent_id": plan.agent.id, "skill_id": plan.skill_id,
            })

        reused_child = (
            isinstance(claim_state, dict)
            and claim_state.get("execute") is False
            and claim_state.get("reason") == "already_completed"
        )
        if reused_child:
            persisted_result = claim_state.get("result", {})
            child = RunResult(
                child_run_id, RunStatus.COMPLETED,
                str(persisted_result.get("summary", "")), plan.skill_id, 0, (),
            )
            child_node_result = dict(persisted_result)
        else:
            if isinstance(claim_state, dict) and claim_state.get("execute") is False:
                raise RuntimeError(f"NODE_NOT_CLAIMABLE:{plan.node_id}")
            async with asyncio.timeout(loop.remaining_seconds):
                child = await self._worker_factory(plan.agent).run(
                    child_input, run_id=child_run_id, selected_skill_id=plan.skill_id,
                    on_event=forward_child_event,
                )
            if child.status == RunStatus.CANCELLED:
                raise asyncio.CancelledError
            child_node_result = self._normalize_node_result(
                child.answer, child.status, child.error_code, child_evidence
            )
        if not reused_child:
            await emit(
                "agent_completed" if child.status == RunStatus.COMPLETED else "agent_failed",
                step=step, parent_run_id=run_id, child_run_id=child_run_id,
                plan_node_id=plan.node_id, agent_id=plan.agent.id, skill_id=plan.skill_id,
                status=child.status.value, child_steps=child.steps, error_code=child.error_code,
                result_summary=child.answer, node_result=child_node_result,
            )
        evaluations: list[dict[str, Any]] = []
        passed = child.status == RunStatus.COMPLETED
        previous_node_id = plan.node_id
        previous_fingerprint = plan.node_fingerprint
        claim = child.answer
        for evaluator_kind, evaluator in self._evaluators_for(plan.skill_id):
            if not passed:
                break
            evaluator_run_id = str(uuid4())
            evaluator_node_id = f"{plan.node_id}.{self._evaluator_suffix(evaluator_kind)}"
            evaluator_task = (
                f"Independently {self._evaluator_verb(evaluator_kind)} the following claim. "
                "Re-run the minimum approved read-only queries, check the Skill contract, then "
                "submit the structured evaluator verdict Action. Use verdict=passed and "
                f"evidence_level=proved only when supported.\n\nOriginal task:\n{plan.task}"
                "\n\nAcceptance contract:\n"
                f"{json.dumps(plan.acceptance_contract, ensure_ascii=False)}"
                f"\n\nClaim to evaluate:\n{claim}"
            )
            evaluator_fingerprint = self._evaluator_node_fingerprint(
                plan, evaluator_kind, evaluator, previous_node_id,
                previous_fingerprint,
            )
            evaluator_claim = await emit(
                "agent_started", step=step, parent_run_id=run_id,
                child_run_id=evaluator_run_id, plan_node_id=evaluator_node_id,
                agent_id=evaluator.id, skill_id=plan.skill_id, task=evaluator_task,
                node_kind=evaluator_kind, depends_on=[previous_node_id],
                node_fingerprint=evaluator_fingerprint,
            )

            async def forward_evaluator_event(evaluator_event: AgentEvent) -> None:
                if evaluator_event.type in {"run_started", "run_completed", "run_failed", "run_cancelled"}:
                    return
                await emit(evaluator_event.type, **{
                    **dict(evaluator_event.data), "parent_step": step,
                    "child_run_id": evaluator_run_id, "plan_node_id": evaluator_node_id,
                    "agent_id": evaluator.id, "skill_id": plan.skill_id,
                })

            reused_evaluator = (
                isinstance(evaluator_claim, dict)
                and evaluator_claim.get("execute") is False
                and evaluator_claim.get("reason") == "already_completed"
            )
            if reused_evaluator:
                persisted = evaluator_claim.get("result", {})
                evaluation = RunResult(
                    evaluator_run_id, RunStatus.COMPLETED,
                    str(persisted.get("summary", "")), plan.skill_id, 0, (),
                )
            else:
                if isinstance(evaluator_claim, dict) and evaluator_claim.get("execute") is False:
                    raise RuntimeError(f"NODE_NOT_CLAIMABLE:{evaluator_node_id}")
                async with asyncio.timeout(loop.remaining_seconds):
                    evaluation = await self._worker_factory(evaluator).run(
                        evaluator_task, run_id=evaluator_run_id,
                        selected_skill_id=plan.skill_id, on_event=forward_evaluator_event,
                    )
                if evaluation.status == RunStatus.CANCELLED:
                    raise asyncio.CancelledError
            try:
                verdict_payload = json.loads(evaluation.answer)
            except json.JSONDecodeError:
                verdict_payload = {}
            passed = (
                evaluation.status == RunStatus.COMPLETED
                and verdict_payload.get("verdict") == "passed"
                and verdict_payload.get("evidence_level") == "proved"
            )
            evaluations.append({
                "kind": evaluator_kind, "agent_id": evaluator.id,
                "status": "passed" if passed else "failed", "verdict": verdict_payload,
            })
            if not reused_evaluator:
                await emit(
                    "agent_completed" if passed else "agent_failed",
                    step=step, parent_run_id=run_id, child_run_id=evaluator_run_id,
                    plan_node_id=evaluator_node_id, agent_id=evaluator.id,
                    skill_id=plan.skill_id, status="completed" if passed else "failed",
                    child_steps=evaluation.steps,
                    error_code=None if passed else f"{evaluator_kind.upper()}_FAILED",
                    result_summary=json.dumps(verdict_payload, ensure_ascii=False),
                    node_result={
                        "status": "completed" if passed else "failed",
                        "summary": json.dumps(verdict_payload, ensure_ascii=False),
                        "outputs": {"verdict": verdict_payload},
                        "evidence": [], "findings": [],
                        "concerns": list(verdict_payload.get("concerns", ())),
                        "recommended_next": [],
                        "error_code": None if passed else f"{evaluator_kind.upper()}_FAILED",
                        "retryable": False,
                    },
                )
            claim = json.dumps(verdict_payload, ensure_ascii=False)
            previous_node_id = evaluator_node_id
            previous_fingerprint = evaluator_fingerprint
        child_outputs = child_node_result.get("outputs", {})
        payload = {
            "kind": "dispatch_result", "status": "completed" if passed else "failed",
            "node_id": plan.node_id, "agent_id": plan.agent.id,
            "skill_id": plan.skill_id, "child_run_id": child_run_id,
            "summary": str(child_node_result.get("summary", child.answer)),
            "outputs": dict(child_outputs) if isinstance(child_outputs, Mapping) else {},
            "findings": list(child_node_result.get("findings", ())),
            "evidence": list(child_node_result.get("evidence", ())),
            "verification": evaluations[0]["verdict"] if evaluations else None,
            "evaluations": evaluations,
            "concerns": (
                [child.error_code] if child.error_code
                else ([] if passed else ["ASSURANCE_GATE_FAILED"])
            ),
            "recommended_next": list(child_node_result.get("recommended_next", ())),
        }
        result = ToolResult(json.dumps(payload, ensure_ascii=False), not passed)
        await emit(
            "tool_completed", step=step, tool=plan.call.name, call_id=plan.call.id,
            is_error=result.is_error, truncated=False, plan_node_id=plan.node_id,
        )
        return DispatchOutcome(plan, result, payload, passed)

    @staticmethod
    def _resolve_inputs(
        plan: PlannedDispatch, results: Mapping[str, Mapping[str, Any]]
    ) -> Mapping[str, Any]:
        if plan.input_bindings:
            return {
                binding.name: results.get(binding.source_node_id, {}).get(binding.selector)
                for binding in plan.input_bindings
            }
        return {
            dependency: results.get(dependency, {}).get("summary")
            for dependency in plan.depends_on
        }

    @staticmethod
    def _validate_input_bindings(plans: Sequence[PlannedDispatch]) -> str | None:
        allowed_selectors = {
            "summary", "outputs", "findings", "evidence", "concerns",
            "recommended_next",
        }
        for plan in plans:
            names: set[str] = set()
            for binding in plan.input_bindings:
                if not binding.name or binding.name in names:
                    return f"Node {plan.node_id} input binding names must be non-empty and unique."
                if binding.source_node_id not in plan.depends_on:
                    return (
                        f"Node {plan.node_id} binding source {binding.source_node_id} "
                        "must also appear in depends_on."
                    )
                if binding.selector not in allowed_selectors:
                    return (
                        f"Node {plan.node_id} uses unsupported result selector: "
                        f"{binding.selector}"
                    )
                names.add(binding.name)
        return None

    @staticmethod
    def _validate_supersedes(
        plans: Sequence[PlannedDispatch], known: Mapping[str, bool]
    ) -> str | None:
        current_ids = {plan.node_id for plan in plans}
        for plan in plans:
            target = plan.supersedes_node_id
            if target is None:
                continue
            if target == plan.node_id or target in current_ids:
                return (
                    f"Node {plan.node_id} may supersede only a node from an earlier revision."
                )
            if target in plan.depends_on:
                return (
                    f"Node {plan.node_id} cannot depend on the failed node it supersedes: "
                    f"{target}"
                )
            if target not in known:
                return f"Node {plan.node_id} supersedes unknown historical node: {target}"
            if known[target]:
                return (
                    f"Node {plan.node_id} cannot supersede successful historical node: {target}"
                )
        return None

    @staticmethod
    def _normalize_node_result(
        answer: str,
        status: RunStatus,
        error_code: str | None,
        evidence: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        parsed: Mapping[str, Any] = {}
        try:
            candidate = json.loads(answer)
            if isinstance(candidate, Mapping):
                parsed = candidate
        except json.JSONDecodeError:
            pass
        completed = status == RunStatus.COMPLETED
        allowed_statuses = {
            "completed", "completed_with_concerns", "needs_context", "failed",
            "blocked", "cancelled", "skipped",
        }
        normalized_status = str(
            parsed.get("status", "completed" if completed else "failed")
        )
        if normalized_status not in allowed_statuses:
            normalized_status = "completed" if completed else "failed"
        summary = str(parsed.get("summary", answer)).strip()
        outputs = parsed.get("outputs", {})

        def strings(key: str, fallback: Sequence[Any] = ()) -> list[str]:
            value = parsed.get(key, fallback)
            if not isinstance(value, (list, tuple)):
                return []
            return [str(item) for item in value if item]
        return {
            "status": normalized_status,
            "summary": summary,
            "outputs": dict(outputs) if isinstance(outputs, Mapping) else {"value": outputs},
            "evidence": [dict(item) for item in evidence],
            "findings": strings("findings"),
            "concerns": strings("concerns", (error_code,) if error_code else ()),
            "recommended_next": strings("recommended_next"),
            "error_code": error_code,
            "retryable": error_code in {
                "DEPENDENCY_ERROR", "TOOL_DISCOVERY_ERROR", "LOOP_TIMEOUT"
            },
        }

    @staticmethod
    def _default_acceptance_contract(skill_id: str, task: str) -> Mapping[str, Any]:
        return {
            "spec_source": f"user_request+registered_skill:{skill_id}",
            "target_outcome": task,
            "positive_checks": ["Return a result that directly satisfies the delegated task."],
            "negative_checks": ["Do not claim facts without approved Tool evidence."],
            "evidence_requirements": [
                "At least one successful non-reference Tool Observation."
            ],
            "gaps": [],
            "pass_label": "business_result_verified",
        }

    def _evaluators_for(self, skill_id: str) -> tuple[tuple[str, AgentDefinition], ...]:
        skill = self._skills.get(skill_id)
        available = self._agents.delegates_for(self._primary)
        evaluators: list[tuple[str, AgentDefinition]] = []
        for kind in skill.required_evaluators:
            match = next((
                agent for agent in available
                if agent.accepts_skill(skill)
                and agent.role == "evaluator" and agent.evaluator_kind == kind
            ), None)
            if match is None:
                raise RuntimeError(f"Skill {skill_id} requires unavailable evaluator: {kind}")
            evaluators.append((kind, match))
        return tuple(evaluators)

    @staticmethod
    def _evaluator_suffix(kind: str) -> str:
        return "verify" if kind == "verification" else kind

    @staticmethod
    def _evaluator_verb(kind: str) -> str:
        return "verify" if kind == "verification" else kind

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
            "workflow_id": skill.workflow_id,
            "workflow_execution_shape": skill.workflow_execution_shape,
            "assurance_mode": skill.assurance_mode,
        } for agent, skill in candidates]
        return (
            "Dynamic capability catalog (metadata only):\n"
            + json.dumps(catalog, ensure_ascii=False)
            + "\nUse only the supplied structured route Actions. Dispatch a listed Agent + Skill "
            "pair when it fits, request clarification when required input or intent is ambiguous, "
            "or reject when semantic fallback candidates do not fit. Never answer the business "
            "request directly. Include a task-specific acceptance_contract for every dispatch. "
            "Business instructions are loaded by the worker."
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
                    "node_id": {
                        "type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$"
                    },
                    "supersedes_node_id": {
                        "type": "string",
                        "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$",
                        "description": (
                            "Optional failed or blocked node from an earlier revision that this "
                            "repair node replaces."
                        ),
                    },
                    "depends_on": {
                        "type": "array", "uniqueItems": True,
                        "items": {"type": "string"},
                    },
                    "input_bindings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "minLength": 1},
                                "source_node_id": {"type": "string", "minLength": 1},
                                "selector": {
                                    "type": "string",
                                    "enum": [
                                        "summary", "outputs", "findings", "evidence",
                                        "concerns", "recommended_next",
                                    ],
                                },
                            },
                            "required": ["name", "source_node_id", "selector"],
                            "additionalProperties": False,
                        },
                    },
                    "acceptance_contract": {
                        "type": "object",
                        "properties": {
                            "spec_source": {"type": "string", "minLength": 1},
                            "target_outcome": {"type": "string", "minLength": 1},
                            "positive_checks": {
                                "type": "array", "minItems": 1,
                                "items": {"type": "string"},
                            },
                            "negative_checks": {
                                "type": "array", "items": {"type": "string"},
                            },
                            "evidence_requirements": {
                                "type": "array", "items": {"type": "string"},
                            },
                            "gaps": {"type": "array", "items": {"type": "string"}},
                            "pass_label": {"type": "string", "minLength": 1},
                        },
                        "required": [
                            "spec_source", "target_outcome", "positive_checks",
                            "negative_checks", "evidence_requirements", "gaps",
                            "pass_label",
                        ],
                        "additionalProperties": False,
                    },
                },
                "required": ["agent_id", "skill_id", "task"],
                "additionalProperties": False,
            },
        )

    @staticmethod
    def _clarification_tool() -> ToolDefinition:
        return ToolDefinition(
            CLARIFICATION_TOOL_NAME,
            "Request one missing input or disambiguating choice before capability dispatch.",
            {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "minLength": 1, "maxLength": 500},
                },
                "required": ["message"],
                "additionalProperties": False,
            },
        )

    @staticmethod
    def _reject_tool() -> ToolDefinition:
        return ToolDefinition(
            REJECT_TOOL_NAME,
            "Reject a request that does not semantically fit any supplied capability.",
            {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "minLength": 1, "maxLength": 500},
                },
                "required": ["reason"],
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
        contract = call.arguments.get("acceptance_contract")
        if contract is not None:
            required = {
                "spec_source", "target_outcome", "positive_checks", "negative_checks",
                "evidence_requirements", "gaps", "pass_label",
            }
            if not isinstance(contract, Mapping) or set(contract) != required:
                return (
                    "INVALID_ACCEPTANCE_CONTRACT",
                    "Acceptance contract must use the canonical compact contract fields.",
                )
            if (
                not str(contract.get("spec_source", "")).strip()
                or not str(contract.get("target_outcome", "")).strip()
                or not str(contract.get("pass_label", "")).strip()
                or not isinstance(contract.get("positive_checks"), list)
                or not contract.get("positive_checks")
            ):
                return (
                    "INVALID_ACCEPTANCE_CONTRACT",
                    "Acceptance contract requires a source, outcome, pass label, and checks.",
                )
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
