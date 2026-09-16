"""SOP-MLOPS-2026-002 の統計カーネル。Analyzer ノードの実体。

【Agent Engine 上の位置づけ】
LLM に Python を書かせて exec しない。Agent Engine / Cloud Run を汎用コード実行環境に
すると任意コード実行になるため、日次評価と同じ ``evaluate_feature_comprehensive`` だけを
SQL 結果へ適用する。監査変数 V14/V17/V12 を先に評価し、レポートと規程照合の共通言語にする。

【主な関数構成】
- _load_evaluate_feature: extra_packages の evaluate/ を実行時 import
- _column_values / _finite: 欠損・Inf を指標計算から除外する
- run_statistical_analysis: 行集合を KS / IV / Cliff / PSI 自己比較に要約する
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import numpy as np

from agent.config import AUDIT_FEATURES, PCA_FEATURES


def _load_evaluate_feature():
    """evaluate/ を sys.path に足してから import する。Agent Engine の extra_packages 前提。

    パッケージ化せずリポジトリ相対で読むのは、日次バッチの評価コードと二重実装したくないため。

    Returns:
        (evaluate_feature_comprehensive, population_stability_index)。
    """
    evaluate_dir = Path(__file__).resolve().parents[2] / "evaluate"
    if evaluate_dir.is_dir() and str(evaluate_dir) not in sys.path:
        sys.path.insert(0, str(evaluate_dir))
    from evaluate_feature import (  # type: ignore
        evaluate_feature_comprehensive,
        population_stability_index,
    )

    return evaluate_feature_comprehensive, population_stability_index


def _column_values(records: Iterable[Dict[str, Any]], column: str) -> np.ndarray:
    """文字列や None を黙って落とし、SOP カーネルが型エラーで Analyzer 全体を落とさないようにする。

    Args:
        records: SQL 行。
        column: 取り出したい列名。

    Returns:
        float64 の 1 次元配列。該当なしは空。
    """
    values = []
    for row in records:
        if column not in row or row[column] is None:
            continue
        try:
            values.append(float(row[column]))
        except (TypeError, ValueError):
            continue
    return np.asarray(values, dtype=np.float64)


def _finite(value: Any) -> Optional[float]:
    """NaN/Inf を JSON に載せない。Agent Engine の query 応答がシリアライズ不能になるのを防ぐ。

    Args:
        value: 数値化したい値。

    Returns:
        有限 float。変換不能・非有限なら None。
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def run_statistical_analysis(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """抽出行を SOP 指標に要約する。LLM に数値を作らせず、Reflection の採点根拠を固定する。

    ラベルは真値 Class を優先する。推論テーブルだけが来たときは predicted_Class と
    規程閾値 0.85 で代理分割する。最大 12 変数に切るのは Agent Engine の CPU と
    プロンプト長のため。PSI の自己比較は学習分布が SQL 結果に無いための妥協で、
    レポートに SOP 第6章を言及できるように API だけ残す。

    Args:
        records: SQL Exec の行 dict。空なら error キーだけ返す。

    Returns:
        件数、平均 Amount/確率、強分離変数、特徴量ごとの KS/IV/Cliff、psi_self_check、notes。
    """
    evaluate_feature_comprehensive, population_stability_index = _load_evaluate_feature()
    if not records:
        return {"error": "No records found", "n_rows": 0}

    n_rows = len(records)
    amounts = _column_values(records, "Amount")
    probs = _column_values(records, "fraud_probability")

    labels = []
    for row in records:
        if row.get("Class") is not None:
            labels.append(int(float(row["Class"])))
        elif row.get("predicted_Class") is not None:
            labels.append(int(float(row["predicted_Class"])))
        elif row.get("fraud_probability") is not None and float(row["fraud_probability"]) >= 0.85:
            labels.append(1)
        else:
            labels.append(0)

    fraud_mask = np.asarray(labels) == 1
    n_fraud = int(fraud_mask.sum())
    n_normal = int((~fraud_mask).sum())

    feature_metrics: Dict[str, Any] = {}
    strong_separators: List[str] = []
    candidate_features = [
        col
        for col in list(PCA_FEATURES) + ["Amount"]
        if any(col in row for row in records)
    ]
    # Always evaluate audit variables first when present.
    ordered = [f for f in AUDIT_FEATURES if f in candidate_features] + [
        f for f in candidate_features if f not in AUDIT_FEATURES
    ]
    for feature in ordered[:12]:
        series = _column_values(records, feature)
        if series.size == 0:
            continue
        fraud_vals = series[fraud_mask[: len(series)]] if n_fraud else np.array([])
        normal_vals = series[~fraud_mask[: len(series)]] if n_normal else np.array([])
        if fraud_vals.size == 0 or normal_vals.size == 0:
            continue
        metrics = evaluate_feature_comprehensive(normal_vals, fraud_vals)
        compact = {
            "ks_statistic": metrics["ks_statistic"],
            "ks_rating": metrics["ks_rating"],
            "cliffs_delta": metrics["cliffs_delta"],
            "cliffs_rating": metrics["cliffs_rating"],
            "information_value": metrics["information_value"],
            "iv_rating": metrics["iv_rating"],
            "z_score_diff": metrics["z_score_diff"],
            "is_strong_separator": metrics["is_strong_separator"],
        }
        feature_metrics[feature] = compact
        if metrics["is_strong_separator"]:
            strong_separators.append(feature)

    psi = None
    if probs.size:
        # Without a training-score baseline in the SQL result, PSI vs itself is
        # Green by construction; still surface the API so reports mention SOP 6.
        psi = population_stability_index(probs, probs)

    mean_amount = _finite(float(np.mean(amounts))) if amounts.size else None
    mean_prob = _finite(float(np.mean(probs))) if probs.size else None

    return {
        "n_rows": n_rows,
        "n_fraud_or_high_risk": n_fraud,
        "n_normal_or_low_risk": n_normal,
        "mean_amount": mean_amount,
        "mean_fraud_probability": mean_prob,
        "audit_features": AUDIT_FEATURES,
        "strong_separators": strong_separators,
        "feature_metrics": feature_metrics,
        "psi_self_check": psi,
        "notes": (
            "Z-score difference is a reference metric only. "
            "Primary separators are KS >= 0.40 and |Cliff's Delta| >= 0.33 "
            "(SOP-MLOPS-2026-002 ch.3 / ch.7)."
        ),
    }
