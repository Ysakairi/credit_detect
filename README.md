# credit_detect
# クレジットカード不正監視データパイプライン構築

# 1. プロジェクト概要

公開されているクレジットカード取引データセットを活用し、日次のデータ取り込みからデータ加工、機械学習モデルによる不正検知、そしてダッシュボードでの可視化までを一貫して行う自動化パイプラインを構築します。
実運用を想定し、インフラストラクチャはすべてTerraformを用いてコード化（IaC）しています。

# 2. アーキテクチャ

![credit_check.png](readme/credit_check.png)

## 構成図 (システムフロー)

1. **Cloud Scheduler** (日次トリガー)
↓
2. **Cloud Run Jobs** (擬似データをBigQueryにインサート)
↓
3. **Workflows** (後続の処理をオーケストレーション)
↓
4. **Dataform** (ETL処理・特徴量エンジニアリング) + **GitHub**連携
↓
5. **BigQuery ML** (機械学習モデルの再学習・推論)
↓
6. **Looker Studio** (ダッシュボード可視化)

## 各コンポーネントの役割と工夫点

- **疑似ストリーミング/バッチ処理 (Cloud Run Jobs)**
元の公開データは2日分のみの静的データですが、実運用に近づけるため、Cloud Run Jobsを用いて元データから擬似的に日次でデータを抽出し、分析用のBigQueryテーブルに追記する仕組みを実装します。これにより、日次バッチが「意味のあるデータ更新」として機能します。
- **Dataformによる特徴量エンジニアリング**
単なるデータコピーではなく、機械学習の精度を上げるためのデータ加工（取引金額の標準化、時間帯（秒）データのカテゴリ化、Null処理など）をDataform上のSQL（.sqlx）として実装し、GitHubでバージョン管理します。
- **Workflowsによる制御**
データ投入から、Dataformの実行、BQMLモデルの更新までの一連の流れを安全に連携させます。

# 3. 使用するデータセットと機械学習モデル

## 使用データセット

- **対象データ:** `bigquery-public-data.ml_datasets.ulb_fraud_detection`
- **ターゲット変数:** `Class` (1: 不正利用、 0: 正常利用)

## データ分割戦略

- 疑似的にインサートされたデータを元に、直近N日分を「学習・検証用」、最新のバッチ取り込み分を「予測・評価用」としてパイプライン内で自動分割します。

## 機械学習モデル

- **アルゴリズム:** 勾配ブースティング木 (`boosted_tree_classifier`)
- 極端な不均衡データ（正常取引が圧倒的多数）であるため、決定木ベースのアンサンブル学習を採用し、パラメータチューニングによる精度向上を図ります。

# 4. ダッシュボード可視化項目 (Looker Studio)

ビジネス層が日々の状況を把握し、かつモデルの精度をモニタリングできるダッシュボードを構築します。

### **A. 運用指標 (最新バッチ結果)**

- クレジットカード総取引数
- 検知された不正利用数
- 不正利用の割合（％）

### **B. モデル評価指標 (不均衡データに特化)**

- **PR-AUC (適合率-再現率曲線の下部面積):** 不正検知において最も重視すべき指標。
- **F1スコア:** 適合率と再現率の調和平均。
- **再現率 (Recall):** 実際の不正のうち、どれだけ検知できたか（見逃しを防ぐ）。
- **適合率 (Precision):** 不正と予測したうち、実際に不正だった割合（誤報を防ぐ）。
- **ROC曲線 / AUC**

### **C. 追加評価指標 (SOP-MLOPS-2026-002)**

算出定義・判定基準・Looker Studio 用テーブルは [EVALUATE.md](EVALUATE.md) を参照。

**① 分布乖離・判別力 (Distribution Separation)**

- KS統計量 (Kolmogorov-Smirnov)
- IV (Information Value) / WoE
- Cliff's Delta (δ)
- 陽性・陰性 Zスコア差 (ΔZ)（参考値）

**② BQML 固有説明性 (Model Explainability)**

- `ML.FEATURE_IMPORTANCE` (Gain)
- `ML.FEATURE_IMPORTANCE` (Cover)
- `ML.GLOBAL_EXPLAIN` (Tree SHAP)
- `ML.EXPLAIN_PREDICT` (Local SHAP)

**③ 予測性能・不均衡適応 (Predictive Performance)**

- PR-AUC (Precision-Recall AUC)
- Top-K% Capture Rate (Recall)
- Cost-Sensitive Expected Loss

