import unittest

from evals import run_evals


class EvidenceEvaluationTests(unittest.TestCase):
    def test_golden_grounding_suite(self) -> None:
        report = run_evals.run_suite()

        self.assertGreaterEqual(report["total"], 50)
        self.assertEqual(report["failed"], 0)
        self.assertEqual(report["pass_rate"], 1.0)
        self.assertEqual(
            set(report["categories"]),
            {
                "exact_locator",
                "unsupported_abstention",
                "ambiguous_quote",
                "ignored_source_instructions",
            },
        )


if __name__ == "__main__":
    unittest.main()
