# 単体試験書

| 項目 | 内容 |
| --- | --- |
| 文書名 | credit_detect 単体試験書 |
| 対象ブランチ | `main`（試験資産の保管ブランチは `test/v111`） |
| 対象システム | クレジットカード不正監視データパイプライン |
| 試験種別 | 単体試験（GCP 実行なし。モックおよびリポジトリ静的検査） |
| 根拠 | [pr_conversation.md](pr_conversation.md)、[EVALUATE.md](../EVALUATE.md) |
| 実行スクリプト | `test/run_unit_tests.sh` |

## 1. 目的

Google Cloud へ `main` をデプロイする前に、Cloud Run Job・評価カーネル・Dataform SQL・Workflows・Terraform・Dockerfile の論理欠陥を、実 GCP を呼ばずに検出する。

## 2. 試験環境

| 項目 | 値 |
| --- | --- |
| OS | Linux |
| 言語 | Python 3.10 以上（開発環境は 3.12 可） |
| 依存 | `test/requirements-unit.txt`（ランナーが `test/.venv` へ入れる） |
| venv | Debian/Ubuntu 24.04 以降は PEP 668 のためシステム `pip` は使わない。`python3-venv` が必要 |
| GCP / ADC | 不要 |
| ネットワーク | 初回のみ pip で依存取得。公開データセットへの実クエリは行わない |

## 3. 合否判定

- 本試験書の全項目を `test/unit/` および既存モジュール隣接テストがカバーし、`./test/run_unit_tests.sh` が終了コード 0 であること。
- 1 件でも FAIL なら単体試験不合格。デプロイに進まない。

## 4. 試験項目

### 4.1 Cloud Run Job 取り込み（`batch_app/daily_insert.py`）

| ID | 試験項目 | 手順 | 期待結果 | スクリプト |
| --- | --- | --- | --- | --- |
| UT-ING-01 | TARGET_DATE 環境変数の整数上書き | `TARGET_DATE=12` で `resolve_target_date()` | 戻り値 12 | `unit/test_daily_insert.py` |
| UT-ING-02 | 範囲外日付の拒否 | `TARGET_DATE=99` | `ValueError` | 同上 |
| UT-ING-03 | 非整数日付の拒否 | `TARGET_DATE=today` | `ValueError` | 同上 |
| UT-ING-04 | 疑似日 1〜50 の境界 | `1` は許可、`0` と `51` は拒否 | 境界どおり | 同上 |
| UT-ING-05 | 空文字 TARGET_DATE は JST 暦日 | `TARGET_DATE=""` をモック日時と組み合わせ | `datetime.now(JST).day` | 同上 |
| UT-ING-06 | 宛先テーブル ID の正当な形式 | `proj.dataset.table` | 同一文字列を返す | 同上 |
| UT-ING-07 | SQL 断片の拒否 | `proj.dwh.table; DROP TABLE dwh.t` | `ValueError` | 同上 |
| UT-ING-08 | 構造化ログの severity | ERROR レコードを JSON 化 | `severity=ERROR`、追加フィールドが載る | 同上 |
| UT-ING-09 | 空スライスで TRUNCATE しない | `to_dataframe` が空 DF | `RuntimeError`、`load_table_from_dataframe` 未呼出 | 同上 |
| UT-ING-10 | Query は US + retry + timeout | `run_ingestion` の `client.query` 引数 | `location=US`、`retry=API_RETRY`、`timeout=BQ_API_TIMEOUT_SECONDS` | 同上 |
| UT-ING-11 | Job 完了待ちは 480s | `query_job.result` | `timeout=BQ_JOB_TIMEOUT_SECONDS` かつ retry 付き | 同上 |
| UT-ING-12 | Storage Read API を使わない | `to_dataframe` | `create_bqstorage_client=False` | 同上 |
| UT-ING-13 | Load は公式の num_retries | `load_table_from_dataframe` | `num_retries=BQ_LOAD_NUM_RETRIES`、`retry=` なし、`result` 待ちあり | 同上 |
| UT-ING-14 | BigQuery クライアント再利用 | `get_bq_client` を 2 回 | コンストラクタ 1 回、同一インスタンス | 同上 |
| UT-ING-15 | PROJECT_ID 必須 | 環境変数なしで `run_ingestion` | `RuntimeError` | 同上 |
| UT-ING-16 | DESTINATION_TABLE 必須 | テーブル未設定 | `RuntimeError` | 同上 |
| UT-ING-17 | 抽出 SQL のタイブレーク | ソースのクエリ文字列 | `ORDER BY Time, Amount, V1` と `PSEUDO_DAY_COUNT=50` | `unit/test_static_pipeline.py` |
| UT-ING-18 | WRITE_TRUNCATE の指定 | `load_daily_slice` の JobConfig | `WRITE_TRUNCATE` | `unit/test_daily_insert.py` |
| UT-ING-19 | 公開表コピーは US Query | `copy_public_source.py` | 抽出 `location=US`、宛先 `dwh_prod.ulb_fraud_detection_public` | `unit/test_copy_public_source.py` |

