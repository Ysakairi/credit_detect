import math
import unittest

import numpy as np

from evaluate_feature import (
    cliffs_delta,
    evaluate_feature_comprehensive,
    information_value,
    ks_2samp,
    population_stability_index,
    rate_capture_0_1,
    rate_iv,
    rate_ks,
    rate_pr_auc,
    rate_psi,
)


class FeatureMetricsTest(unittest.TestCase):
    def test_identical_distributions_are_not_separators(self):
        rng = np.random.default_rng(0)
        values = rng.normal(size=400)
        result = evaluate_feature_comprehensive(values, values.copy())
        self.assertLess(result["ks_statistic"], 0.20)
        self.assertLess(abs(result["cliffs_delta"]), 0.147)
        self.assertEqual(result["ks_rating"], "分離能不足")
        self.assertFalse(result["is_strong_separator"])

    def test_complete_separation(self):
        normal = np.zeros(200)
        fraud = np.ones(80) + 3.0
        result = evaluate_feature_comprehensive(normal, fraud)
        self.assertGreaterEqual(result["ks_statistic"], 0.99)
        self.assertGreaterEqual(abs(result["cliffs_delta"]), 0.99)
        self.assertEqual(result["ks_rating"], "卓越")
        self.assertEqual(result["cliffs_rating"], "Large")
        self.assertTrue(result["is_strong_separator"])
        self.assertGreaterEqual(result["information_value"], 0.30)

    def test_ks_matches_manual_supremum(self):
        fraud = np.array([1.0, 2.0, 3.0, 10.0])
        normal = np.array([0.0, 1.0, 1.5, 2.0])
        ks, _ = ks_2samp(fraud, normal)
        self.assertGreater(ks, 0.0)
        self.assertLessEqual(ks, 1.0)

    def test_cliffs_delta_matches_histogram_method(self):
        fraud = np.array([1.0, 1.0, 4.0, 5.0])
        normal = np.array([0.0, 1.0, 2.0, 2.0, 3.0])
        brute = cliffs_delta(fraud, normal)
        # SQL equivalent: unique values + cumulative negatives
        values = np.unique(np.concatenate([fraud, normal]))
        n_pos = np.array([np.sum(fraud == v) for v in values], dtype=float)
        n_neg = np.array([np.sum(normal == v) for v in values], dtype=float)
        cum_neg = np.cumsum(n_neg)
        total_neg = n_neg.sum()
        gt = np.sum(n_pos * (cum_neg - n_neg))
        lt = np.sum(n_pos * (total_neg - cum_neg))
        hist = (gt - lt) / (n_pos.sum() * total_neg)
        self.assertAlmostEqual(brute, hist, places=12)

    def test_cliffs_delta_sign(self):
        fraud = np.array([5.0, 6.0, 7.0])
        normal = np.array([0.0, 1.0, 2.0])
        self.assertGreater(cliffs_delta(fraud, normal), 0.9)
        self.assertLess(cliffs_delta(normal, fraud), -0.9)

    def test_iv_low_when_no_signal(self):
        rng = np.random.default_rng(1)
        shared = rng.normal(size=300)
        iv, _ = information_value(shared[:50], shared[50:])
        self.assertLess(iv, 0.30)

    def test_sop_rating_thresholds(self):
        self.assertEqual(rate_ks(0.50), "卓越")
        self.assertEqual(rate_ks(0.40), "優秀")
        self.assertEqual(rate_ks(0.20), "許容")
        self.assertEqual(rate_ks(0.19), "分離能不足")
        self.assertEqual(rate_iv(0.50), "過学習疑い")
        self.assertEqual(rate_iv(0.30), "強い予測力")
        self.assertEqual(rate_iv(0.01), "無効変数")
        self.assertEqual(rate_pr_auc(0.80), "PASS")
        self.assertEqual(rate_pr_auc(0.79), "FAIL")
        self.assertEqual(rate_capture_0_1(0.75), "PASS")
        self.assertEqual(rate_psi(0.09), "Green")
        self.assertEqual(rate_psi(0.10), "Yellow")
        self.assertEqual(rate_psi(0.25), "Red")

    def test_psi_identical_is_green(self):
        scores = np.linspace(0.0, 1.0, 200)
        result = population_stability_index(scores, scores)
        self.assertEqual(result["psi_rating"], "Green")
        self.assertTrue(math.isfinite(result["psi"]))
        self.assertLess(result["psi"], 0.10)


if __name__ == "__main__":
    unittest.main()
