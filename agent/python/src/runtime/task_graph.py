from __future__ import annotations

from typing import Any, Mapping
from uuid import uuid4

from framework import (
    AcceptanceContract, AgentEvent, AgentRepository, AgentRun, AttemptStatus, GateStatus,
    NodeAttempt, RunResult, RunStatus, TaskGate, TaskGraph, TaskGraphSnapshot,
    TaskGraphStatus, TaskNode, TaskNodeStatus, utc_now,
)


class TaskGraphController:
    """Persist macro task truth while ReAct checkpoints remain node-local state."""

    def __init__(self, repository: AgentRepository, runtime_instance_id: str) -> None:
        self._repository = repository
        self._runtime_instance_id = runtime_instance_id
        self._successful_evidence: dict[str, list[str]] = {}

    async def ensure(
        self, run: AgentRun, user_intent: str, parent_graph_id: str | None = None
    ) -> TaskGraphSnapshot:
        existing = await self._repository.get_task_graph(run.id)
        if existing is not None:
            return existing
        graph = TaskGraph(
            str(uuid4()), run.id, run.conversation_id, user_intent,
            metadata={"schema_version": "1.0", "control_plane": "nino-harness"},
            parent_graph_id=parent_graph_id,
            relation_type="conversation_follow_up" if parent_graph_id else None,
        )
        root = TaskNode(
            id=f"{graph.id}:orchestration", graph_id=graph.id, kind="orchestration",
            owner_agent_id="nino.orchestrator", title="Route and reconcile the user request",
            contract=AcceptanceContract(
                spec_source="user_request_and_registered_skill_policy",
                target_outcome="Return a policy-compliant answer grounded in registered Skills.",
                positive_checks=(
                    "Reject requests outside registered Skill scope before model execution.",
                    "Require successful specialist evidence for matched requests.",
                ),
                negative_checks=("Never use an unregistered capability or Tool.",),
                evidence_requirements=("Persisted events and passed child evidence gates.",),
                pass_label="policy_and_evidence_accepted",
            ),
        )
        gate = TaskGate(
            id=f"{root.id}:acceptance", graph_id=graph.id, node_id=root.id,
            kind="acceptance",
        )
        await self._repository.create_task_graph(graph, root, gate)
        return (await self._repository.get_task_graph(run.id))  # type: ignore[return-value]

    async def start(self, run: AgentRun, user_intent: str) -> tuple[TaskGraph, TaskNode, NodeAttempt]:
        snapshot = await self.ensure(run, user_intent)
        graph, root = snapshot.graph, self._root(snapshot)
        expected_version = graph.version
        graph.status, graph.updated_at = TaskGraphStatus.RUNNING, utc_now()
        graph.version = expected_version + 1
        if not await self._repository.compare_and_swap_task_graph(graph, expected_version):
            raise RuntimeError("GRAPH_VERSION_CONFLICT")
        attempt = await self._repository.claim_task_node(
            root.id, self._runtime_instance_id, lease_seconds=600
        )
        if attempt is None:
            raise RuntimeError("ROOT_NODE_NOT_CLAIMABLE")
        root.status, root.started_at = TaskNodeStatus.RUNNING, attempt.started_at
        return graph, root, attempt

    async def record_event(self, run: AgentRun, event: AgentEvent) -> dict[str, Any] | None:
        snapshot = await self._repository.get_task_graph(run.id)
        if snapshot is None:
            return
        if event.type in {"graph_planned", "graph_reconciled"}:
            await self._apply_plan(snapshot, event)
        elif event.type == "agent_started":
            return await self._start_child(snapshot, event)
        elif event.type == "node_skipped":
            await self._skip_node(snapshot, event)
        elif event.type == "tool_completed" and not bool(event.data.get("is_error", False)):
            child_id, tool = str(event.data.get("child_run_id", "")), str(event.data.get("tool", ""))
            if child_id and tool not in {
                "nino_runtime_dispatch_agent", "nino_runtime_read_reference",
                "nino_runtime_request_clarification",
            }:
                self._successful_evidence.setdefault(child_id, []).append(tool)
        elif event.type in {"agent_completed", "agent_failed"}:
            await self._finish_child(snapshot, event)
        return None

    async def finish(
        self, graph: TaskGraph, root: TaskNode, attempt: NodeAttempt, result: RunResult
    ) -> None:
        now, completed, cancelled = utc_now(), result.status == RunStatus.COMPLETED, result.status == RunStatus.CANCELLED
        root.status = TaskNodeStatus.COMPLETED if completed else TaskNodeStatus.CANCELLED if cancelled else TaskNodeStatus.FAILED
        root.result_summary, root.error_code, root.completed_at = result.answer, result.error_code, now
        root.result = {
            "status": result.status.value, "summary": result.answer,
            "outputs": {"answer": result.answer},
            "evidence": [], "findings": [],
            "concerns": [result.error_code] if result.error_code else [],
            "recommended_next": [],
            "error_code": result.error_code, "retryable": False,
        }
        attempt.status = AttemptStatus.COMPLETED if completed else AttemptStatus.CANCELLED if cancelled else AttemptStatus.FAILED
        attempt.completed_at, attempt.error_code = now, result.error_code
        attempt.lease_owner, attempt.lease_expires_at = None, None
        attempt.checkpoint = {"steps": result.steps, "skill_id": result.skill_id}
        graph.status = TaskGraphStatus.COMPLETED if completed else TaskGraphStatus.CANCELLED if cancelled else TaskGraphStatus.FAILED
        graph.updated_at, graph.completed_at = now, now
        graph.archived_at = now
        snapshot = await self._repository.get_task_graph(result.run_id)
        if snapshot is None:
            raise RuntimeError("TaskGraph disappeared while completing a Run.")
        # Planning/reconcile events update the stored graph while this attempt is running. Merge
        # their revision metadata instead of overwriting it with the stale start-of-attempt object.
        graph.metadata = snapshot.graph.metadata
        expected_version = snapshot.graph.version
        graph.version = expected_version + 1
        gate = next(item for item in snapshot.gates if item.node_id == root.id)
        gate.status = GateStatus.PASSED if completed else GateStatus.FAILED
        gate.verdict = "Runtime completion policy satisfied." if completed else f"Run failed: {result.error_code or 'unknown'}"
        gate.evidence = tuple(item.id for item in snapshot.gates if item.node_id != root.id and item.status == GateStatus.PASSED)
        if completed and not gate.evidence:
            gate.evidence = ("policy_rejection_or_clarification_recorded",)
        gate.evaluated_at = now
        if not completed:
            await self._repository.close_open_task_nodes(
                result.run_id, cancelled, result.error_code or "GRAPH_TERMINATED"
            )
        await self._repository.commit_task_node(root, gate, attempt)
        if not await self._repository.compare_and_swap_task_graph(graph, expected_version):
            raise RuntimeError("GRAPH_VERSION_CONFLICT")

    async def fail_unexpected(
        self, graph: TaskGraph, root: TaskNode, attempt: NodeAttempt, error_code: str
    ) -> None:
        await self.finish(
            graph, root, attempt,
            RunResult(graph.run_id, RunStatus.FAILED, error_code, None, 0, (), error_code),
        )

    async def _start_child(
        self, snapshot: TaskGraphSnapshot, event: AgentEvent
    ) -> dict[str, Any]:
        child_id = str(event.data.get("child_run_id", ""))
        if not child_id:
            return {"execute": False, "reason": "missing_child_id"}
        plan_node_id = str(event.data.get("plan_node_id", ""))
        node_id = f"{snapshot.graph.id}:plan:{plan_node_id or child_id}"
        current = await self._repository.get_task_graph(snapshot.graph.run_id)
        if current is None:
            return {"execute": False, "reason": "graph_missing"}
        existing = next((item for item in current.nodes if item.id == node_id), None)
        root = self._root(snapshot)
        agent_id, skill_id = str(event.data.get("agent_id", "specialist")), str(event.data.get("skill_id", ""))
        node_kind = str(event.data.get("node_kind", "specialist"))
        raw_dependencies = event.data.get("depends_on", ())
        if isinstance(raw_dependencies, str):
            raw_dependencies = (raw_dependencies,)
        dependencies = tuple(
            f"{snapshot.graph.id}:plan:{item}" for item in raw_dependencies
        )
        node = existing or self._planned_node(
            snapshot.graph.id, root.id, plan_node_id or child_id, node_kind,
            agent_id, skill_id, str(event.data.get("task", "Execute delegated Skill task")),
            dependencies,
        )
        node.metadata = {**dict(node.metadata), "child_run_id": child_id, "skill_id": skill_id}
        gate = next((item for item in current.gates if item.node_id == node.id), None)
        if gate is None:
            gate = self._planned_gate(node, agent_id)
        await self._repository.upsert_task_node(node)
        await self._repository.upsert_task_gate(gate)
        attempt = await self._repository.claim_task_node(
            node.id, self._runtime_instance_id, lease_seconds=600
        )
        if attempt is None:
            refreshed = await self._repository.get_task_graph(snapshot.graph.run_id)
            persisted = next((item for item in refreshed.nodes if item.id == node.id), None) if refreshed else None
            if persisted is not None and persisted.status == TaskNodeStatus.COMPLETED:
                return {"execute": False, "reason": "already_completed", "result": dict(persisted.result)}
            return {"execute": False, "reason": "node_not_ready"}
        return {"execute": True, "attempt_id": attempt.id}

    async def _finish_child(self, snapshot: TaskGraphSnapshot, event: AgentEvent) -> None:
        child_id = str(event.data.get("child_run_id", ""))
        current = await self._repository.get_task_graph(snapshot.graph.run_id)
        if current is None:
            return
        plan_node_id = str(event.data.get("plan_node_id", ""))
        node_id = f"{snapshot.graph.id}:plan:{plan_node_id or child_id}"
        node = next((item for item in current.nodes if item.id == node_id), None)
        if node is None:
            return
        evidence = tuple(dict.fromkeys(self._successful_evidence.pop(child_id, ())))
        accepted = event.type == "agent_completed" and event.data.get("status") == "completed" and bool(evidence)
        now = utc_now()
        node.status, node.completed_at = (TaskNodeStatus.COMPLETED if accepted else TaskNodeStatus.FAILED), now
        node.error_code = None if accepted else str(event.data.get("error_code", "EVIDENCE_GATE_FAILED"))
        node_result = event.data.get("node_result", {})
        node.result = dict(node_result) if isinstance(node_result, dict) else {}
        node.result_summary = str(
            event.data.get(
                "result_summary",
                "Specialist completed with persisted Tool evidence." if accepted else "",
            )
        )
        gate = next(item for item in current.gates if item.node_id == node.id)
        passed_verdict = (
            f"Independent {node.kind} returned PASS with Tool evidence."
            if node.kind in {"verification", "review", "critique"}
            else "Successful Tool Observation recorded."
        )
        gate.status, gate.verdict = (
            (GateStatus.PASSED, passed_verdict)
            if accepted else (GateStatus.FAILED, "Missing evidence or evaluator PASS verdict.")
        )
        gate.evidence, gate.evaluated_at = evidence, now
        attempt = next(item for item in reversed(current.attempts) if item.node_id == node.id)
        attempt.status, attempt.completed_at = (AttemptStatus.COMPLETED if accepted else AttemptStatus.FAILED), now
        attempt.error_code, attempt.checkpoint = node.error_code, {"evidence_tools": list(evidence)}
        attempt.lease_owner, attempt.lease_expires_at = None, None
        await self._repository.commit_task_node(node, gate, attempt)

    async def _apply_plan(self, snapshot: TaskGraphSnapshot, event: AgentEvent) -> None:
        current = await self._repository.get_task_graph(snapshot.graph.run_id)
        if current is None:
            return
        existing = {item.id: item for item in current.nodes}
        root = self._root(current)
        raw_nodes = event.data.get("nodes", ())
        if not isinstance(raw_nodes, list):
            return
        for raw in raw_nodes:
            if not isinstance(raw, dict):
                continue
            logical_id = str(raw.get("node_id", ""))
            if not logical_id:
                continue
            node_id = f"{current.graph.id}:plan:{logical_id}"
            if node_id in existing:
                continue
            dependencies = tuple(
                f"{current.graph.id}:plan:{item}" for item in raw.get("depends_on", ())
            )
            node = self._planned_node(
                current.graph.id, root.id, logical_id, str(raw.get("kind", "specialist")),
                str(raw.get("agent_id", "specialist")), str(raw.get("skill_id", "")),
                str(raw.get("task", "Execute planned task")), dependencies,
                raw.get("acceptance_contract"), raw.get("input_bindings", ()),
            )
            await self._repository.upsert_task_node(node)
            await self._repository.upsert_task_gate(
                self._planned_gate(node, node.owner_agent_id)
            )
        expected_version = current.graph.version
        current.graph.version = expected_version + 1
        current.graph.updated_at = utc_now()
        revisions = list(current.graph.metadata.get("revisions", ()))
        revisions.append({
            "revision": event.data.get("revision"), "event": event.type,
            "node_count": len(raw_nodes), "recorded_at": utc_now().isoformat(),
        })
        current.graph.metadata = {**dict(current.graph.metadata), "revisions": revisions}
        if not await self._repository.compare_and_swap_task_graph(
            current.graph, expected_version
        ):
            raise RuntimeError("GRAPH_VERSION_CONFLICT")

    async def _skip_node(self, snapshot: TaskGraphSnapshot, event: AgentEvent) -> None:
        logical_id = str(event.data.get("plan_node_id", ""))
        current = await self._repository.get_task_graph(snapshot.graph.run_id)
        if current is None or not logical_id:
            return
        node_id = f"{current.graph.id}:plan:{logical_id}"
        node = next((item for item in current.nodes if item.id == node_id), None)
        if node is None:
            return
        now = utc_now()
        node.status, node.completed_at = TaskNodeStatus.SKIPPED, now
        node.error_code = "DEPENDENCY_FAILED"
        gate = next(item for item in current.gates if item.node_id == node.id)
        gate.status, gate.verdict, gate.evaluated_at = (
            GateStatus.BLOCKED, "A required dependency failed.", now
        )
        await self._repository.upsert_task_node(node)
        await self._repository.upsert_task_gate(gate)

    @staticmethod
    def _planned_node(
        graph_id: str, root_id: str, logical_id: str, kind: str, agent_id: str,
        skill_id: str, task: str, dependencies: tuple[str, ...],
        raw_contract: Any = None, raw_bindings: Any = (),
    ) -> TaskNode:
        contract_data = raw_contract if isinstance(raw_contract, Mapping) else {}
        default_target = (
            f"Independently evaluate upstream evidence through {kind}."
            if kind in {"verification", "review", "critique"}
            else task
        )
        contract = AcceptanceContract(
            spec_source=str(
                contract_data.get("spec_source", f"registered_skill:{skill_id}")
            ),
            target_outcome=str(contract_data.get("target_outcome", default_target)),
            positive_checks=tuple(
                str(item) for item in contract_data.get(
                    "positive_checks", ("Node completes successfully.",)
                )
            ),
            negative_checks=tuple(
                str(item) for item in contract_data.get(
                    "negative_checks",
                    ("No Tool outside Agent and Skill allowlists is invoked.",),
                )
            ),
            evidence_requirements=tuple(
                str(item) for item in contract_data.get(
                    "evidence_requirements",
                    ("At least one successful non-reference Tool Observation.",),
                )
            ),
            gaps=tuple(str(item) for item in contract_data.get("gaps", ())),
            pass_label=str(contract_data.get(
                "pass_label",
                f"independently_{kind}_passed"
                if kind in {"verification", "review", "critique"}
                else "worker_evidence_accepted",
            )),
        )
        return TaskNode(
            id=f"{graph_id}:plan:{logical_id}", graph_id=graph_id, kind=kind,
            owner_agent_id=agent_id, title=task,
            contract=contract,
            parent_node_id=root_id, dependencies=dependencies,
            metadata={
                "logical_node_id": logical_id,
                "skill_id": skill_id,
                "input_bindings": list(raw_bindings) if isinstance(raw_bindings, list) else [],
            },
        )

    @staticmethod
    def _planned_gate(node: TaskNode, agent_id: str) -> TaskGate:
        evaluator = node.kind in {"verification", "review", "critique"}
        gate_kind = "independent_verification" if node.kind == "verification" else node.kind
        return TaskGate(
            id=f"{node.id}:evidence", graph_id=node.graph_id, node_id=node.id,
            kind=gate_kind if evaluator else "evidence",
            evaluator_agent_id=agent_id if evaluator else None,
        )

    @staticmethod
    def _root(snapshot: TaskGraphSnapshot) -> TaskNode:
        return next(item for item in snapshot.nodes if item.kind == "orchestration")
