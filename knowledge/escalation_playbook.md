# エスカレーション・プレイブック

## Green / Yellow / Red（PSI）

- Green (PSI < 0.10): 通常の高スコア調査を継続。
- Yellow (0.10 <= PSI < 0.25): 特徴量ごとの CSI を `ulb_fraud_detection_feature_separation` で確認。
- Red (PSI >= 0.25): モデルドリフト。遮断自動化を縮小し、人へエスカレーション。

## レポート必須項目

1. 対象期間（擬似 Date または MAX(Date)）
2. 抽出件数と不正率
3. 強分離特徴量（is_strong_separator）
4. 規程条項（POL-SEC-2026-004 第2条など）
5. 監視用 SELECT（DML 禁止）

## 人間承認

Agent は BigQuery への DELETE/UPDATE/CREATE を実行しない。
是正 SQL は提案として出力し、適用は Dataform の PR レビュー後とする。
