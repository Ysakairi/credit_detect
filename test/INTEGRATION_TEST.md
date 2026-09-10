# 結合試験書

| 項目 | 内容 |
| --- | --- |
| 文書名 | credit_detect 結合試験書 |
| 対象ブランチ | `main`（試験資産の保管ブランチは `test/v111`） |
| 対象システム | Scheduler → Workflows → Cloud Run Jobs → BigQuery → Dataform / BQML |
| 試験種別 | 結合試験（デプロイ済み GCP プロジェクトに対する API 検証） |
| 根拠 | [pr_conversation.md](pr_conversation.md)、[EVALUATE.md](../EVALUATE.md) |
| 前提 | `test/deploy_gcp.sh` によるデプロイ完了、または同等の Terraform + イメージ登録 |
| 実行スクリプト | `test/run_integration_tests.sh` |

## 1. 目的

単体試験で固定した契約が、Google Cloud 上でサービス横断に成立することを確認する。特に PR Conversation で「実行時に必ず失敗する」とされた非同期連携・クロスリージョン Load・IAM・Dataform タグ分離を実環境で検証する。

## 2. 試験環境

| 項目 | 値 |
| --- | --- |
| GCP プロジェクト | 環境変数 `GCP_PROJECT_ID`（Dataform の `defaultProject` と一致させる） |
| リージョン | `asia-northeast1`（`GCP_REGION` で上書き可） |
| 認証 | Application Default Credentials、または `GOOGLE_APPLICATION_CREDENTIALS` |
| 権限の目安 | BigQuery Job User、Cloud Run 参照、Workflows 参照、Dataform 参照、Service Usage 参照 |
| 公開データ | `bigquery-public-data.ml_datasets.ulb_fraud_detection`（US） |
| 宛先データセット | `dwh_prod`（asia-northeast1） |

認証が無い場合、スクリプトは試験項目を SKIP として終了コード 2 を返す（単体試験の失敗とは区別する）。

## 3. 合否判定

| 区分 | 判定 |
| --- | --- |
| 必須（IT-ENV / IT-IAM / IT-ING / IT-WF のコア） | すべて PASS |
| 条件付き（IT-DF-INIT, IT-ML） | `initial_setup` 未実施なら SKIP 可。日次推論を本番相当で回すなら必須 |
| SOP 指標（IT-SOP） | モデル学習後。PR-AUC は観測値を記録し、本番合否は EVALUATE.md の 0.80 |

## 4. 試験項目

### 4.1 環境・API・成果物の存在

| ID | 試験項目 | 手順 | 期待結果 | スクリプト |
| --- | --- | --- | --- | --- |
| IT-ENV-01 | 必須 API | Service Usage で有効化状態を取得 | bigquery / run / workflows / cloudscheduler / dataform / iam / secretmanager が ENABLED | `integration/test_gcp_pipeline.py` |
| IT-ENV-02 | Artifact Registry | リポジトリ `my-repo`（`GCP_AR_REPO`） | Docker リポジトリが asia-northeast1 に存在 | 同上 |
| IT-ENV-03 | コンテナイメージ | `daily-ingest:latest` | タグまたは digest が存在する | 同上 |
| IT-ENV-04 | BigQuery データセット | `dwh_prod` | location が `asia-northeast1` | 同上 |
| IT-ENV-05 | Cloud Run Job | `daily-ingest-job` | リージョン一致、timeout 600s、max_retries≥3、memory 1Gi | 同上 |
| IT-ENV-06 | Job 環境変数 | Job テンプレート | `PROJECT_ID` と `DESTINATION_TABLE=project.dwh_prod.ulb_fraud_detection_Batch` | 同上 |
| IT-ENV-07 | Workflows | `fraud-detection-pipeline` | 存在し SA が付いている | 同上 |
| IT-ENV-08 | Cloud Scheduler | `daily-fraud-pipeline-trigger` | `0 2 * * *`、`Asia/Tokyo` | 同上 |
| IT-ENV-09 | Dataform リポジトリ | `fraud-pipeline-repo` | リージョン内に存在 | 同上 |
| IT-ENV-10 | Dataform と TF のプロジェクト一致 | `dataform.json` の defaultDatabase と `GCP_PROJECT_ID` | 一致。不一致は FAIL（書き込み先ずれ） | 同上 |

### 4.2 IAM 結合

| ID | 試験項目 | 手順 | 期待結果 | スクリプト |
| --- | --- | --- | --- | --- |
| IT-IAM-01 | Run Jobs SA | `sa-run-jobs-executor` | 存在し、プロジェクト `bigquery.jobUser` 相当を持つ | 同上 |
| IT-IAM-02 | Workflows SA | `sa-workflows-orchestrator` | 存在 | 同上 |
| IT-IAM-03 | Scheduler SA | `sa-scheduler-trigger` | Workflows とは別アカウント | 同上 |
| IT-IAM-04 | Job 実行権限 | Job IAM | Workflows SA が Job を実行できる（developer） | 同上 |

### 4.3 取り込み結合（Cloud Run × BigQuery）

