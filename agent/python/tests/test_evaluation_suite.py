from __future__ import annotations

import unittest

from evals.live_benchmark import (
    DEFAULT_SUITE,
    BenchmarkFollowUp,
    load_suite,
    score,
)


class EvaluationSuiteTests(unittest.TestCase):
    def test_standard_suite_is_declared_and_matches_skill_contract(self) -> None:
        suite = load_suite(DEFAULT_SUITE)

        self.assertEqual("nino-data.analysis", suite.skill_id)
        self.assertEqual("nino-data.analysis.standard", suite.id)
        self.assertEqual(14, len(suite.cases))
        self.assertEqual(6, sum("smoke" in case.tags for case in suite.cases))
        self.assertEqual(23, sum(1 + len(case.follow_ups) for case in suite.cases))
        self.assertEqual(len(suite.cases), len({case.id for case in suite.cases}))
        self.assertTrue(all(case.derived_from for case in suite.cases))
        july = next(case for case in suite.cases if case.id == "july-summary-report")
        self.assertEqual(("nino_data_query_summary",), july.required_tools)

        order = next(case for case in suite.cases if case.id == "order-detail-margin-001")
        self.assertEqual(
            {
                "related-history",
                "unrelated-new-data",
                "unrelated-out-of-scope",
            },
            {turn.relationship for turn in order.follow_ups},
        )
        history = next(
            turn for turn in order.follow_ups if turn.relationship == "related-history"
        )
        unrelated = next(
            turn for turn in order.follow_ups
            if turn.relationship == "unrelated-new-data"
        )
        out_of_scope = next(
            turn for turn in order.follow_ups
            if turn.relationship == "unrelated-out-of-scope"
        )
        self.assertIn("history_reconciliation", history.required_model_phases)
        self.assertIn("history_reconciliation", unrelated.forbidden_model_phases)
        self.assertEqual(1, out_of_scope.max_model_calls)
        self.assertIn("planning", out_of_scope.required_model_phases)

        refund = next(case for case in suite.cases if case.id == "supplier-refund-recovery-031")
        self.assertEqual(
            {"related-history", "related-out-of-scope", "unrelated-new-data"},
            {turn.relationship for turn in refund.follow_ups},
        )

    def test_score_checks_history_outcome_and_planning_phase(self) -> None:
        follow_up = BenchmarkFollowUp(
            id="history-reformat",
            relationship="related-history",
            prompt="仅整理上一轮答案。",
            derived_from=("history policy",),
            expected_status="completed",
            expected_skill=None,
            expect_dispatch=False,
            required_tools=(),
            forbidden_tools=("nino_data_get_order_detail",),
            required_references=(),
            answer_facts=(("225",),),
            max_model_calls=2,
            expected_outcome="history_answer",
            required_events=(),
            forbidden_events=(),
            required_model_phases=("planning", "history_reconciliation"),
            forbidden_model_phases=(),
        )
        run = {
            "id": "run-1",
            "status": "completed",
            "skill_id": None,
            "answer": "收入为 225 CNY。",
        }
        events = [
            {"type": "model_started", "data": {"phase": "planning"}},
            {
                "type": "model_started",
                "data": {"phase": "history_reconciliation"},
            },
            {
                "type": "loop_checkpoint",
                "data": {
                    "state": {
                        "step": 1,
                        "max_steps": 3,
                        "action_count": 0,
                        "max_actions": 2,
                        "elapsed_ms": 10,
                        "timeout_seconds": 10,
                    }
                },
            },
            {"type": "run_completed", "data": {"outcome": "history_answer"}},
        ]

        result = score(follow_up, run, events)

        self.assertEqual(100, result["score"])
        self.assertEqual("history_answer", result["outcome"])


if __name__ == "__main__":
    unittest.main()
