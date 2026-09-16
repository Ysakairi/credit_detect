"""SOP 統計カーネルの回帰。Analyzer が LLM 数値を使わず強分離を再現すること。

【Agent Engine 上の位置づけ】
Agent Engine では Python exec を禁止しているため、フィクスチャ行で
evaluate_feature_comprehensive が V14 を強分離と判定することを固定する。
空結果は error を返し、Reflection が捏造指標を書かないようにする。

【主な構成】
- test_empty: 0 件でも例外にしない
- test_audit_features_are_strong: 監査変数 V14 が is_strong_separator
"""

import unittest

from agent.tools.bq_client import default_fraud_rows
from agent.tools.data_analyzer import run_statistical_analysis


class AnalyzerTest(unittest.TestCase):
    def test_empty(self):
        """SQL 失敗後も Analyzer が落ちず、query() を完了させる。"""
        result = run_statistical_analysis([])
        self.assertEqual(result["n_rows"], 0)

    def test_audit_features_are_strong(self):
        """合成データの V14 シフトが SOP 第7章の強分離条件を満たすこと。デモと CI の前提。"""
        summary = run_statistical_analysis(default_fraud_rows())
        self.assertGreaterEqual(summary["n_rows"], 10)
        self.assertIn("V14", summary["feature_metrics"])
        self.assertTrue(summary["feature_metrics"]["V14"]["is_strong_separator"])
        self.assertIn("V14", summary["strong_separators"])


if __name__ == "__main__":
    unittest.main()