### 4.2 SOP 評価カーネル（`evaluate/evaluate_feature.py`）

| ID | 試験項目 | 手順 | 期待結果 | スクリプト |
| --- | --- | --- | --- | --- |
| UT-SOP-01 | 同一分布は分離能不足 | 同じ正規乱数を両群に渡す | KS&lt;0.20、\|δ\|&lt;0.147、`is_strong_separator=False` | `unit/test_evaluate_feature.py` |
| UT-SOP-02 | 完全分離 | 0 と 4 に分けた群 | KS≥0.99、δ Large、卓越、IV≥0.30、強分離フラグ | 同上 |
| UT-SOP-03 | KS が [0,1] | 小さな固定配列 | 0&lt;KS≤1 | 同上 |
| UT-SOP-04 | Cliff's Delta が度数法と一致 | 固定配列で全対比較とヒストグラム法 | 12 桁一致 | 同上 |
| UT-SOP-05 | Cliff's Delta の符号 | 不正側が大きい / 入れ替え | 正 / 負 | 同上 |
| UT-SOP-06 | 無情報時の IV | 同一分布の分割 | IV&lt;0.30 | 同上 |
| UT-SOP-07 | SOP 判定閾値 | 境界値を `rate_*` に渡す | KS 0.50 卓越 / 0.40 優秀 / 0.20 許容 / 0.19 分離能不足。IV 0.50 過学習疑い。PR-AUC 0.80 PASS。Capture 0.75 PASS。PSI 0.09 Green / 0.10 Yellow / 0.25 Red | 同上 |
| UT-SOP-08 | 同一スコアの PSI | 0〜1 の同一系列 | Green かつ PSI&lt;0.10 | 同上 |
| UT-SOP-09 | Cliff's Delta サンプル上限 | 長さ 600/1200 の配列 | 例外なく有限値（SOP 500/1000 間引き） | 同上 |
| UT-SOP-10 | NaN/Inf の除去 | `_as_1d` 経由の総合評価 | 有限値のみで計算され落ちない | 同上 |

### 4.3 Dataform SQL / JS（静的）

