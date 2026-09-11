# 試験一式（`test/`）

`main` ブランチの Google Cloud デプロイに向けた試験書・試験スクリプト・過去 PR Conversation の保管場所です。

| ファイル | 内容 |
| --- | --- |
| [UNIT_TEST.md](UNIT_TEST.md) | 単体試験書 |
| [INTEGRATION_TEST.md](INTEGRATION_TEST.md) | 結合試験書 |
| [pr_conversation.md](pr_conversation.md) | PR Conversation に記載していた試験要項・レビュー指摘の保管 |
| [deploy_gcp.sh](deploy_gcp.sh) | `main` 相当のパイプラインを GCP へデプロイする手順スクリプト |
| [run_unit_tests.sh](run_unit_tests.sh) | 単体試験の実行 |
| [run_integration_tests.sh](run_integration_tests.sh) | 結合試験の実行（GCP 認証が必要） |
| [unit/](unit/) | 単体試験スクリプト |
| [integration/](integration/) | 結合試験スクリプト |
| [results/](results/) | 試験結果の出力先（実行時に生成） |

## 実行方法

リポジトリルートから:

```bash
# 単体試験（GCP 不要）
./test/run_unit_tests.sh

# GCP デプロイ（要 gcloud / Terraform / 課金プロジェクト）
export GCP_PROJECT_ID="your-gcp-project-id"
./test/deploy_gcp.sh

# 結合試験（デプロイ後、ADC または GOOGLE_APPLICATION_CREDENTIALS）
export GCP_PROJECT_ID="your-gcp-project-id"
./test/run_integration_tests.sh
```

ブランチ方針: 本一式は `test/v111` に置く。`main` へのプルリクエストは作成しない。

## GCP デプロイについて

`test/deploy_gcp.sh` が Artifact Registry・Cloud Build・Terraform apply を一括実行する。

この作業環境には `gcloud` / Terraform / ADC が無く、プロジェクトへの認証情報も注入されていない。そのため **このエージェント実行中に `main` を GCP へ apply することはできない**。課金プロジェクトで次を実行する。

```bash
export GCP_PROJECT_ID="skir_sample_credit"   # dataform.json と一致させる
gcloud auth application-default login
./test/deploy_gcp.sh
./test/run_integration_tests.sh
```

初回は Dataform タグ `initial_setup` でモデルを作成してから日次 `daily_batch` を回す（Workflow は daily_batch のみ）。