| ID | 試験項目 | 手順 | 期待結果 | スクリプト |
| --- | --- | --- | --- | --- |
| IT-ING-01 | 公開データ到達 | US ロケーションで件数クエリ | 約 28 万行規模で 1 件以上 | 同上 |
| IT-ING-02 | クロスリージョン制約 | 公開テーブルを `dwh_prod` へ destination 付き SELECT | 失敗する（同一リージョン制約）。実装が Query→Load であることの裏付け | 同上 |
| IT-ING-03 | スライス抽出 | `ROW_NUMBER` と同じ SQL で Date=@target_date | 0 件でない（1〜50 の指定日） | 同上 |
| IT-ING-04 | タイブレーク決定性 | 同じ SQL を 2 回 | 件数一致 | 同上 |
| IT-ING-05 | Job 手動実行 | `TARGET_DATE` を上書きして Job 実行（`RUN_INGEST_JOB=1`） | SUCCEEDED。Batch テーブルに行がある | 同上 |
| IT-ING-06 | べき等性 | 同じ TARGET_DATE で再実行 | 行数が増えず、TRUNCATE 置換 | 同上 |
| IT-ING-07 | Time NOT NULL | Batch または抽出結果 | Time が NULL の行がない | 同上 |

IT-ING-05/06 は Job を動かすため既定 OFF。`RUN_INGEST_JOB=1` のときのみ実施する。

### 4.4 Workflows 結合

| ID | 試験項目 | 手順 | 期待結果 | スクリプト |
| --- | --- | --- | --- | --- |
| IT-WF-01 | ソースに poll と location | Workflows 定義を取得 | `location`、`completionTime`、`daily_batch`、`gitCommitish` が main | 同上 |
| IT-WF-02 | 実行（任意） | `RUN_WORKFLOW=1` で実行し完了待ち | SUCCEEDED。ingest より先に Dataform が終わっていないこと（ログ順序） | 同上 |

### 4.5 Dataform / BQML 結合

| ID | 試験項目 | 手順 | 期待結果 | スクリプト |
| --- | --- | --- | --- | --- |
| IT-DF-01 | コンパイル（任意） | `RUN_DATAFORM_COMPILE=1` | compilation state = SUCCEEDED（Git 未接続なら SKIP） | 同上 |
| IT-DF-INIT | initial_setup（任意） | `RUN_INITIAL_SETUP=1` | train / validation / model が作成される | 同上 |
| IT-ML-01 | モデル存在 | `dwh_prod.ulb_fraud_detection_model` | モデルが存在する。無ければ SKIP（日次の前提不足） | 同上 |
| IT-ML-02 | 日次テーブル | Batch / daily_converted / predictions | テーブルが存在し、predictions は Date 1〜31 | 同上 |
| IT-ML-03 | 学習リーク | predictions の Date | 32 以上が無い | 同上 |
| IT-SOP-01 | evaluation テーブル | `ulb_fraud_detection_evaluation` | 行がある。evaluation_date が JST 日付 | 同上 |
| IT-SOP-02 | 評価マトリクス | `ulb_fraud_detection_evaluation_matrix` | category_id 1〜4 のメトリクスが揃う | 同上 |
| IT-SOP-03 | 不均衡指標 | `ulb_fraud_detection_imbalance_metrics` | `pr_auc` 列がある。値を記録 | 同上 |
| IT-SOP-04 | PSI | `ulb_fraud_detection_psi` | `psi_rating` が Green/Yellow/Red | 同上 |
| IT-SOP-05 | 監査 SHAP | `ulb_fraud_detection_global_explain` | V14/V17/V12 の `is_audit_variable` | 同上 |

## 5. 実施手順

```bash
export GCP_PROJECT_ID="your-gcp-project-id"
# 未デプロイなら先に:
# ./test/deploy_gcp.sh

./test/run_integration_tests.sh

# Job / Workflow / Dataform まで踏む場合（課金・実行時間に注意）
RUN_INGEST_JOB=1 RUN_WORKFLOW=1 ./test/run_integration_tests.sh
```

結果ファイル: `test/results/integration_latest.txt`

## 6. トレーサビリティ

| Conversation 指摘 | 試験 ID |
| --- | --- |
| クロスリージョンで destination query 不可 | IT-ING-02 |
| ingest 完了前に Dataform | IT-WF-01, IT-WF-02 |
| location 欠落 | IT-ENV-05, IT-WF-01 |
| jobUser 不足 | IT-IAM-01, IT-ING-01 |
| Scheduler と Workflows SA の混同 | IT-IAM-03, IT-ENV-08 |
| Dataform Git / gitCommitish main | IT-DF-01, IT-WF-01 |
| initial_setup が日次に含まれない | IT-WF-01, IT-DF-INIT, IT-ML-01 |
| 学習と推論の Date 分割 | IT-ML-03 |
| WRITE_TRUNCATE べき等 | IT-ING-06 |
| PR-AUC が ML.EVALUATE に出ない | IT-SOP-03 |
| プロジェクト ID 不一致 | IT-ENV-10 |
| 監査変数 V14/V17/V12 | IT-SOP-05 |