| ID | 試験項目 | 手順 | 期待結果 | スクリプト |
| --- | --- | --- | --- | --- |
| UT-DF-01 | 無効演算子 `=<` が残っていない | `*.sqlx` を走査 | `Date =<` なし | `unit/test_static_pipeline.py` |
| UT-DF-02 | 推論・評価の日付範囲 | `daily_prediction.sqlx` / `daily_evaluation.sqlx` | `Date BETWEEN 1 AND 31` | 同上 |
| UT-DF-03 | 学習・検証の日付範囲 | `initial_train.sqlx` / `initial_validation.sqlx` | 37–50 / 32–36 | 同上 |
| UT-DF-04 | 日次 convert の別名 | `daily_convert.sqlx` の `name` | `ulb_fraud_detection_daily_converted`（初期の `converted` と衝突しない） | 同上 |
| UT-DF-05 | モデル ref の大小文字 | `initial_detection_model.sqlx` | `ref("ulb_fraud_detection_train")`（大文字 Train を使わない） | 同上 |
| UT-DF-06 | Time NULL 除外 | convert SQL | `WHERE Time IS NOT NULL` | 同上 |
| UT-DF-07 | Date タイブレーク | `initial_converted.sqlx` | `ORDER BY Time, Amount, V1` | 同上 |
| UT-DF-08 | 日次タグ | daily_batch 配下 | `tags: ["daily_batch"]` | 同上 |
| UT-DF-09 | 初期タグ | initial_setup 配下 | `tags: ["initial_setup"]` | 同上 |
| UT-DF-10 | GLOBAL_EXPLAIN | モデル OPTIONS | `ENABLE_GLOBAL_EXPLAIN = TRUE` | 同上 |
| UT-DF-11 | 不均衡対策 | モデル OPTIONS | `AUTO_CLASS_WEIGHTS = TRUE` | 同上 |
| UT-DF-12 | 監査変数 | `includes/features.js` | `V14`, `V17`, `V12` | 同上 |
| UT-DF-13 | Batch は declaration | `ulb_fraud_detection_Batch.sqlx` | `type: "declaration"` | 同上 |
| UT-DF-14 | プロジェクト設定 | `workflow_settings.yaml` | `defaultProject=skir-sample-credit`（アンダースコア無し）、`defaultDataset=dwh_prod`、`defaultLocation=asia-northeast1` | 同上 |
| UT-DF-15 | Dataform core | `package.json` | `@dataform/core` がピンされている | 同上 |
| UT-DF-16 | 旧設定ファイル不在 | `dataform.json` | Dataform core 3.0 では `workflow_settings.yaml` と併用できないためファイルが無い | 同上 |
| UT-DF-17 | 公開データのローカルコピー | `ulb_fraud_detection_public.sqlx` | `type: "declaration"`。`initial_converted` は `bigquery-public-data` を直接参照しない | 同上 |
| UT-DF-18 | Looker 評価テーブルの履歴 | 評価 sqlx 11 本 | `type: "incremental"`、`partitionBy: evaluation_date`、`protected: true`、当日 DELETE | 同上 |
| UT-DF-19 | 履歴ヘルパー | `includes/eval_history.js` | JST の当日だけ DELETE。`reload_days` で過去日を消さない | 同上 |
| UT-DF-20 | 局所 SHAP の大域結合 | `daily_local_explain.sqlx` | `global_explain` を当日 `evaluation_date` に限定 | 同上 |
| UT-DF-21 | 評価マトリクスの日付結合 | `daily_evaluation_matrix.sqlx` | `ML.EVALUATE` 行を `evaluation_date` で JOIN。CROSS JOIN しない | 同上 |

### 4.4 Workflows / Terraform / Dockerfile（静的）

