"""
Python REPL ノードで実行される高度特徴量妥当性検証ロジック
SOP-MLOPS-2026-002 第7章
"""

from __future__ import annotations

from typing import Dict, Iterable, Optional

import numpy as np

try:
    from scipy import stats as scipy_stats
except ImportError:  # pragma: no cover - scipy is optional for local tests
    scipy_stats = None


def _as_1d(values: Iterable[float]) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    return arr.reshape(-1)


def ks_2samp(fraud_vals: np.ndarray, normal_vals: np.ndarray) -> tuple[float, float]:
    """Two-sample KS statistic and p-value (scipy if available)."""
    if scipy_stats is not None:
        result = scipy_stats.ks_2samp(fraud_vals, normal_vals)
        return float(result.statistic), float(result.pvalue)

    # Empirical CDF supremum; p-value is left unset without scipy.
    all_x = np.unique(np.concatenate([fraud_vals, normal_vals]))
    n1 = max(len(fraud_vals), 1)
    n0 = max(len(normal_vals), 1)
    cdf1 = np.searchsorted(np.sort(fraud_vals), all_x, side="right") / n1
    cdf0 = np.searchsorted(np.sort(normal_vals), all_x, side="right") / n0
    ks_stat = float(np.max(np.abs(cdf1 - cdf0))) if all_x.size else 0.0
    return ks_stat, float("nan")


def cliffs_delta(
    fraud_vals: np.ndarray,
    normal_vals: np.ndarray,
    *,
    max_fraud: int = 500,
    max_normal: int = 1000,
    rng: Optional[np.random.Generator] = None,
) -> float:
    """SOP 3.3 / 第7章: 大規模時はサンプルダウンして δ を算出する。"""
    rng = rng or np.random.default_rng(0)
    s_fraud = fraud_vals
    s_normal = normal_vals
    if len(fraud_vals) > max_fraud:
        s_fraud = rng.choice(fraud_vals, size=max_fraud, replace=False)
    if len(normal_vals) > max_normal:
        s_normal = rng.choice(normal_vals, size=max_normal, replace=False)
    if len(s_fraud) == 0 or len(s_normal) == 0:
        return float("nan")
    diff_matrix = s_fraud[:, None] - s_normal[None, :]
    return float(
        (np.sum(diff_matrix > 0) - np.sum(diff_matrix < 0))
        / (len(s_fraud) * len(s_normal))
    )


