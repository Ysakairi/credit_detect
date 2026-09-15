# モデル評価基準（SOP-MLOPS-2026-002）

文書管理番号 SOP-MLOPS-2026-002 に基づき、BQML `BOOSTED_TREE_CLASSIFIER` の評価指標の算出定義・判定基準・BigQuery 格納先を定義する。Looker Studio は本ドキュメントのテーブルをデータソースとする。

対象モデル: `dwh_prod.ulb_fraud_detection_model`  
適用パイプライン: `credit_detect` 日次推論（Dataform タグ `daily_batch`）

---

## 1. 評価の方針

極端な不均衡（不正率 約 0.17%）では、陰性件数が巨大なため ROC-AUC が見かけ倒しになりやすい。また PCA 特徴量（V1〜V28）は歪度・尖度が大きく、平均と標準偏差に依存する Z スコア差だけでは分離能を過大・過小評価する。

そのため評価は次の 4 階層・12 項目で行う。

| 評価カテゴリ | 評価項目 | 評価メトリクス / 手法 |
| --- | --- | --- |
| ① 分布乖離・判別力 | 非正規ロバスト乖離 | KS統計量 (Kolmogorov-Smirnov) |
|  | 情報量・予測力 | IV (Information Value) / WoE |
|  | ノンパラメトリック効果量 | Cliff's Delta (δ) |
|  | 従来基準（参考値） | 陽性・陰性 Zスコア差 (ΔZ) |
| ② BQML 固有説明性 | 分岐利得寄与度 | `ML.FEATURE_IMPORTANCE` (Gain) |
|  | サンプル網羅度 | `ML.FEATURE_IMPORTANCE` (Cover) |
|  | 大域的寄与度 | `ML.GLOBAL_EXPLAIN` (Tree SHAP) |
|  | 局所的寄与度 | `ML.EXPLAIN_PREDICT` (Local SHAP) |
| ③ 予測性能・不均衡適応 | 不均衡データ適合精度 | PR-AUC (Precision-Recall AUC) |
|  | 業務キャパシティ適合 | Top-K% Capture Rate (Recall) |
|  | 損益最適化 | Cost-Sensitive Expected Loss |
| ④ 安定性・経時変化 | ドリフト検知 | PSI (Population Stability Index) |

各指標には SOP の判定基準を STRING 列（`rating` / `*_rating`）として付与する。Looker Studio では値そのものと判定ラベルの両方を可視化できる。

---

## 2. Looker Studio 向けテーブル

日次バッチ（`daily_batch`）が `dwh_prod` に CREATE OR REPLACE する。すべての評価テーブルに `evaluation_date`（Asia/Tokyo）と `evaluated_at` を付与する。当日表示用のテーブルは 1 日分のまま残し、置換直前に現行行を `v_detection_*` へ退避して過去分を蓄積する。

### 2.1 ダッシュボードの主テーブル（ロング形式）

| テーブル | 粒度 | 用途 |
| --- | --- | --- |
| `ulb_fraud_detection_evaluation_matrix` | モデル × メトリクス 1 行 | スコアカード、合格/不合格、PSI 信号機 |
| `ulb_fraud_detection_feature_matrix` | 特徴量 × メトリクス 1 行 | 特徴量スコアカード、フラグ一覧 |

`ulb_fraud_detection_evaluation_matrix` の列:

| 列 | 内容 |
| --- | --- |
| `evaluation_date` | 評価日 (JST) |
| `evaluated_at` | 算出時刻 |
| `category_id` | 1〜4 |
| `category_name` | ①〜④ の名称 |
| `metric_name` | パラメータ名（英語キー） |
| `metric_label` | 表示名 |
| `metric_value` | 算出値 |
| `threshold_pass` | 合格閾値（ない場合 NULL） |
| `threshold_warn` | 警戒閾値（PSI の 0.25 など） |
| `rating` | SOP 判定ラベル |
| `is_pass` | デプロイ可否に使う BOOL |
| `sort_order` | 表示順 |

### 2.2 詳細テーブル（ワイド / ビン）

| テーブル | 内容 |
| --- | --- |
| `ulb_fraud_detection_evaluation` | 既存 `ML.EVALUATE`（precision / recall / f1_score / roc_auc 等） |
| `ulb_fraud_detection_feature_separation` | 特徴量ごとの KS / IV / Cliff's Delta / ΔZ と判定 |
| `ulb_fraud_detection_woe_bins` | 特徴量 × 10 ビンの WoE（WoE プロット用） |
| `ulb_fraud_detection_feature_importance` | Gain / Cover / Weight、上位5 Gain 占有率、Cover 過学習フラグ |
| `ulb_fraud_detection_global_explain` | 不正クラスの Tree SHAP、監査変数フラグ |
| `ulb_fraud_detection_local_explain` | 高リスク取引の局所 SHAP（UNNEST 済み） |
| `ulb_fraud_detection_imbalance_metrics` | PR-AUC、Capture Rate、最適閾値と最小期待損失 |
| `ulb_fraud_detection_cost_curve` | 閾値ごとの期待損失（折れ線用） |
| `ulb_fraud_detection_psi` | スコア 10 ビンの PSI 寄与と総合 PSI |

