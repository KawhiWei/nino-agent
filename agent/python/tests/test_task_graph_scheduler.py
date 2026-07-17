from __future__ import annotations

import unittest
from dataclasses import dataclass

from harness import TaskGraphScheduler


@dataclass(frozen=True)
class Node:
    node_id: str
    depends_on: tuple[str, ...] = ()


class TaskGraphSchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scheduler = TaskGraphScheduler()

    def test_validates_unknown_dependencies_and_cycles(self) -> None:
        self.assertIn(
            "unknown dependencies",
            self.scheduler.validate((Node("a", ("missing",)),), {}) or "",
        )
        self.assertIn(
            "dependency cycle",
            self.scheduler.validate((Node("a", ("b",)), Node("b", ("a",))), {}) or "",
        )

    def test_selects_ready_and_blocked_nodes_from_durable_outcomes(self) -> None:
        nodes = (
            Node("independent"),
            Node("ready", ("passed",)),
            Node("blocked", ("failed",)),
            Node("waiting", ("future",)),
        )

        decision = self.scheduler.decide(
            nodes, {"passed": True, "failed": False}
        )

        self.assertEqual(("independent", "ready"), decision.ready_ids)
        self.assertEqual(("blocked",), decision.blocked_ids)


if __name__ == "__main__":
    unittest.main()