def information_value(
    fraud_vals: np.ndarray,
    normal_vals: np.ndarray,
    n_bins: int = 10,
) -> tuple[float, np.ndarray]:
    """Equal-frequency bins with Laplace smoothing. Returns (IV, per-bin WoE)."""
    values = np.concatenate([normal_vals, fraud_vals])
    labels = np.concatenate(
        [np.zeros(len(normal_vals), dtype=int), np.ones(len(fraud_vals), dtype=int)]
    )
    if values.size == 0:
        return float("nan"), np.array([])

    n_bins = int(min(n_bins, max(len(np.unique(values)), 1)))
    order = np.argsort(values, kind="mergesort")
    bin_ids = np.empty(len(values), dtype=int)
    bin_ids[order] = np.minimum(n_bins - 1, (np.arange(len(values)) * n_bins) // len(values))

    total_pos = max(len(fraud_vals), 1)
    total_neg = max(len(normal_vals), 1)
    iv = 0.0
    woe_bins = []
    for b in range(n_bins):
        mask = bin_ids == b
        n_pos = float(np.sum(labels[mask] == 1))
        n_neg = float(np.sum(labels[mask] == 0))
        dist_pos = (n_pos + 0.5) / (total_pos + 0.5 * n_bins)
        dist_neg = (n_neg + 0.5) / (total_neg + 0.5 * n_bins)
        woe = float(np.log(dist_pos / dist_neg))
        iv += (dist_pos - dist_neg) * woe
        woe_bins.append(woe)
    return float(iv), np.asarray(woe_bins)


def rate_ks(ks_stat: float) -> str:
    if ks_stat >= 0.50:
        return "卓越"
    if ks_stat >= 0.40:
        return "優秀"
    if ks_stat >= 0.20:
        return "許容"
    return "分離能不足"


def rate_iv(iv: float) -> str:
    if iv >= 0.50:
        return "過学習疑い"
    if iv >= 0.30:
        return "強い予測力"
    if iv >= 0.10:
        return "中程度"
    if iv >= 0.02:
        return "弱い予測力"
    return "無効変数"


def rate_cliffs(delta: float) -> str:
    abs_d = abs(delta)
    if abs_d >= 0.474:
        return "Large"
    if abs_d >= 0.330:
        return "Medium"
    if abs_d >= 0.147:
        return "Small"
    return "Negligible"


def rate_z(z_score_diff: float) -> str:
    return "有意 (参考)" if abs(z_score_diff) >= 3.0 else "参考値未満"


def rate_pr_auc(pr_auc: float) -> str:
    return "PASS" if pr_auc >= 0.80 else "FAIL"


def rate_capture_0_1(rate: float) -> str:
    return "PASS" if rate >= 0.75 else "FAIL"


def rate_psi(psi: float) -> str:
    if psi < 0.10:
        return "Green"
    if psi < 0.25:
        return "Yellow"
    return "Red"


def evaluate_feature_comprehensive(
    normal_vals: np.ndarray, fraud_vals: np.ndarray
) -> dict:
    """
    Zスコア、KS統計量、Cliff's Delta、歪度・尖度を統合計算する
    """
    normal_vals = _as_1d(normal_vals)
    fraud_vals = _as_1d(fraud_vals)

    # 1. 従来の Z-score 差
    mean_normal, std_normal = np.mean(normal_vals), np.std(normal_vals) + 1e-9
    mean_fraud = np.mean(fraud_vals) if len(fraud_vals) else np.nan
    z_score_diff = (mean_fraud - mean_normal) / std_normal

    # 2. KS統計量 (ノンパラメトリック分布分離度)
    ks_stat, ks_pvalue = ks_2samp(fraud_vals, normal_vals)

    # 3. Cliff's Delta (ノンパラメトリック効果量)
    cliffs = cliffs_delta(fraud_vals, normal_vals)

    # 4. 歪度 (Skewness) と尖度 (Kurtosis) の比較
    if scipy_stats is not None and len(fraud_vals) > 2:
        skew_fraud = float(scipy_stats.skew(fraud_vals))
        kurt_fraud = float(scipy_stats.kurtosis(fraud_vals))
    else:
        centered = fraud_vals - np.mean(fraud_vals) if len(fraud_vals) else np.array([])
        std = np.std(fraud_vals) + 1e-9
        skew_fraud = (
            float(np.mean((centered / std) ** 3)) if len(fraud_vals) else float("nan")
        )
        kurt_fraud = (
            float(np.mean((centered / std) ** 4) - 3.0) if len(fraud_vals) else float("nan")
        )

    iv, woe_bins = information_value(fraud_vals, normal_vals)

    # 判定フラグ
    is_strong_separator = (ks_stat >= 0.40) and (abs(cliffs) >= 0.33)

    return {
        "z_score_diff": float(z_score_diff),
        "ks_statistic": float(ks_stat),
        "ks_pvalue": float(ks_pvalue),
        "cliffs_delta": float(cliffs),
        "skewness_fraud": float(skew_fraud),
        "kurtosis_fraud": float(kurt_fraud),
        "information_value": float(iv),
        "woe_bins": woe_bins.tolist(),
        "ks_rating": rate_ks(ks_stat),
        "iv_rating": rate_iv(iv),
        "cliffs_rating": rate_cliffs(cliffs),
        "z_rating": rate_z(z_score_diff),
        "is_strong_separator": bool(is_strong_separator),
    }


def population_stability_index(
    baseline: np.ndarray, daily: np.ndarray, n_bins: int = 10
) -> Dict[str, float]:
    """Equal-width PSI on scores assumed in [0, 1]."""
    baseline = _as_1d(baseline)
    daily = _as_1d(daily)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    base_counts, _ = np.histogram(np.clip(baseline, 0, 1), bins=edges)
    daily_counts, _ = np.histogram(np.clip(daily, 0, 1), bins=edges)
    base_pct = np.maximum(base_counts / max(base_counts.sum(), 1), 1e-4)
    daily_pct = np.maximum(daily_counts / max(daily_counts.sum(), 1), 1e-4)
    psi = float(np.sum((daily_pct - base_pct) * np.log(daily_pct / base_pct)))
    return {"psi": psi, "psi_rating": rate_psi(psi)}