Looker Studio 推奨チャート:

- スコアカード: `evaluation_matrix` を `metric_name` でフィルタ
- 特徴量棒グラフ: `feature_separation` の `ks_statistic` / `information_value`
- 損益曲線: `cost_curve` の `threshold` × `expected_loss`（`is_optimal_threshold` で P* を強調）
- PSI 内訳: `psi` の `bin_id` × `psi_contribution`
- 局所説明: `local_explain` を取引キーでフィルタ

### 2.3 履歴バックアップ（`v_detection_*`）

当日表示用の `ulb_fraud_detection_*` は従来どおり CREATE OR REPLACE する。過去分は日次バッチの置換 **前** に、現行テーブルを同スキーマのバックアップへ `INSERT` する。バックアップテーブルは sqlx の `CREATE TABLE IF NOT EXISTS` で初回作成する。

| 当日テーブル | バックアップテーブル |
| --- | --- |
| `ulb_fraud_detection_evaluation` | `v_detection_evaluation` |
| `ulb_fraud_detection_feature_separation` | `v_detection_feature_separation` |
| `ulb_fraud_detection_woe_bins` | `v_detection_woe_bins` |
| `ulb_fraud_detection_feature_importance` | `v_detection_feature_importance` |
| `ulb_fraud_detection_global_explain` | `v_detection_global_explain` |
| `ulb_fraud_detection_local_explain` | `v_detection_local_explain` |
| `ulb_fraud_detection_imbalance_metrics` | `v_detection_imbalance_metrics` |
| `ulb_fraud_detection_cost_curve` | `v_detection_cost_curve` |
| `ulb_fraud_detection_psi` | `v_detection_psi` |

- 命名: `ulb_fraud_detection_XXXXX` → `v_detection_XXXXX`
- スキーマ: 当日テーブルと同一（余分な列は足さない）
- 粒度: `evaluation_date` 単位。既に同じ評価日がある行は再挿入しない
- 初回: 当日テーブルがまだ無い場合はバックアップをスキップする。履歴は翌日の置換前から貯まる
- 当日を含む時系列: バックアップ（過去日）と当日テーブルを UNION する
- 適用: Dataform ワークスペースで Git の再取得（pull）と再コンパイルが必要

`evaluation_matrix` / `feature_matrix` は本バックアップの対象外（当日スコアカード用）。

---

## 3. ① 分布乖離・判別力

入力は日次特徴量テーブル `ulb_fraud_detection_daily_converted` の V1〜V28, Amount, Hour。Class=1 を陽性（不正）、Class=0 を陰性（正常）とする。

### 3.1 KS統計量 (Kolmogorov-Smirnov)

$$
KS = \sup_{x} \lvert F_1(x) - F_0(x) \rvert
$$

$F_1$、$F_0$ は各群の経験分布関数。同一値はまとめてから累積し、分布形状の仮定を置かない。

| 判定 (`ks_rating`) | 基準 |
| --- | --- |
| 卓越 | $KS \ge 0.50$ |
| 優秀 | $0.40 \le KS < 0.50$ |
| 許容 | $0.20 \le KS < 0.40$ |
| 分離能不足 | $KS < 0.20$ |

マトリクス上の合否目安は $KS \ge 0.40$（優秀以上）。

### 3.2 IV (Information Value) / WoE

特徴量を等頻度 10 ビン（`NTILE(10)`）に分割する。空ビンで対数を壊さないよう、件数に 0.5 を加える Laplace 平滑を使う。

$$
WoE_b = \ln \left( \frac{P(\text{Feature} \in \text{Bin}_b \mid \text{Class}=1)}{P(\text{Feature} \in \text{Bin}_b \mid \text{Class}=0)} \right)
$$

$$
IV = \sum_{b=1}^{B} \left( P(\text{Feature} \in \text{Bin}_b \mid \text{Class}=1) - P(\text{Feature} \in \text{Bin}_b \mid \text{Class}=0) \right) \times WoE_b
$$

