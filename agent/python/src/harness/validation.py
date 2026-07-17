from __future__ import annotations

from dataclasses import dataclass

from framework import GateStatus, TaskGraphSnapshot, TaskNodeStatus


@dataclass(frozen=True, slots=True)
class HarnessLintIssue:
    code: str
    message: str
    node_id: str | None = None


def lint_task_graph(snapshot: TaskGraphSnapshot) -> tuple[HarnessLintIssue, ...]:
    issues: list[HarnessLintIssue] = []
    nodes = {node.id: node for node in snapshot.nodes}
    gates_by_node = {
        node_id: tuple(gate for gate in snapshot.gates if gate.node_id == node_id)
        for node_id in nodes
    }
    for node in snapshot.nodes:
        missing = set(node.dependencies) - nodes.keys()
        if missing:
            issues.append(HarnessLintIssue(
                "UNKNOWN_DEPENDENCY",
                f"Node references unknown dependencies: {', '.join(sorted(missing))}",
                node.id,
            ))
        required = tuple(gate for gate in gates_by_node[node.id] if gate.required)
        if not required:
            issues.append(HarnessLintIssue(
                "REQUIRED_GATE_MISSING", "Node has no required acceptance gate.", node.id
            ))
        if node.status == TaskNodeStatus.COMPLETED and any(
            gate.status != GateStatus.PASSED for gate in required
        ):
            issues.append(HarnessLintIssue(
                "COMPLETED_WITHOUT_PASSED_GATE",
                "Completed Node has a required Gate that did not pass.", node.id,
            ))
        running_attempts = tuple(
            attempt for attempt in snapshot.attempts
            if attempt.node_id == node.id and attempt.status.value == "running"
        )
        if node.status == TaskNodeStatus.RUNNING and len(running_attempts) != 1:
            issues.append(HarnessLintIssue(
                "RUNNING_ATTEMPT_MISMATCH",
                "Running Node must have exactly one running Attempt.", node.id,
            ))
        if node.status != TaskNodeStatus.RUNNING and running_attempts:
            issues.append(HarnessLintIssue(
                "TERMINAL_NODE_HAS_RUNNING_ATTEMPT",
                "Non-running Node retains a running Attempt.", node.id,
            ))

    remaining = set(nodes)
    resolved: set[str] = set()
    while remaining:
        ready = {
            node_id for node_id in remaining
            if set(nodes[node_id].dependencies) <= resolved
        }
        if not ready:
            issues.append(HarnessLintIssue(
                "DEPENDENCY_CYCLE", "TaskGraph contains a dependency cycle."
            ))
            break
        resolved.update(ready)
        remaining -= ready
    return tuple(issues)
