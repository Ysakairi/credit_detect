"""SOP statistical kernel on fixture rows."""

import unittest

from agent.tools.bq_client import default_fraud_rows
from agent.tools.data_analyzer import run_statistical_analysis


class AnalyzerTest(unittest.TestCase):
    def test_empty(self):
        result = run_statistical_analysis([])
        self.assertEqual(result["n_rows"], 0)

    def test_audit_features_are_strong(self):
        summary = run_statistical_analysis(default_fraud_rows())
        self.assertGreaterEqual(summary["n_rows"], 10)
        self.assertIn("V14", summary["feature_metrics"])
        self.assertTrue(summary["feature_metrics"]["V14"]["is_strong_separator"])
        self.assertIn("V14", summary["strong_separators"])


if __name__ == "__main__":
    unittest.main()