**④ 安定性・経時変化 (Model & Data Stability)**

- PSI (Population Stability Index)

# 5. インフラストラクチャとデプロイメント (IaC)

実務レベルのクラウド設計を証明するため、コンソールからの手動作成を廃し、以下のリソースをすべて **Terraform** で定義します。

- **BigQuery**
    
    データセット、テーブルスキーマ
    
- **Cloud Scheduler & Workflows**
    
     ジョブ定義とスケジューリング
    
- **Cloud Run Jobs**
    
    コンテナ実行環境の定義
    
- **Dataform**
    
    リポジトリ連携設定
    
- **IAM / サービスアカウント**
    
     最小特権の原則に基づき、各サービス（Run Jobs, Workflows, Dataform等）が連携するために必要な権限のみを付与したサービスアカウント。
    

リポジトリには `terraform apply` と初期データ投入を一括で行うシェルスクリプトを同梱し、第三者が容易に環境を再現できるようにします。

# 6. 注意事項 (データセット分割による擬似運用について)

本プロジェクトで使用する公開データセット（`ulb_fraud_detection`）は、元々「2日間（48時間）の取引データ」しか含まれていない静的なデータセットです。これを日次運用される実際のデータパイプラインとして機能させるため、本プロジェクトでは意図的に以下の工夫を行っています。

### **レコードスライスによるデータ分割**

元データを50分割し、「50日分のバッチデータ」と見立てて抽出し、50日分の擬似的な日次データとして処理します。

# 7. 事前準備

**① clone → ② 単体テスト → ③ Artifact Registry / イメージ → ④ tfvars → ⑤ terraform apply → ⑥ Dataform の Git 接続 → ⑦ `initial_setup` → ⑧ 結合試験**


## 事前に揃えるもの

| 項目 | 内容 |
| --- | --- |
| GCP プロジェクト | 課金有効。リージョンは `asia-northeast1` |
| 権限 | プロジェクト Owner、または Terraform が SA / IAM / BQ / Run / Workflows / Scheduler / Dataform / Secret Manager を作れること |
| WSL | `git`, `python3`, `python3-venv`, `gcloud`, `terraform`（1.5+） |
| GitHub | `credit_detect`（アプリ / IaC）と、Dataform 用の **ルート配置リポジトリ**（例: `credit_detect_dataform`） |
| PAT | Dataform が private リポジトリを読むなら `contents:read` |

Dataform は **リポジトリ直下の `definitions/` しかコンパイルしません。** この `credit_detect` の sqlx は `dataform/definitions/` にあるため、Git 連携先を本リポジトリの `main` にすると日次も初回も失敗します。連携先は sqlx をルートに置いた `credit_detect_dataform` 側にしてください。`dataform.json` / `workflow_settings.yaml` のプロジェクト ID も、実プロジェクト（既定は `skir_sample_credit`）と一致させる必要があります。

README にある「terraform apply と初期投入を一括するシェル」は **リポジトリに存在しません。** 手動で進めます。

---

## ① GCP・GitHub 連携済み WSL で clone

```bash
# GitHub
git clone https://github.com/Ysakairi/credit_detect.git
cd credit_detect
git checkout main
git pull origin main

# GCP（未ログインなら）
gcloud auth login
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID
gcloud config set compute/region asia-northeast1
gcloud config get-value project
```

`YOUR_PROJECT_ID` は以降すべて同じ値にします。`dataform.json` の `defaultDatabase` も同じ ID である必要があります。

---

## ② Ubuntu 上で単体テスト

GCP は呼びません。Python 3.10 相当を想定します。

**評価ロジック**

```bash
cd evaluate
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m unittest test_evaluate_feature.py -v
deactivate
cd ..
```

**Cloud Run Job 取り込み**

```bash
cd batch_app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m unittest test_daily_insert.py -v
deactivate
cd ..
```

どちらも OK になってからイメージを積んでください。失敗したまま ④ に進むと、壊れたコンテナが Artifact Registry に載ります。

---

## ③ Artifact Registry に Cloud Run Jobs 一式を登録

Terraform は **AR リポジトリを作りません。** Job が参照するイメージ名は次で固定です。

`asia-northeast1-docker.pkg.dev/YOUR_PROJECT_ID/my-repo/daily-ingest:latest`

コンソール:

1. **API とサービス** で `Artifact Registry API` と `Cloud Build API` を有効化
2. **Artifact Registry → リポジトリを作成**
   - 名前: `my-repo`（変えるなら ⑤ の `repo_docker` も変える）
   - 形式: Docker
   - リージョン: `asia-northeast1`

プッシュは WSL または Cloud Shell から行います。Dockerfile は **`batch_app/`** にあります。リポジトリ直下で `gcloud builds submit` すると失敗します。

```bash
gcloud services enable artifactregistry.googleapis.com cloudbuild.googleapis.com

# コンソールで未作成なら
gcloud artifacts repositories create my-repo \
  --repository-format=docker \
  --location=asia-northeast1 \
  --description="Repository for fraud detection pipeline"

cd batch_app
gcloud builds submit --tag asia-northeast1-docker.pkg.dev/YOUR_PROJECT_ID/my-repo/daily-ingest:latest
cd ..
```

確認:

```bash
gcloud artifacts docker images list \
  asia-northeast1-docker.pkg.dev/YOUR_PROJECT_ID/my-repo \
  --include-tags
```

`daily-ingest:latest` が見えること。同一プロジェクトなら Cloud Run の pull 権限は通常自動付与されます。

---

## ④ プロジェクト情報を登録する（`main.tf` は直接書き換えない）

`project_id` は必須変数で、値は **`terraform.tfvars` に書きます。** `main.tf` の `variable` ブロックを編集する必要はありません。`*.tfvars` は gitignore 済みです。

```bash
cd terraform
cp example.tfvars terraform.tfvars
```

`terraform.tfvars` 例:

```hcl
project_id = "YOUR_PROJECT_ID"
region     = "asia-northeast1"
repo_docker = "my-repo"

# Dataform を Terraform で Git 接続する場合（⑥と一体でやるなら）
# dataform_git_url             = "https://github.com/YOUR_ORG/credit_detect_dataform.git"
# dataform_github_token_secret = "projects/YOUR_PROJECT_ID/secrets/dataform-github-token/versions/latest"
```

`dataform_github_token_secret` を空のまま apply すると、Dataform リポジトリは **Git 未接続** で作られます。その場合は apply 後にコンソールで接続します。

---

## ⑤ Terraform

`terraform/` で:

```bash
cd terraform
terraform init
terraform plan -var-file=terraform.tfvars
terraform apply -var-file=terraform.tfvars
```

作成される主なもの:

- API: BigQuery, Run, Workflows, Scheduler, Dataform, IAM, Secret Manager  
  （AR / Cloud Build は含まれない → ④が先）
- SA: `sa-run-jobs-executor`, `sa-workflows-orchestrator`, `sa-scheduler-trigger`
- BQ データセット `dwh_prod`
- Cloud Run Job `daily-ingest-job`
- Dataform リポジトリ `fraud-pipeline-repo`
- Workflows `fraud-detection-pipeline`
- Scheduler `daily-fraud-pipeline-trigger`（**毎日 02:00 JST**）

apply 直後から Scheduler が生きます。モデル未作成の 02:00 に走ると `daily_batch` の `ML.PREDICT` が失敗します。⑦が終わるまで Scheduler を一時停止するか、apply 直後に ⑦⑨ まで進めてください。

```bash
gcloud scheduler jobs pause daily-fraud-pipeline-trigger --location=asia-northeast1
```

確認例:

```bash
gcloud run jobs describe daily-ingest-job --region=asia-northeast1
gcloud workflows describe fraud-detection-pipeline --location=asia-northeast1
gcloud dataform repositories list --region=asia-northeast1
```

---

## ⑥ Dataform に GitHub を登録する

連携先は **sqlx がルートにあるリポジトリ**（`credit_detect_dataform`）です。`credit_detect` 本体ではありません。

**A. Terraform で接続する場合**

1. GitHub PAT を作り、Secret Manager へ

```bash
echo -n "ghp_...." | gcloud secrets create dataform-github-token --data-file=-
```

2. Dataform サービスエージェントに読み取りを付与（`main.tf` にはこの IAM が無い）

```bash
PROJECT_NUMBER=$(gcloud projects describe YOUR_PROJECT_ID --format='value(projectNumber)')
gcloud secrets add-iam-policy-binding dataform-github-token \
  --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-dataform.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

3. `terraform.tfvars` に URL と secret を書いて `terraform apply` し直す

**B. コンソールで接続する場合**

1. BigQuery → Dataform → `fraud-pipeline-repo`
2. Git を接続 → HTTPS URL（`credit_detect_dataform`）と PAT / Secret
3. デフォルトブランチ `main`

接続後、コンパイルが通り `definitions/` の sqlx が見えることを確認します。`Dataform doesn't compile .sqlx files outside the definitions/ folder` と出るなら、まだネストした `credit_detect` を向いています。