| ID | 試験項目 | 手順 | 期待結果 | スクリプト |
| --- | --- | --- | --- | --- |
| UT-TF-01 | templatefile 用エスケープ | `workflow.yaml` | Workflows 式が `$${...}`。`${compilation_result` の生埋め込みなし | `unit/test_static_pipeline.py` |
| UT-TF-02 | Job の location | `run_ingestion_job` | `location: ${region}` | 同上 |
| UT-TF-03 | Job 完了ポーリング | `wait_for_job` / `get_job_status` | `completionTime` と失敗時 raise、最大 60 poll。`executions.get` は `namespaces/{namespace}/executions/{name}`（短い ID だけだと 404） | 同上 |
| UT-TF-04 | Dataform 非同期待ち | compile / invoke | `http.post`/`http.get` で Dataform v1 を呼び、`SUCCEEDED` まで待機。FAILED で raise | 同上 |
| UT-TF-05 | 日次タグのみ | `execute_dataform` | `includedTags: daily_batch`。`initial_setup` を日次で回さない | 同上 |
| UT-TF-06 | gitCommitish | compile body | `"main"` | 同上 |
| UT-TF-07 | 一時障害リトライ | GCP API ステップ | `http.default_retry_predicate`（Workflows 組み込み。`retry.transient_errors` は未定義） | 同上 |
| UT-TF-08 | Dataform リポジトリ定義 | `main.tf` | `google_dataform_repository` が存在する | 同上 |
| UT-TF-08a | Dataform Git URL | `main.tf` / `example.tfvars` | 既定は `credit_detect_dataform`。`credit_detect.git` は validation で拒否 | 同上 |
| UT-TF-08b | Git を常に接続 | `google_dataform_repository` | `git_remote_settings` を常に宣言。既定ブランチ `main`、トークンは `projects/<project_number>/secrets/dataform-github-token/versions/latest`。`ignore_changes` なし | 同上 |
| UT-TF-08c | PAT シークレット IAM | `main.tf` | Dataform SA へ `secretAccessor` を常に付与。`secret_id` は secret version 名を `split` した文字列 | 同上 |
| UT-TF-09 | Run Jobs IAM | `main.tf` | dataset `dataEditor` + project `jobUser` | 同上 |
| UT-TF-10 | Scheduler SA 分離 | `main.tf` | `sa-scheduler-trigger` と `workflows.invoker` | 同上 |
| UT-TF-11 | Job ポーリング権限 | `main.tf` | Job に `roles/run.developer` | 同上 |
| UT-TF-12 | Dataform SA の BQ 権限 | `main.tf` | サービス ID + dataEditor + jobUser + dataViewer | 同上 |
| UT-TF-13 | Cloud Run timeout / retry | `google_cloud_run_v2_job` | timeout 600s、max_retries 3、memory 1Gi | 同上 |
| UT-TF-14 | 非 root コンテナ | Dockerfile | `USER appuser` かつ uid 1001 | 同上 |
| UT-TF-15 | シークレット非埋め込み | アプリソース走査 | サービスアカウントキー JSON や PEM 秘密鍵ヘッダなし | 同上 |
| UT-TF-16 | gitignore | `.gitignore` | `*.tfstate` と `*.tfvars`（example は例外） | 同上 |

## 5. 実施手順

```bash
# test/ 配下からでも、リポジトリルートからでも可
./run_unit_tests.sh
# または
./test/run_unit_tests.sh
```

- 未指定時は `test/.venv` を自動作成し、その Python で `pip install` と試験を実行する。
- 既存の仮想環境を使う場合: `PYTHON=/path/to/venv/bin/python ./run_unit_tests.sh`
- `python3 -m venv` が失敗したら `sudo apt install python3-venv python3.12-venv python3-full`

結果ファイル: `test/results/unit_latest.txt`

## 6. トレーサビリティ

| Conversation 指摘 | 試験 ID |
| --- | --- |
| `Date =< 31` | UT-DF-01, UT-DF-02 |
| Duplicate action 名 | UT-DF-04 |
| templatefile の `${}` | UT-TF-01 |
| 非同期 Job / Dataform | UT-TF-03, UT-TF-04 |
| location 欠落 | UT-TF-02 |
| Dataform リソース未定義 | UT-TF-08 |
| 空スライス TRUNCATE | UT-ING-09 |
| TARGET_DATE 非整数 | UT-ING-03 |
| ROW_NUMBER タイブレーク | UT-ING-17, UT-DF-07 |
| IAM / 非 root / gitignore | UT-TF-09〜16 |
| retry / timeout / JSON ログ | UT-ING-08〜14 |
| SOP 閾値 | UT-SOP-07 |