| 判定 (`iv_rating`) | 基準 |
| --- | --- |
| 無効変数 | $IV < 0.02$ |
| 弱い予測力 | $0.02 \le IV < 0.10$ |
| 中程度 | $0.10 \le IV < 0.30$ |
| 強い予測力 | $0.30 \le IV < 0.50$ |
| 過学習疑い | $IV \ge 0.50$（リーク精査） |

ビンごとの `woe` と `iv_contribution` は `ulb_fraud_detection_woe_bins` に格納する。

### 3.3 Cliff's Delta (δ)

$$
\delta = \frac{|\{x_1 \mid x_1 > x_0\}| - |\{x_1 \mid x_1 < x_0\}|}{n_1 \cdot n_0}
$$

同値は分子に含めない。SQL では値ごとの度数と累積陰性件数から交差積を取って算出する（全対比較の交差結合は行わない）。

| 判定 (`cliffs_rating`) | 基準 |
| --- | --- |
| Negligible | $\lvert\delta\rvert < 0.147$ |
| Small | $0.147 \le \lvert\delta\rvert < 0.330$ |
| Medium | $0.330 \le \lvert\delta\rvert < 0.474$ |
| Large | $\lvert\delta\rvert \ge 0.474$ |

`is_strong_separator` は $KS \ge 0.40$ かつ $\lvert\delta\rvert \ge 0.33$ のとき TRUE（SOP 第7章の統合フラグ）。

### 3.4 陽性・陰性 Zスコア差 (ΔZ)（参考値）

$$
\Delta Z = \frac{\bar{x}_{1} - \bar{x}_{0}}{\sigma_{0} + 10^{-9}}
$$

| 判定 (`z_rating`) | 基準 |
| --- | --- |
| 有意 (参考) | $\lvert\Delta Z\rvert \ge 3.0$ |
| 参考値未満 | それ以外 |

正規性を仮定するため、合否判定の一次指標には使わない。

---

## 4. ② BQML 固有説明性

モデル作成時に `ENABLE_GLOBAL_EXPLAIN = TRUE` が必須である。定義変更後は `initial_setup` タグでモデルを作り直す。

### 4.1 `ML.FEATURE_IMPORTANCE` (Gain)

分岐が損失関数（Log Loss）を平均でどれだけ減らしたか。モデル精度への純粋な貢献度。

判定: 上位 5 変数の Gain 合計が全体の 60% 以上なら `top5_gain_concentrated = TRUE`（マトリクス `top5_gain_share` が PASS）。

### 4.2 `ML.FEATURE_IMPORTANCE` (Cover)

その特徴量の分岐を通過したサンプルの相対量。Cover 占有率が 0.5% 未満かつ Gain 占有率が 5% 超の変数は `cover_overfit_flag = TRUE`（極少数レコードへの過学習分岐の疑い）とし、監視対象から排除候補とする。

Weight（分岐回数）も同テーブルに保持する。カーディナリティが高い連続量は Weight が膨らみやすいため、Gain との乖離を見る。

### 4.3 `ML.GLOBAL_EXPLAIN` (Tree SHAP)

不正クラス（`class` が 1）に対する平均寄与。他特徴量との共線性を織り込んだ多変量貢献度。

監査要件（社内規程 POL-SEC-2026-004 第7条）: 重要変数 **V14, V17, V12** が Tree SHAP 上位 5 位以内に入っていること。`is_audit_variable` と `audit_in_top5` で検証する。マトリクスの `audit_vars_in_shap_top5` は、存在する監査変数がすべて上位 5 に入れば「監査OK」。

### 4.4 `ML.EXPLAIN_PREDICT` (Local SHAP)

日次予測で `fraud_probability >= 0.85` の行、またはスコア上位 20 件を対象に、上位 5 特徴量の局所寄与を取得する。ARRAY は Looker Studio で扱えないため UNNEST する。

局所寄与の符号が大域 SHAP の符号と一致するか `direction_consistent` で検証する。取引横断の整合率 `local_shap_direction_consistency` は 0.80 以上を PASS とする。

---

## 5. ③ 予測性能・不均衡適応

対象は `ulb_fraud_detection_predictions`（疑似日付 1〜31 の推論結果）。陽性が 0 件の日は rating を「データ不足」とする。

### 5.1 PR-AUC (Average Precision)

不正率 0.17% では FPR の分母が大きく、ROC-AUC が 0.98 でも運用不能なことがある。合否の一次指標は PR-AUC とする。

スコア降順に並べ、各陽性サンプルでの適合率を再現率の増分 $1/N_{pos}$ で加重した Average Precision を `pr_auc` とする。

| 判定 | 基準 |
| --- | --- |
| PASS | $PR\text{-}AUC \ge 0.80$（本番デプロイ必須） |
| FAIL | それ以外 |