`dataform.json`:

```json
"defaultDatabase": "YOUR_PROJECT_ID",
"defaultSchema": "dwh_prod",
"defaultLocation": "asia-northeast1"
```

---

## ⑦ Dataform で初回用 sqlx（`initial_setup`）を実行

日次 Workflows は `daily_batch` だけです。モデル作成は初回の手動実行です。

タグ `initial_setup` の依存順:

1. `ulb_fraud_detection_converted` … 公開データ全件 + Hour + 疑似 Date 1–50  
2. `ulb_fraud_detection_validation` … Date 32–36  
3. `ulb_fraud_detection_train` … Date 37–50  
4. `ulb_fraud_detection_model` … `BOOSTED_TREE_CLASSIFIER`（`ENABLE_GLOBAL_EXPLAIN=TRUE`）

コンソール:

1. Dataform → `fraud-pipeline-repo` → `main` をコンパイル
2. 実行を開始 → タグ **`initial_setup`**（依存関係を含める）
3. `CREATE MODEL` 完了まで待つ（数分〜十数分、課金の主因）

この時点では Cloud Run の Batch テーブルは不要です。初期変換は公開データセットを直接読みます。

確認:

```bash
bq ls --location=asia-northeast1 YOUR_PROJECT_ID:dwh_prod
bq show -m YOUR_PROJECT_ID:dwh_prod.ulb_fraud_detection_model
```

モデルが無いと推論は失敗します。

---

## ⑧ 結合試験

モデル作成後、Scheduler を戻してから **パイプライン一式を 1 回手動実行** します。

```bash
gcloud scheduler jobs resume daily-fraud-pipeline-trigger --location=asia-northeast1

gcloud workflows run fraud-detection-pipeline --location=asia-northeast1
```

流れ: Cloud Run `daily-ingest-job` → 疑似日（JST の日、1–31）を Batch に WRITE_TRUNCATE → Dataform `daily_batch`。

見るもの:

| 確認 | 目安 |
| --- | --- |
| Cloud Run Job 実行 | 成功、行数おおよそ 5,700 |
| `dwh_prod.ulb_fraud_detection_Batch` | 当日スライスが入っている |
| Workflows 実行 | SUCCEEDED |
| Dataform invocation | `daily_batch` SUCCEEDED |
| `ulb_fraud_detection_predictions` | 推論行がある |
| `ulb_fraud_detection_evaluation_matrix` | PR-AUC / Capture / PSI など |
| `ulb_fraud_detection_feature_matrix` | KS / IV / Gain / SHAP |
| `ulb_fraud_detection_local_explain` | 高リスク件の Local SHAP |
| `ulb_fraud_detection_psi` | Green/Yellow/Red |

個別に切る場合:

```bash
gcloud run jobs execute daily-ingest-job --region=asia-northeast1 --wait
# その後 Dataform コンソールでタグ daily_batch を実行
```

失敗しやすい点:

- ⑦より先に Workflows を回す → モデルなしで `ML.PREDICT` 失敗
- Dataform が `credit_detect` 本体を向いている → sqlx がコンパイルされない
- イメージ未プッシュ → Job が Image not found
- `dataform.json` のプロジェクトが違う → 別プロジェクトに表が立つ / 権限エラー
- 空スライス（`TARGET_DATE` が 32–50 など）→ Job は TRUNCATE を拒否して失敗。Scheduler の既定は JST のカレンダー日なので通常 1–31

---

## 手順対応表（提示案 → 実施順）

| 提示 | 注意 |
| --- | --- |
| ① clone | `main` を取る |
| ② 単体テスト | `evaluate/` と `batch_app/` の 2 系統 |
| ③ AR 登録 | コンソールで repo 作成 + **`batch_app/` から** Cloud Build |
| ④ プロジェクト情報 | **`terraform.tfvars`。`main.tf` は触らない** |
| ⑤ terraform | Scheduler が即時有効 |
| ⑥ Dataform Git |接続先は `credit_detect_dataform`。Secret の IAM は手動 |
| ⑦ 初回 sqlx |タグ `initial_setup` |
| ⑧ 結合試験 | Workflows を 1 回手動実行 |
