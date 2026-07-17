from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, Sequence, TypeVar


class SchedulableNode(Protocol):
    node_id: str
    depends_on: tuple[str, ...]


NodeT = TypeVar("NodeT", bound=SchedulableNode)


@dataclass(frozen=True, slots=True)
class ScheduleDecision:
    """One deterministic scheduler decision over a validated DAG revision."""

    ready_ids: tuple[str, ...]
    blocked_ids: tuple[str, ...]


class TaskGraphScheduler:
    """Validate DAG revisions and select ready/blocked nodes from durable outcomes.

    This class contains no model or storage logic. Repositories still perform the authoritative
    transactional Node claim immediately before execution, so a scheduler decision is advisory
    and remains safe when more than one Runtime instance observes the same Graph.
    """

    _NODE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}")

    def validate(self, nodes: Sequence[SchedulableNode], known: dict[str, bool]) -> str | None:
        ids = {node.node_id for node in nodes}
        if len(ids) != len(nodes):
            return "TaskGraph node ids must be unique within one revision."
        for node in nodes:
            if self._NODE_ID.fullmatch(node.node_id) is None:
                return f"Invalid TaskGraph node id: {node.node_id}"
            unknown = set(node.depends_on) - ids - set(known)
            if unknown:
                return (
                    f"Node {node.node_id} has unknown dependencies: "
                    f"{', '.join(sorted(unknown))}"
                )

        remaining = set(ids)
        resolved = set(known)
        dependencies = {node.node_id: set(node.depends_on) for node in nodes}
        while remaining:
            ready = {node_id for node_id in remaining if dependencies[node_id] <= resolved}
            if not ready:
                return "TaskGraph revision contains a dependency cycle."
            resolved.update(ready)
            remaining -= ready
        return None

    def decide(
        self, nodes: Sequence[NodeT], outcomes: dict[str, bool]
    ) -> ScheduleDecision:
        blocked = tuple(
            node.node_id for node in nodes
            if any(dependency in outcomes and not outcomes[dependency]
                   for dependency in node.depends_on)
        )
        blocked_set = set(blocked)
        ready = tuple(
            node.node_id for node in nodes
            if node.node_id not in blocked_set
            and all(outcomes.get(dependency, False) for dependency in node.depends_on)
        )
        return ScheduleDecision(ready, blocked)
