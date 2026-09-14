# SOP-MLOPS-2026-002（エージェント投入用要約）

文書管理番号: SOP-MLOPS-2026-002
対象モデル: BigQuery ML BOOSTED_TREE_CLASSIFIER（`dwh_prod.ulb_fraud_detection_model`）
適用: credit_detect 日次推論、自律型調査 Agent

## 目的

不正率約 0.17% の極端不均衡データでは ROC-AUC が見かけ倒しになりやすい。
PCA 特徴量 V1〜V28 は歪度・尖度が大きく、Zスコア差（ΔZ）を一次判定にしてはならない。

## 必須評価指標

1. 分布乖離: KS統計量、IV / WoE、Cliff's Delta。ΔZ は参考値。
2. 説明性: ML.FEATURE_IMPORTANCE（Gain/Cover/Weight）、ML.GLOBAL_EXPLAIN、ML.EXPLAIN_PREDICT。
3. 予測性能: PR-AUC >= 0.80、Capture Rate@0.1% >= 0.75、コスト感応損失。
4. 安定性: PSI < 0.10 安定、0.10–0.25 警戒、>= 0.25 重大（再学習・エスカレーション）。

## KS 判定

- KS >= 0.50: 卓越（自動遮断ルール候補）
- KS >= 0.40: 優秀
- KS >= 0.20: 許容（複合条件が必要）
- KS < 0.20: 分離能不足

## IV 判定

- IV < 0.02 無効、0.02–0.10 弱い、0.10–0.30 中程度、0.30–0.50 強い、>= 0.50 はリーク/過学習を疑う。

## Cliff's Delta

- |δ| >= 0.474 Large、>= 0.330 Medium。SOP 第7章の強分離フラグは KS>=0.40 かつ |δ|>=0.33。

## 監査重点変数

POL-SEC-2026-004 第7条: V14, V17, V12 が Tree SHAP 上位5に入っていることを四半期監査する。

## エージェントへの指示

高リスク抽出の既定閾値は fraud_probability >= 0.85。
局所説明は `ulb_fraud_detection_local_explain` または ML.EXPLAIN_PREDICT を使う。
数値は SQL 結果と評価テーブルから引用し、捏造しない。
