# PR Conversation 保管（試験要項・レビュー指摘）

GitHub の PR Conversation に記載されていた試験要項・レビュー結果・運用注意を、`test/` 配下で参照できるように転記した文書である。

転記元（マージ済み / クローズ済み PR）。Issue コメントは空だったため、Conversation 相当の本文（PR 説明）を正本とする。

| PR | ブランチ | 状態 | タイトル |
| --- | --- | --- | --- |
| [#1](https://github.com/Ysakairi/credit_detect/pull/1) | `cursor/code-quality-fixes-34af` | CLOSED | コード品質レビュー指摘の修正（バグ・IAM・非同期待機） |
| [#2](https://github.com/Ysakairi/credit_detect/pull/2) | `review/v100` | MERGED | Fix pipeline correctness, IAM, and Dataform SQL bugs |
| [#3](https://github.com/Ysakairi/credit_detect/pull/3) | `evaluate/add-evaluation-matrix` | MERGED | Add SOP-MLOPS-2026-002 evaluation matrix for Looker Studio |
| [#4](https://github.com/Ysakairi/credit_detect/pull/4) | `review/v110` | MERGED | Harden Cloud Run ingest retries, timeouts, and structured logging |
| [#5](https://github.com/Ysakairi/credit_detect/pull/5) | `review/v111` | MERGED | Document GCP design intent in application comments |
| [#6](https://github.com/Ysakairi/credit_detect/pull/6) | `agent/poc` | OPEN | Add Autonomous Fraud Investigation Agent (PoC) ※main 対象外 |

単体・結合の試験項目への落とし込みは [UNIT_TEST.md](UNIT_TEST.md) / [INTEGRATION_TEST.md](INTEGRATION_TEST.md) を正とする。

---

## 1. PR #1 / #2 — パイプライン正しさ・IAM・非同期待機（試験要項の原典）

4観点で現行パイプラインを確認し、実行時に必ず失敗する欠陥と、セキュリティ／性能上の問題を修正した。以下は指摘と修正の対応表である。単体・結合試験はこの表を回帰対象とする。

### 1.1 バグ・エッジケース

| 重大度 | 指摘 | 修正 | 試験での確認 |
| --- | --- | --- | --- |
| 致命的 | `daily_prediction.sqlx` / `daily_evaluation.sqlx` の `Date =< 31` は BigQuery として無効（正しくは `<=`）。日次バッチが SQL エラーで落ちる | `Date BETWEEN 1 AND 31` に変更 | 単体: SQL 静的検査。結合: Dataform compile / `ML.PREDICT` |
| 致命的 | `initial_converted.sqlx` と `daily_convert.sqlx` が同じアクション名 `ulb_fraud_detection_converted`。Dataform コンパイルが Duplicate action で失敗し、成功しても学習用テーブルが日次データで上書きされる | 日次側を `ulb_fraud_detection_daily_converted` に改名 | 単体: アクション名の一意性。結合: 学習テーブルが日次で消えないこと |
| 致命的 | `terraform/workflow.yaml` を `templatefile()` で読むため、Workflows の `${compilation_result.name}` 等が Terraform 変数として評価され apply 時点で失敗 | Workflows 式を `$${...}` でエスケープ | 単体: `$${}` 残存検査。結合: `terraform validate` / apply |
| 致命的 | `googleapis.run.v1.namespaces.jobs.run` は非同期。ingest 完了前に Dataform が走り、空／古い Batch テーブルを読む | execution をポーリングし、`completionTime` と条件で成功判定。タイムアウトあり | 単体: workflow.yaml の poll 構造。結合: Job 完了後に Dataform が走ること |
| 致命的 | Dataform の compile / invoke も非同期。`create` の直後に次へ進む | compilation / invocation の state を SUCCEEDED まで待機 | 単体: workflow.yaml。結合: compilation/invocation SUCCEEDED |
| 致命的 | Cloud Run Jobs 呼び出しに `location` が無い（asia-northeast1 の Job を引けない） | `location` を付与 | 単体: `location: ${region}`。結合: Job 起動成功 |
| 致命的 | `google_dataform_repository` を参照しているがリソース未定義。`terraform apply` 不可 | Dataform リポジトリを追加（Git 連携は Secret 任意） | 単体: Terraform リソース定義。結合: リポジトリ存在 |
| 高 | 空データでも `return` して Job 成功。TRUNCATE 前に抜けるため古いデータで後続が「成功」する。`TARGET_DATE=abc` は `int()` で例外 | 範囲外・非整数は `ValueError`。0件は `RuntimeError`（TRUNCATE しない） | 単体: `run_ingestion` / `resolve_target_date` |
| 高 | Python の `index % 50` と SQL の `ROW_NUMBER()` が Time 同値行で食い違う | 両方 `ORDER BY Time, Amount, V1` に統一。抽出は BQ 側で実施 | 単体: クエリ文字列。結合: Date スライスの一致 |
| 中 | モデル SQL が `ulb_fraud_detection_Train`（大文字）を参照し、Dataform の `name` は小文字 | `${ref("ulb_fraud_detection_train")}` に変更 | 単体: ref 名。結合: CREATE MODEL |
| 中 | `PROJECT_ID` 未設定時に `"your-gcp-project-id"` へクエリしうる | 必須環境変数にして即失敗 | 単体: 環境変数欠落 |
| 中 | `raise e` でトレースバックが欠ける | `logger.exception` + bare `raise` | 単体: ソース検査 |
| 中 | `Time` NULL 時に `Hour` / `Date` が壊れる | `WHERE Time IS NOT NULL` | 単体: SQL 検査 |

**残課題（仕様確認が必要なため当時未修正。結合試験で観測する）**

- スケジューラは暦日 1–31 のみ投入。疑似日付 32–50 は学習専用で日次には乗らない（意図どおりなら OK）。2月は 29–31 が欠ける。
- Workflow は `daily_batch` のみ。`initial_setup`（モデル作成）は初回に別途実行が必要。未作成だと `ML.PREDICT` が失敗する。
- README の「20日目以降で評価」と実装（1–31）がもともと不一致だった。学習未使用スライス 1–31 に合わせた。

### 1.2 コード品質（試験で固定する事項）

- 未使用の `numpy` / `pandas` 直接 import を削除。`print` を `logging` に変更。
- Dataform の Batch テーブルを `declaration` 化し、`ref()` で依存を明示。
- 評価 SQL のコメント「20日目以降」は実装と矛盾していたため修正。
- `.gitignore` を追加（`.tfstate` / `.tfvars` / `__pycache__`）。
- `terraform/example.tfvars` を追加。
- README が「一括シェルスクリプト同梱」と書いているが当時リポジトリに存在しなかった → 本ディレクトリの `deploy_gcp.sh` で補完する。
- `readme/readme.md` が重複、Dataform Git 設定が「追記中」——ドキュメント負債。

### 1.3 セキュリティ

| 指摘 | 修正 | 試験での確認 |
| --- | --- | --- |
| Run Jobs SA が `roles/bigquery.dataEditor` のみで `jobs.create` がなく、公開データセットのクエリが失敗 | データセット限定 `dataEditor` + プロジェクト `jobUser` | 単体: Terraform IAM。結合: 公開データ Query 成功 |
| Scheduler が Workflows SA を流用し、`roles/workflows.invoker` が無い | Scheduler 専用 SA + `workflows.invoker` | 単体: SA 分離。結合: Scheduler ジョブ定義 |
| `roles/run.invoker` だけでは Job 起動と execution GET（ポーリング）に不足しうる | Job リソースに `roles/run.developer` | 単体: IAM。結合: Workflows からの Job 起動 |
| Dataform 実行 SA への BQ 権限が無い（CREATE MODEL / テーブル作成不可） | Dataform サービス ID を発行し dataset editor + jobUser | 単体: Terraform。結合: Dataform 実行 |
| コンテナが root 実行 | uid 1001 の非 root USER | 単体: Dockerfile |
| tfstate / tfvars がコミットされうる | `.gitignore` | 単体: gitignore 検査 |
| GitHub PAT の直書きは無し（良好）。Dataform Git 連携は Secret Manager の version 名を変数化 | `dataform_github_token_secret`（sensitive） | 単体: 変数定義 |

**残課題**

- `roles/dataform.editor` はリポジトリ改変まで含む。実行だけならカスタムロールの方が最小特権。
- `:latest` タグは再現性・サプライチェーン上弱い。digest 固定を推奨。
- `dataform.json` の `skir_sample_credit` は実プロジェクト ID。Terraform の `project_id` と不一致だと書き込み先がずれる。

### 1.4 パフォーマンス

- 毎日公開テーブル全件（約 28 万行）を pandas にダウンロードしてから 1/50 にフィルタしていた。メモリ（デフォルト 512Mi で OOM しうる）とスロット消費が過大。
- 修正: BQ 上で `ROW_NUMBER` → `WHERE Date = @target_date`（パラメータクエリ）。Cloud Run は 1Gi / timeout 600s。
- `ML.EVALUATE` は ROC-AUC / F1 等は返すが、README が重視する PR-AUC は出ない。`ML.PR_CURVE` 相当は `daily_imbalance_metrics.sqlx` で別テーブル化した。

### 1.5 運用上の注意（結合試験の前提）

1. `initial_setup` タグを一度実行してモデルを作る（日次 Workflow では回らない）。
2. Dataform を GitHub 連携するなら Secret Manager に PAT を置き、`dataform_github_token_secret` を渡す。未設定だと `gitCommitish: main` のコンパイルは失敗する。
3. `dataform.json` / `workflow_settings.yaml` の `defaultProject` を Terraform の `project_id` と一致させる。

---

## 2. PR #4 — Cloud Run ingest の回復性・タイムアウト・構造化ログ

Google Cloud Architecture Framework に基づきアプリケーションコード（主に Cloud Run Job `batch_app/daily_insert.py`）を評価し、回復性・タイムアウト・可観測性の不足を修正した。

公開データセット（US）から地域データセット（asia-northeast1）へのロードはクロスリージョンのため、Query → Load を維持する。同一リージョンの destination query にはできない。

### 修正内容

- BigQuery 呼び出しに Exponential Backoff（`google.api_core.retry`）と API / Job タイムアウト
- Cloud Logging 向け JSON 構造化ログ（`severity` フィールド）
- プロセスライフサイクルで BigQuery Client を再利用
- Workflows の GCP API 呼び出しに `retry.transient_errors`
- Cloud Run Job `max_retries` を 1 → 3
- 単体テスト `batch_app/test_daily_insert.py`

### レビュー指摘（評価観点）

#### 指摘事項 [重要度: High]

対象ファイル・関数: `batch_app/daily_insert.py` の `run_ingestion`

問題点: BigQuery `query` / `to_dataframe` / `load_table_from_dataframe` にリトライもタイムアウトも無く、一時的な 429/5xx やハングで Job が失敗または 600 秒 kill されていた。

根拠: Architecture Framework の Reliability は一時障害をクライアント側で backoff し、明示タイムアウトでフェイルファストすることを求める。

改善案: `API_RETRY` + `job.result(timeout=480)` + `job_timeout_ms` を追加。

#### 指摘事項 [重要度: Medium]

対象ファイル・関数: `batch_app/daily_insert.py` のモジュール初期化（旧 `logging.basicConfig`）

問題点: テキストログのため Cloud Logging で severity / jsonPayload として扱いにくい。

根拠: Cloud Run のベストプラクティスは stdout の JSON（`severity` キー）または Cloud Logging クライアント。

改善案: `CloudLoggingJsonFormatter` を追加。

#### 指摘事項 [重要度: Medium]

対象ファイル・関数: `terraform/workflow.yaml` の `run_ingestion_job` / `compile_dataform` / `execute_dataform`

問題点: Workflows からの GCP API にリトライポリシーが無かった。

根拠: オーケストレータも一時的な API エラーを吸収すべき。

改善案: `retry.transient_errors` と指数バックオフを追加。

#### 指摘事項 [重要度: Low]

対象ファイル・関数: `batch_app/daily_insert.py` の `run_ingestion` 内 `bigquery.Client(...)`

問題点: リクエスト（Job）ごとにクライアントを生成していた。

根拠: GCP クライアントはプロセス内で再利用するのが推奨。Job は単発だがテスト容易性と一貫性のためモジュールキャッシュに変更。

### セキュリティ（問題なし）

認証情報のハードコードなし。Cloud Run Job のサービスアカウント + ADC。`PROJECT_ID` / `DESTINATION_TABLE` は機密ではないため Secret Manager は不要。

### ステートレス性

ローカル FS への永続化なし。pandas メモリは US→asia-northeast1 のクロスリージョン制約による一時バッファであり、永続状態ではない。

---

## 3. PR #5 — GCP 設計意図コメント（実行時振る舞い変更なし）

実行時の振る舞いを変えず、GCP 連携の設計意図（Why）をコメントとして追記した。

対象は Cloud Run Job の Python、調査エージェント用評価モジュール、Dockerfile / Workflows / Terraform の関連箇所、および BQML を呼ぶ Dataform の一部。

コメントで明示した内容:

- 対象 GCP サービス（BigQuery / Cloud Run Jobs / Cloud Logging / Workflows / Dataform / BigQuery ML）
- サービスアカウントと ADC、環境変数
- Exponential Backoff とタイムアウト値の根拠（Cloud Run 600s との関係）
- 日次スライスで Storage Read API / 自前ページングが不要な理由
- WRITE_TRUNCATE によるべき等性
- US → asia-northeast1 のクロスリージョン制約
- Cliff's Delta のサンプル上限（メモリ）

検証: `batch_app/test_daily_insert.py` と `evaluate/test_evaluate_feature.py` はコメント追加後も成功している。

---

## 4. PR #3 — SOP-MLOPS-2026-002 評価マトリクス（評価試験の前提）

BQML `BOOSTED_TREE_CLASSIFIER` の評価指標の算出定義・判定基準・BigQuery 格納先を定義する。Looker Studio は当該テーブルをデータソースとする。

詳細な閾値はリポジトリルートの [EVALUATE.md](../EVALUATE.md) を正本とする。結合試験では次を確認する。

- `ulb_fraud_detection_evaluation_matrix`（モデル × メトリクス 1 行）
- `ulb_fraud_detection_feature_matrix`（特徴量 × メトリクス 1 行）
- PR-AUC ≥ 0.80 を本番デプロイの一次合否とする（ROC-AUC は参考値）
- PSI Green / Yellow / Red（0.10 / 0.25）
- 監査変数 V14 / V17 / V12 の Tree SHAP
- Workflow は `daily_batch` のみ。`ENABLE_GLOBAL_EXPLAIN = TRUE` のモデルは `initial_setup` で作る

---

## 5. デプロイ試験で守る運用制約（Conversation からの抽出）

1. 公開データ `ulb_fraud_detection` は US、宛先 `dwh_prod` は asia-northeast1。Query（US）→ Load（地域）に分解する。
2. Cloud Run Job timeout 600s。アプリ側 Job 待ちは 480s。`max_retries = 3`。WRITE_TRUNCATE なので再実行はべき等。
3. 空スライスでは Load しない（前日データを消さない）。
4. Storage Read API は使わない（`create_bqstorage_client=False`）。Job SA に `bigquery.readSessionUser` を付けていない。
5. Dataform 日次はタグ `daily_batch`。推論対象は疑似日付 1〜31（学習 37–50、検証 32–36 とのリーク防止）。
6. `gitCommitish: "main"`。デプロイ対象ブランチは main。
