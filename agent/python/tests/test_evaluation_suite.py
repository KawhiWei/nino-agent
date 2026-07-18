from __future__ import annotations

import unittest

from evals.live_benchmark import DEFAULT_SUITE, load_suite


class EvaluationSuiteTests(unittest.TestCase):
    def test_standard_suite_is_declared_and_matches_skill_contract(self) -> None:
        suite = load_suite(DEFAULT_SUITE)

        self.assertEqual("nino-data.analysis", suite.skill_id)
        self.assertEqual("nino-data.analysis.standard", suite.id)
        self.assertEqual(8, len(suite.cases))
        self.assertEqual(5, sum("smoke" in case.tags for case in suite.cases))
        self.assertEqual(len(suite.cases), len({case.id for case in suite.cases}))
        self.assertTrue(all(case.derived_from for case in suite.cases))


if __name__ == "__main__":
    unittest.main()