ROC-AUC / Precision / Recall / F1 は `ML.EVALUATE` の参考値としてマトリクスに残す。合否には使わない。

### 5.2 Top-K% Capture Rate

$$
\text{Capture Rate}@K = \frac{\text{上位 } K\% \text{ のスコア帯に含まれる不正件数}}{\text{全体の不正件数}}
$$

上位件数は $\max(\lceil N \times K \rceil, 1)$。

| メトリクス | 合格基準 |
| --- | --- |
| `capture_rate_0_1pct` | $\ge 0.75$（マトリクス。運用下限 0.70 は第5.2節の参考） |
| `capture_rate_0_5pct` | $\ge 0.90$ |

### 5.3 Cost-Sensitive Expected Loss

$$
L_{\text{total}} = C_{\text{FN}} \sum_{i \in \text{FN}} \text{Amount}_i + C_{\text{FP}} \cdot \text{Count}(\text{FP}) + C_{\text{TP}} \cdot \text{Count}(\text{TP})
$$

本パイプラインのコスト係数:

| 記号 | 値 | 意味 |
| --- | --- | --- |
| $C_{\text{FN}}$ | 当該取引の `Amount` + 15 | 見逃し（補填 + チャージバック手数料） |
| $C_{\text{FP}}$ | 5 | 誤遮断（審査人件費・離脱） |
| $C_{\text{TP}}$ | 0.1 | 遮断成功時の通知費用 |

閾値 $P$ を 0.01 刻みで 0.01〜0.99 まで走査し、$L_{\text{total}}$ を最小にする $P^*$ を `optimal_threshold` とする。曲線は `ulb_fraud_detection_cost_curve`。判定は「合計最小化」であり固定閾値の PASS/FAIL は持たない（rating は「最小化済み」）。

---

## 6. ④ 安定性・経時変化 — PSI

学習データ `ulb_fraud_detection_train` の予測確率をベースライン、日次推論スコアを比較対象とする。ビンは確率 $[0,1]$ の均等幅 10 本。空ビンは割合 0.0001 に置換して対数を定義する。

$$
PSI = \sum_{b=1}^{10} \left( P_{\text{daily}, b} - P_{\text{baseline}, b} \right) \times \ln \left( \frac{P_{\text{daily}, b}}{P_{\text{baseline}, b}} \right)
$$

| 判定 (`psi_rating`) | 基準 | 運用 |
| --- | --- | --- |
| Green | $PSI < 0.10$ | 通常運用 |
| Yellow | $0.10 \le PSI < 0.25$ | CSI（特徴量ドリフト）調査 |
| Red | $PSI \ge 0.25$ | 緊急再学習・エスカレーション |

再学習トリガー（SOP 第8章）: 日次 $PSI \ge 0.25$、または月間 $PR\text{-}AUC < 0.80$。

---

## 7. Python REPL による特徴量検証

調査エージェント向けの算出ロジックは `evaluate/evaluate_feature.py` に置く。Zスコア差、KS、Cliff's Delta、歪度・尖度、IV/WoE、SOP 判定フラグを返す。大規模データでは Cliff's Delta を SOP どおり最大 500（陽性）/ 1000（陰性）にサンプルする。

---

## 8. パイプライン上の注意

1. `ML.GLOBAL_EXPLAIN` はモデル OPTIONS の `ENABLE_GLOBAL_EXPLAIN = TRUE` が必要である。本変更を適用したあと、初回は `initial_setup` を実行してモデルを再作成する。
2. 特徴量乖離指標は当日バッチ（1 疑似日）で算出するため、陽性が極端に少ない日は IV / KS が不安定になる。その場合は rating と併せて `n_pos`（`ulb_fraud_detection_imbalance_metrics`）を確認する。
3. PSI のベースラインは学習テーブルを直接参照する（Dataform `ref` には載せない）。学習テーブルが無い環境では当該アクションは失敗する。
4. 既存の `ulb_fraud_detection_evaluation` は後方互換のため残し、`evaluation_date` / `evaluated_at` のみ追加している。
5. 詳細テーブル（節 2.2）は CREATE OR REPLACE のため当日 1 日分のみ残る。過去分は置換前に `v_detection_*` へ退避する（節 2.3）。バックアップ sqlx はソースを `ref()` せず、当日テーブル側がバックアップ完了に依存する。
6. バックアップ sqlx を追加したあとは、Dataform ワークスペースでリモートから pull し、再コンパイルしてから `daily_batch` を実行する。スキーマを変えた当日テーブルがある場合は、対応する `v_detection_*` を DROP してから再実行する。
