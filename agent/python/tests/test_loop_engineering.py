from __future__ import annotations

import unittest

from framework import LoopBudget, LoopKind, LoopStatus, LoopStopReason
from harness import LoopController, strictest_budget


class LoopControllerTests(unittest.TestCase):
    def test_tracks_progress_and_terminal_snapshot_without_raw_action(self) -> None:
        loop = LoopController(
            "run-1", LoopKind.ORCHESTRATION, 3,
            LoopBudget(max_actions=2, timeout_seconds=30),
        )

        self.assertIsNone(loop.begin_step())
        self.assertIsNone(loop.register_action("tool:{\"secret\":\"value\"}"))
        self.assertIsNone(loop.record_observation(True))
        loop.stop(LoopStatus.COMPLETED, LoopStopReason.FINAL_ANSWER)
        snapshot = loop.snapshot().to_data()

        self.assertEqual("completed", snapshot["status"])
        self.assertEqual("final_answer", snapshot["stop_reason"])
        self.assertEqual(1, snapshot["successful_actions"])
        self.assertEqual(64, len(snapshot["last_action_hash"]))
        self.assertNotIn("secret", str(snapshot))

    def test_stops_duplicate_action_and_action_budget(self) -> None:
        duplicate = LoopController("run-1", LoopKind.WORKER_REACT, 3)
        duplicate.begin_step()
        self.assertIsNone(duplicate.register_action("same"))
        violation = duplicate.register_action("same")
        self.assertEqual(LoopStopReason.DUPLICATE_ACTION, violation.stop_reason)

        bounded = LoopController(
            "run-2", LoopKind.WORKER_REACT, 3,
            LoopBudget(max_actions=1, timeout_seconds=30),
        )
        bounded.begin_step()
        self.assertIsNone(bounded.register_action("first"))
        violation = bounded.register_action("second")
        self.assertEqual("MAX_ACTIONS_EXCEEDED", violation.error_code)

    def test_stops_repeated_failed_observations(self) -> None:
        loop = LoopController(
            "run-1", LoopKind.WORKER_REACT, 4,
            LoopBudget(
                max_actions=4, timeout_seconds=30,
                max_consecutive_failures=2, max_no_progress_steps=3,
            ),
        )
        loop.begin_step()
        loop.register_action("one")
        self.assertIsNone(loop.record_observation(False))
        loop.register_action("two")
        violation = loop.record_observation(False)
        self.assertEqual(LoopStopReason.CONSECUTIVE_FAILURES, violation.stop_reason)

    def test_composes_strictest_policy_layer(self) -> None:
        result = strictest_budget(
            LoopBudget(12, 180, 4, 4),
            LoopBudget(6, 60, 2, 3),
        )
        self.assertEqual(6, result.max_actions)
        self.assertEqual(60, result.timeout_seconds)
        self.assertEqual(2, result.max_consecutive_failures)
        self.assertEqual(3, result.max_no_progress_steps)


if __name__ == "__main__":
    unittest.main()
