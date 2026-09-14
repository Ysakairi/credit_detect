# credit_detect

クレジットカード不正監視データパイプラインと、その推論結果を調査する **Autonomous Fraud Investigation Agent（PoC）** です。

- パイプライン（守り）: Dataform × BQML × Cloud Workflows × Terraform
- エージェント（攻め）: LangGraph × Vertex AI × BigQuery Vector Search × Cloud Run
- エージェント仕様の詳細は **[Agent.md](Agent.md)**
- 評価指標の定義は **[EVALUATE.md](EVALUATE.md)**

# 1. プロジェクト概要

公開されているクレジットカード取引データセットを活用し、日次のデータ取り込みからデータ加工、機械学習モデルによる不正検知、そしてダッシュボードでの可視化までを一貫して行う自動化パイプラインを構築します。
実運用を想定し、インフラストラクチャはすべてTerraformを用いてコード化（IaC）しています。

その上に、日次推論で付いた異常スコアと自然言語の調査指示を入力に、SQL 生成・規程検索・統計・自己修正・是正 SQL 提案までを自律実行する Agent を PoC として載せます。

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
↓
7. **Fraud Investigation Agent**（本 PoC）LangGraph が推論テーブルと規程 RAG を使って調査レポートを返す

```mermaid
flowchart TD
  analyst[アナリスト] --> ui[Cloud Run Streamlit]
  ui --> agent[LangGraph Agent]
  agent --> gemini[Vertex AI Gemini Flash]
  agent --> bq[BigQuery dwh_prod]
  agent --> vs[VECTOR_SEARCH knowledge]
  bq --> pred[ulb_fraud_detection_predictions]
  bq --> model[ulb_fraud_detection_model]
  vs --> manuals[規程 / SOP / アップロード資料]
  ingest[日次パイプライン] --> pred
  ingest --> model
```

## 各コンポーネントの役割と工夫点

- **疑似ストリーミング/バッチ処理 (Cloud Run Jobs)**
元の公開データは2日分のみの静的データですが、実運用に近づけるため、Cloud Run Jobsを用いて元データから擬似的に日次でデータを抽出し、分析用のBigQueryテーブルに追記する仕組みを実装します。これにより、日次バッチが「意味のあるデータ更新」として機能します。
- **Dataformによる特徴量エンジニアリング**
単なるデータコピーではなく、機械学習の精度を上げるためのデータ加工（取引金額の標準化、時間帯（秒）データのカテゴリ化、Null処理など）をDataform上のSQL（.sqlx）として実装し、GitHubでバージョン管理します。
- **Workflowsによる制御**
データ投入から、Dataformの実行、BQMLモデルの更新までの一連の流れを安全に連携させます。
- **調査 Agent**
推論確率 `fraud_probability` を自然言語から Text-to-SQL し、SOP の KS / Cliff's Delta で特徴量を評価して監査レポートを出します。詳細は [Agent.md](Agent.md)。

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

- **Agent PoC（`terraform/agent.tf`）**
    
    Vertex AI API、ステージング GCS、ナレッジテーブル、調査用 SA、任意の Streamlit Cloud Run
    

リポジトリには `terraform apply` と初期データ投入を一括で行うシェルスクリプトを同梱し、第三者が容易に環境を再現できるようにします。

# 6. 注意事項 (データセット分割による擬似運用について)

本プロジェクトで使用する公開データセット（`ulb_fraud_detection`）は、元々「2日間（48時間）の取引データ」しか含まれていない静的なデータセットです。これを日次運用される実際のデータパイプラインとして機能させるため、本プロジェクトでは意図的に以下の工夫を行っています。

### **レコードスライスによるデータ分割**

元データを50分割し、「50日分のバッチデータ」と見立てて抽出し、50日分の擬似的な日次データとして処理します。

# 7. 事前準備（日次パイプライン）

**① clone → ② 単体テスト → ③ Artifact Registry / イメージ → ④ tfvars → ⑤ terraform apply → ⑥ Dataform の Git 接続 → ⑦ `initial_setup` → ⑧ 結合試験 → ⑨ 調査 Agent（第8節）**


## 事前に揃えるもの

| 項目 | 内容 |
| --- | --- |
| GCP プロジェクト | 課金有効。リージョンは `asia-northeast1` |
| 権限 | プロジェクト Owner、または Terraform が SA / IAM / BQ / Run / Workflows / Scheduler / Dataform / Secret Manager / Vertex AI を作れること |
| WSL | `git`, `python3`, `python3-venv`, `gcloud`, `terraform`（1.5+） |
| GitHub | `credit_detect`（アプリ / IaC）と、Dataform 用の **ルート配置リポジトリ**（例: `credit_detect_dataform`） |
| PAT | Dataform が GitHub HTTPS で読む／書くなら PAT。Secret Manager に入れ、Dataform SA へ `secretAccessor` を付ける |

Dataform は **リポジトリ直下の `definitions/` しかコンパイルしません。** この `credit_detect` の sqlx は `dataform/definitions/` にあるため、Git 連携先を本リポジトリの `main` にすると日次も初回も失敗します。連携先は sqlx をルートに置いた `credit_detect_dataform` 側にしてください。`workflow_settings.yaml` の `defaultProject` も、実プロジェクト（既定は `skir-sample-credit`）と一致させる必要があります。Dataform core 3.0 では `dataform.json` は廃止で、`workflow_settings.yaml` と同時に置くとコンパイルが失敗します。

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

`YOUR_PROJECT_ID` は以降すべて同じ値にします。`workflow_settings.yaml` の `defaultProject` も同じ ID である必要があります。

---

## ② Ubuntu 上で単体テスト

GCP は呼びません。Python 3.10 相当を想定します。Ubuntu / WSL では `python` は標準では入っておらず `python3` だけです。`python3 -m venv .venv` のあと `source .venv/bin/activate` すると、venv 内の `python` が使えます。venv 作成に失敗する場合は `sudo apt install python3-venv python3.12-venv python3-full`。

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

**調査 Agent（PoC）**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r agent/requirements.txt
export PYTHONPATH="$(pwd):$(pwd)/evaluate"
python -m unittest discover -s agent/tests -v
deactivate
```

GCP は呼びません（mock バックエンド）。LangGraph と既存の `evaluate/` カーネルを使います。

どれも OK になってからイメージを積んでください。失敗したまま ④ に進むと、壊れたコンテナが Artifact Registry に載ります。

---

## ③ Artifact Registry に Cloud Run Jobs 一式を登録

Terraform は **AR リポジトリを作りません。** Job が参照するイメージ名は次で固定です。

`asia-northeast1-docker.pkg.dev/YOUR_PROJECT_ID/my-repo/daily-ingest:latest`

`YOUR_PROJECT_ID` は ① で `gcloud config set project` した値です。プレースホルダのまま submit しないでください。

コンソール:

1. **API とサービス** で `Artifact Registry API` と `Cloud Build API` を有効化
2. **Artifact Registry → リポジトリを作成**
   - 名前: `my-repo`（変えるなら ⑤ の `repo_docker` も変える）
   - 形式: Docker
   - リージョン: `asia-northeast1`

プッシュは WSL または Cloud Shell から行います。Dockerfile は **`batch_app/`** にあります。リポジトリ直下で `gcloud builds submit` すると失敗します。

2024 年以降、新しいプロジェクトの Cloud Build はレガシーの `@cloudbuild.gserviceaccount.com` ではなく **Compute Engine デフォルト SA**（`PROJECT_NUMBER-compute@developer.gserviceaccount.com`）でビルドします。この SA にソース読み取り権限が無いと、アップロード直後に次で落ちます。

`...-compute@developer.gserviceaccount.com does not have storage.objects.get access to the Google Cloud Storage object`

```bash
PROJECT_ID="$(gcloud config get-value project)"
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

gcloud services enable artifactregistry.googleapis.com cloudbuild.googleapis.com

# Cloud Build 実行 SA（Compute デフォルト）へ、ソース取得・ログ・イメージ push に必要な権限
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${COMPUTE_SA}" \
  --role="roles/storage.objectViewer"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${COMPUTE_SA}" \
  --role="roles/logging.logWriter"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${COMPUTE_SA}" \
  --role="roles/artifactregistry.writer"

# コンソールで未作成なら
gcloud artifacts repositories create my-repo \
  --repository-format=docker \
  --location=asia-northeast1 \
  --description="Repository for fraud detection pipeline"

# IAM 反映待ち（直後の submit がまだ 403 なら数十秒置いて再実行）
cd batch_app
gcloud builds submit --tag "asia-northeast1-docker.pkg.dev/${PROJECT_ID}/my-repo/daily-ingest:latest"
cd ..
```

手順 ② で作った `.venv` が `batch_app/` に残っていると、ビルドコンテキストが数百 MB になります。submit 前に消すか、ディレクトリの外へ移してください。`.venv` はイメージに不要です。

確認:

```bash
PROJECT_ID="$(gcloud config get-value project)"
gcloud artifacts docker images list \
  "asia-northeast1-docker.pkg.dev/${PROJECT_ID}/my-repo" \
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

# Autonomous Fraud Investigation Agent (PoC)
enable_agent             = true
enable_agent_cloud_run   = false   # イメージ未 push のうちは false
agent_ui_unauthenticated = false

# Dataform を Terraform で Git 接続する場合（⑥と一体でやるなら）
# dataform_git_url             = "https://github.com/YOUR_ORG/credit_detect_dataform.git"
# dataform_github_token_secret = "projects/YOUR_PROJECT_ID/secrets/dataform-github-token/versions/latest"
```

`dataform_github_token_secret` を空のまま apply すると、Dataform リポジトリは **Git 未接続** で作られます。その場合は apply 後にコンソールで接続します。

---

## ⑤ Terraform

`google_project_service` は apply 時に Service Usage API へ `serviceusage.services.list` します。この API が未有効だと、権限不足に見える 403 になります。

```
Permission denied to list services for consumer container [projects/PROJECT_NUMBER]
permission: serviceusage.services.list
```

Terraform はこの一覧取得ができないと API を有効化できないため、**先に gcloud で土台 API を有効化**します（IaC だけでは初回を解けません）。Terraform は `gcloud` のユーザーログインではなく **ADC** を使います。

apply の前に `terraform/` の外で:

```bash
PROJECT_ID="$(gcloud config get-value project)"

# Terraform 用の ADC（① をまだなら）
gcloud auth application-default login
gcloud auth application-default set-quota-project "${PROJECT_ID}"

# List Project Services に必要。未有効だと上記 403 になる
gcloud services enable \
  serviceusage.googleapis.com \
  cloudresourcemanager.googleapis.com \
  dataform.googleapis.com

# Dataform の Google 管理 SA を実体化する。未作成だと apply が
# `service-PROJECT_NUMBER@gcp-sa-dataform.iam.gserviceaccount.com does not exist` で落ちる
# beta が無い場合: gcloud components install beta
gcloud beta services identity create \
  --service=dataform.googleapis.com \
  --project="${PROJECT_ID}"
```

`gcloud services enable` 自体が 403 なら、実行アカウントに Owner または Service Usage Admin がありません。プロジェクト Owner が付与します。

```bash
ACCOUNT="$(gcloud config get-value account)"
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="user:${ACCOUNT}" \
  --role="roles/serviceusage.serviceUsageAdmin"
```

確認:

```bash
gcloud services list --enabled --filter="config.name:(serviceusage.googleapis.com OR cloudresourcemanager.googleapis.com OR dataform.googleapis.com)"
```

その後 `terraform/` で:

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

HTTPS で Git 接続する場合、コンソールに PAT を直接貼る欄はありません。**Secret Manager のシークレットが必須**です。未作成だと「シークレットが必要」で進めません。SSH や Developer Connect は本手順では使いません。

### 1. GitHub PAT を発行する

1. GitHub → Settings → Developer settings → Personal access tokens
2. 次のいずれか

**Fine-grained token（推奨）**

- Repository access: Only select repositories → `credit_detect_dataform`
- Permissions → Repository permissions → **Contents: Read and write**（コンパイルの pull と workspace からの push）
- Expiration: 運用に合わせて設定

**Classic token**

- スコープ `repo`（private の場合）

SAML SSO を使っている組織なら、発行後に token を Authorize する。値（`ghp_...` または `github_pat_...`）は画面に一度しか出ません。リポジトリにコミットしない。

### 2. Secret Manager にシークレットを作る

名前は `dataform-github-token`。値は PAT の文字列だけ（JSON にしない、前後の改行を付けない）。

**コンソール**

1. セキュリティ → Secret Manager → **シークレットを作成**
2. 名前: `dataform-github-token`
3. シークレットの値: PAT を貼る
4. 作成

Secret Manager API が未有効なら、⑤ の apply 後か次で有効化する。

```bash
gcloud services enable secretmanager.googleapis.com
```

**gcloud**

```bash
echo -n "ghp_...." | gcloud secrets create dataform-github-token --data-file=-
```

既存シークレットに PAT を入れ直す場合:

```bash
echo -n "ghp_...." | gcloud secrets versions add dataform-github-token --data-file=-
```

### 3. Dataform サービスエージェントに読み取りを付与する

`main.tf` にはこの IAM がありません。シークレットを Dataform が読めないと、接続画面のドロップダウンに出てもリンク後に失敗します。

```bash
PROJECT_ID="$(gcloud config get-value project)"
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
gcloud secrets add-iam-policy-binding dataform-github-token \
  --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-dataform.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

**コンソール**なら Secret Manager → `dataform-github-token` → 権限 → アクセスを許可。プリンシパルに `service-PROJECT_NUMBER@gcp-sa-dataform.iam.gserviceaccount.com`、ロールは **Secret Manager シークレット アクセサー**。

### A. Terraform で接続する場合

`terraform.tfvars` に URL とシークレット版を書いて `terraform apply` し直す。

```hcl
dataform_git_url             = "https://github.com/Ysakairi/credit_detect_dataform.git"
dataform_github_token_secret = "projects/YOUR_PROJECT_ID/secrets/dataform-github-token/versions/latest"
```

URL にユーザー名や PAT を含めない。末尾は `.git`。

### B. コンソールで接続する場合

1. BigQuery → Dataform → `fraud-pipeline-repo`
2. **設定 → Git と接続**（Connect with Git）
3. プロトコル: **HTTPS**
4. リモート Git リポジトリ URL: `https://github.com/Ysakairi/credit_detect_dataform.git`  
   （ユーザー名・パスワードを URL に入れない。末尾 `.git`）
5. デフォルト ブランチ: `main`
6. **シークレット:** ドロップダウンで `dataform-github-token` を選ぶ（PAT の直貼りはできない）
7. リンク

ドロップダウンにシークレットが出ないときは、同一プロジェクトにシークレットがあることと、手順 3 の `secretAccessor` を確認する。

接続後、コンパイルが通り `definitions/` の sqlx が見えることを確認します。`Dataform doesn't compile .sqlx files outside the definitions/ folder` と出るなら、まだネストした `credit_detect` を向いています。

`workflow_settings.yaml`（`dataform.json` は置かない）:

```yaml
defaultProject: YOUR_PROJECT_ID
defaultDataset: dwh_prod
defaultLocation: asia-northeast1
```

---

## ⑦ Dataform で初回用 sqlx（`initial_setup`）を実行

日次 Workflows は `daily_batch` だけです。モデル作成は初回の手動実行です。コンソールから実行するには、先に **開発ワークスペースを作り、そこでコンパイル** します。リポジトリの `main` を直接コンパイルする欄はありません。

### 使う ID

| 項目 | 値 |
| --- | --- |
| プロジェクト ID | `gcloud config get-value project`（例: `skir-sample-credit`） |
| ロケーション | `asia-northeast1` |
| Dataform リポジトリ ID | `fraud-pipeline-repo` |
| ワークスペース ID | `initial-setup`（英数字・ハイフン・アンダースコアのみ。リポジトリ内で一意） |
| Git デフォルト ブランチ | `main` |
| 実行タグ | `initial_setup` |

ワークスペースのリソース名:

```
projects/YOUR_PROJECT_ID/locations/asia-northeast1/repositories/fraud-pipeline-repo/workspaces/initial-setup
```

### 開発ワークスペースを作成する

1. BigQuery → Dataform → `fraud-pipeline-repo`
2. **開発ワークスペース** タブ → **開発ワークスペースを作成**
3. ワークスペース ID: `initial-setup` → 作成

⑥ で Git 接続済みなら、空のテンプレートで初期化しない。**「ワークスペースを初期化」は使わない**（`sample.sqlx` などが入り、`credit_detect_dataform` の sqlx と食い違う）。

ファイルが見えない／空なら、ワークスペースを開き **Git → リモートから pull**（デフォルト ブランチ `main`）。`definitions/initial_setup/` と `definitions/daily_batch/` が見えること。

### コンパイルする

コンソールはワークスペースを開くと自動コンパイルします。

1. `initial-setup` を開く
2. 画面上部のコンパイル状態が成功であること
3. **コンパイル済みグラフ** タブで DAG が出ること（エラー文言ではなくグラフ）

失敗しやすい例: `workflow_settings.yaml` の `defaultProject` が実プロジェクトと違う、Git 先が `credit_detect` 本体で `definitions/` がネストしている、`package.json` が無く `Can't find package.json` になる、`dataform.json` が残っていて `has been deprecated and cannot be defined alongside workflow_settings.yaml` になる。ルートに `package.json`（`@dataform/core`）があり、`dataform.json` が無いこと。初回はファイルを開いて **パッケージをインストール** する。公開表を Dataform から直接読むと `Access Denied: Table bigquery-public-data:ml_datasets.ulb_fraud_detection` になる（後述のコピーと IAM を先にやる）。

### Dataform SA の BigQuery 権限と公開データのコピー

`initial_setup` の先頭アクションは、かつては公開表 `bigquery-public-data.ml_datasets.ulb_fraud_detection` を直接読んでいました。次のエラーで落ちます。

```
Access Denied: Table bigquery-public-data:ml_datasets.ulb_fraud_detection: User does not have permission to query table bigquery-public-data:ml_datasets.ulb_fraud_detection, or perhaps it does not exist.
```

原因は次の両方です（文言は権限不足と同じに見えます）。

1. Dataform 実行 SA に BigQuery のジョブ作成・書き込みが無い  
2. 公開表は **US** マルチリージョン、`dwh_prod` と Dataform の `defaultLocation` は **asia-northeast1**。リージョンをまたぐ 1 本のクエリは書けない（Cloud Run Job が Query → Load に分けているのと同じ制約）

公開プロジェクト `bigquery-public-data` に IAM を付けることはできません。自分のプロジェクト側に権限を付け、公開表を `dwh_prod` へコピーしてから Dataform を回します。

#### 1. Dataform SA へ BigQuery 権限を付ける

Terraform は Dataform SA に `jobUser` / `dataViewer`（プロジェクト）と `dataEditor`（`dwh_prod`）を付けます。未 apply、または apply が古い場合は次の gcloud で足せます。コンソールの IAM 一覧では Google 管理 SA が隠れるので、**「Google 提供のロール付与を含める」** をオンにして確認します。

```
service-PROJECT_NUMBER@gcp-sa-dataform.iam.gserviceaccount.com
```

| ロール | 対象 | 用途 |
| --- | --- | --- |
| `roles/bigquery.jobUser` | プロジェクト | クエリ / ロード / `CREATE MODEL` のジョブ作成 |
| `roles/bigquery.dataViewer` | プロジェクト | 参照（公式が Dataform に要求） |
| `roles/bigquery.dataEditor` | データセット `dwh_prod` | 変換テーブルと BQML モデルの作成 |

未付与なら（Owner で実行）:

```bash
PROJECT_ID="$(gcloud config get-value project)"
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
DATAFORM_SA="service-${PROJECT_NUMBER}@gcp-sa-dataform.iam.gserviceaccount.com"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${DATAFORM_SA}" \
  --role="roles/bigquery.jobUser"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${DATAFORM_SA}" \
  --role="roles/bigquery.dataViewer"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${DATAFORM_SA}" \
  --role="roles/bigquery.dataEditor"
```

データセット単位に絞る場合は、最後の `dataEditor` の代わりにコンソールで **BigQuery → `dwh_prod` → 共有** から、同じ SA に **BigQuery データ編集者** を付けます（terraform の既定もデータセット単位です）。

確認:

```bash
gcloud projects get-iam-policy "${PROJECT_ID}" \
  --flatten="bindings[].members" \
  --filter="bindings.members:serviceAccount:${DATAFORM_SA}" \
  --format="table(bindings.role)"
```

`roles/bigquery.jobUser` が見えること。IAM 反映は数十秒かかることがあります。

組織で VPC Service Controls を使っていると、公開データセット自体が遮断されます。その場合は管理者に `bigquery-public-data` への egress を依頼するか、別経路で CSV を `dwh_prod` へ入れてください。

自分のアカウントで公開表が読めるかは、処理ロケーション **US** で次を実行して確認します。

```bash
bq query --location=US --use_legacy_sql=false --nouse_cache \
  'SELECT COUNT(*) AS n FROM `bigquery-public-data.ml_datasets.ulb_fraud_detection`'
```

#### 2. 公開表を `dwh_prod` へコピーする

Dataform はコピー後の `dwh_prod.ulb_fraud_detection_public` だけを読みます。`credit_detect_dataform` 側にも declaration `ulb_fraud_detection_public` と、それを `ref` する `initial_converted.sqlx` が必要です（本リポジトリの `dataform/` と揃える。ワークスペースで pull）。

ADC は ① のユーザー（公開表を US で読めるアカウント）です。

Ubuntu / WSL では `python` コマンドは標準では入りません（`python3` のみ）。`python copy_public_source.py` は **`python3` で仮想環境を作り、それを有効化したあと** で実行します。venv 内に `python` が作られます。`.venv` は git に含まれないので、② を飛ばした場合や clone し直した場合はここで作ります。`python3 -m venv` が失敗したら `sudo apt install python3-venv python3.12-venv python3-full`。

```bash
cd batch_app

# ② で batch_app/.venv を作済みなら、作成と pip は省略可
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export PROJECT_ID="$(gcloud config get-value project)"
python copy_public_source.py
deactivate
cd ..

bq show --location=asia-northeast1 "${PROJECT_ID}:dwh_prod.ulb_fraud_detection_public"
```

プロンプトに `(.venv)` が出ていることを確認してから `python` を実行してください。venv を使わず `python3 copy_public_source.py` でも起動はできますが、Ubuntu 24.04 以降はシステム `pip` が拒否されるため非推奨です。

約 28 万行です。Query は US、Load は asia-northeast1 です。成功するとテーブルが見えます。

---

### タグ `initial_setup` を実行する

依存順:

1. `ulb_fraud_detection_converted` … ローカルコピー全件 + Hour + 疑似 Date 1–50  
2. `ulb_fraud_detection_validation` … Date 32–36  
3. `ulb_fraud_detection_train` … Date 37–50  
4. `ulb_fraud_detection_model` … `BOOSTED_TREE_CLASSIFIER`（`ENABLE_GLOBAL_EXPLAIN=TRUE`）

コンソール:

1. ワークスペース `initial-setup` のツールバー → **実行を開始 → アクションを実行**
2. **タグの選択** → `initial_setup`
3. **依存関係を含める** をオン（従属はオフでよい）
4. 開始
5. `CREATE MODEL` 完了まで待つ（数分〜十数分、課金の主因）。実行タブで状態を確認

この時点では Cloud Run の Batch テーブルは不要です。初期変換は **先にコピーした** `dwh_prod.ulb_fraud_detection_public` を読みます。コピーしていないと declaration 先が空で失敗します。

確認:

```bash
PROJECT_ID="$(gcloud config get-value project)"
bq ls --location=asia-northeast1 "${PROJECT_ID}:dwh_prod"
bq show -m "${PROJECT_ID}:dwh_prod.ulb_fraud_detection_model"
```

モデルが無いと推論は失敗します。

gcloud でワークスペースを作る場合の例:

```bash
PROJECT_ID="$(gcloud config get-value project)"
gcloud dataform workspaces create initial-setup \
  --project="${PROJECT_ID}" \
  --location=asia-northeast1 \
  --repository=fraud-pipeline-repo
```

コマンドが無い環境ではコンソールで作成する。コンパイルとタグ実行もコンソールが確実です。

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

- terraform apply が `serviceusage.services.list` で 403 → Service Usage / Cloud Resource Manager を gcloud で有効化し、ADC をやり直す
- terraform apply が Dataform SA `does not exist` → ⑤ の `gcloud beta services identity create --service=dataform.googleapis.com` を実行してから再 apply
- `--tag` のプロジェクト ID がプレースホルダのまま → ① の `gcloud config` と一致させる
- ⑦より先に Workflows を回す → モデルなしで `ML.PREDICT` 失敗
- Dataform が `credit_detect` 本体を向いている → sqlx がコンパイルされない
- イメージ未プッシュ → Job が Image not found
- `workflow_settings.yaml` の `defaultProject` が違う → 別プロジェクトに表が立つ / 権限エラー
- 公開表を Dataform が直接読む → `Access Denied: Table bigquery-public-data:ml_datasets.ulb_fraud_detection`（US と asia-northeast1 の跨ぎ + SA 権限）。⑦の IAM 付与と `copy_public_source.py` を先に行う
- `python: command not found` / `.venv/bin/activate: No such file` → ⑦ の `python3 -m venv .venv` を先に実行する。システムに `python` は無くてよい
- `load_table_from_dataframe() got an unexpected keyword argument 'retry'` → `Client.load_table_from_dataframe` は `retry=` を受けない。現行コードは `num_retries` を使うので `main` を pull する
- `dataform.json` が `workflow_settings.yaml` と同居 → Dataform core 3.0 でコンパイル失敗（deprecated）
- 空スライス（`TARGET_DATE` が 32–50 など）→ Job は TRUNCATE を拒否して失敗。Scheduler の既定は JST のカレンダー日なので通常 1–31

---

## 手順対応表（提示案 → 実施順）

| 提示 | 注意 |
| --- | --- |
| ① clone | `main` を取る |
| ② 単体テスト | `evaluate/`、`batch_app/`、`agent/tests` |
| ③ AR 登録 | Compute デフォルト SA に Storage / Logging / AR 権限 + **`batch_app/` から** Cloud Build |
| ④ プロジェクト情報 | **`terraform.tfvars`。`main.tf` は触らない** |
| ⑤ terraform | 先に Service Usage / Resource Manager / Dataform identity を gcloud で用意。Scheduler が即時有効 |
| ⑥ Dataform Git | 接続先は `credit_detect_dataform`。HTTPS は Secret Manager の PAT + Dataform SA の `secretAccessor` |
| ⑦ 初回 sqlx | Dataform SA の BQ IAM + 公開表を `dwh_prod` へコピー。ワークスペース ID `initial-setup` を作成してコンパイル。タグ `initial_setup` |
| ⑧ 結合試験 | Workflows を 1 回手動実行 |
| ⑨ 調査 Agent | 第8節。推論テーブルがあること。`enable_agent=true`、Cloud Run はイメージ push 後 |

---

# 8. Autonomous Fraud Investigation Agent の GCP 構築

本節だけ読めば、既存の `dwh_prod` 推論テーブルの上に調査 Agent を東京リージョンへ出せます。仕様の詳細は [Agent.md](Agent.md) です。

PoC では **2 つのデプロイ経路** を用意しています。

| 経路 | 使いどころ | エージェントの実行場所 |
| --- | --- | --- |
| **A. Cloud Run 同梱（推奨）** | 最短で画面まで出す | Streamlit コンテナ内の LangGraph |
| **B. Vertex AI Agent Engine** | 設計メモどおりのマネージド実行 | Reasoning Engine。UI は Cloud Run |

どちらも BigQuery と Gemini は `asia-northeast1`、ナレッジは `dwh_prod.fraud_investigation_knowledge` です。

## 8.1 前提条件

1. 日次パイプラインが一度以上成功し、次が存在すること。
   - `{PROJECT}.dwh_prod.ulb_fraud_detection_model`
   - `{PROJECT}.dwh_prod.ulb_fraud_detection_predictions`
2. 操作する Google アカウントに、対象プロジェクトの Owner または次相当があること。
   - `roles/aiplatform.admin` または `roles/aiplatform.user`
   - `roles/bigquery.admin` または jobUser + データセット編集
   - `roles/run.admin`, `roles/storage.admin`, `roles/iam.serviceAccountUser`
   - Terraform 用の `roles/resourcemanager.projectIamAdmin`（既存 SA への IAM 追加）
3. 課金アカウントがリンクされていること。Vertex AI と BigQuery は従量です。
4. ローカルに `gcloud`、`terraform` >= 1.5、Python 3.11、Docker または Cloud Build。

パイプライン未構築の場合は、先に第7節と `terraform apply`（`enable_agent_cloud_run=false` のまま）でデータセットと Workflows を作り、Dataform の `initial_setup` と `daily_batch` を実行してください。Agent は推論テーブルが空でも mock では動けますが、本番相当の調査には予測行が必要です。

## 8.2 プロジェクトと認証

```bash
export PROJECT_ID="your-gcp-project-id"
export REGION="asia-northeast1"
export DATASET_ID="dwh_prod"

gcloud auth login
gcloud auth application-default login
gcloud config set project "${PROJECT_ID}"
gcloud config set billing/quota_project "${PROJECT_ID}"
```

Application Default Credentials が Vertex / BigQuery の両方で使われます。組織ポリシーで `iam.disableServiceAccountKeyCreation` が有効でも、ADC と Cloud Run の実行 SA だけで完結する想定です（キーは作りません）。

## 8.3 有効化する API

Terraform（`terraform/main.tf` と `terraform/agent.tf`）が主に有効化します。手動で先に通す場合:

```bash
gcloud services enable \
  aiplatform.googleapis.com \
  bigquery.googleapis.com \
  storage.googleapis.com \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  iam.googleapis.com \
  cloudresourcemanager.googleapis.com
```

初回の `aiplatform.googleapis.com` は数分かかることがあります。Agent Engine 用の Google 管理 SA `service-{PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com` は API 有効化後に自動作成されます。Terraform がその SA へ BQ / GCS 権限を付けます。

## 8.4 Terraform で基盤を作る

日次パイプライン側の `terraform apply` 手順（Service Usage の先行有効化、Dataform identity の実体化）は第7節⑤に従ってください。
Agent 用リソースは同じ `terraform/` ディレクトリの `agent.tf` が追加します。
`enable_agent_cloud_run` はイメージ未 push のあいだ `false` のままにします。

```bash
cd terraform
cp example.tfvars terraform.tfvars
# terraform.tfvars を編集:
#   project_id                 = "your-gcp-project-id"
#   enable_agent               = true
#   enable_agent_cloud_run     = false   # イメージ未pushのうちは false
#   agent_ui_unauthenticated   = false

terraform init
terraform plan
terraform apply
```

作成される Agent 関連リソース:

| リソース | 名前 / ID | 役割 |
| --- | --- | --- |
| GCS | `{project_id}-fraud-agent-staging` | Agent Engine のパッケージステージング |
| SA | `sa-fraud-agent` | Cloud Run UI と Vertex 呼出し |
| BQ テーブル | `dwh_prod.fraud_investigation_knowledge` | 規程の本文と 768 次元 embedding |
| IAM | Agent Engine 管理 SA への jobUser / dataEditor / objectAdmin | マネージド実行から BQ と GCS を触るため |

適用後に出力を控えます。

```bash
terraform output agent_staging_bucket
terraform output agent_runtime_sa
terraform output knowledge_table
```

ステージングバケット URL は次の形です。

```text
gs://YOUR_PROJECT_ID-fraud-agent-staging
```

## 8.5 ナレッジ（規程）の初期投入とベクトル化

設計どおり、調査マニュアルは初期状態では未整備です。リポジトリの `knowledge/` に SOP 要約・社内規程ドラフト・カードテスト観点・エスカレーション手順を入れてあるので、それを BigQuery に埋め込みます。

```bash
cd /path/to/credit_detect
python3 -m venv .venv
source .venv/bin/activate
pip install -r agent/requirements.txt

export PROJECT_ID="your-gcp-project-id"
export LOCATION="asia-northeast1"
export DATASET_ID="dwh_prod"

# チャンク内容の確認（GCP 不要）
python scripts/seed_knowledge.py --dry-run

# 本番: text-embedding-004 でベクトル化し、knowledge テーブルへ UPSERT
python scripts/seed_knowledge.py \
  --project-id "${PROJECT_ID}" \
  --source-dir knowledge
```

追加のマニュアル（例: 正式版の評価手順）も同じテーブルに載せられます。

```bash
python scripts/seed_knowledge.py \
  --project-id "${PROJECT_ID}" \
  --file EVALUATE.md
```

行数が少ないと `CREATE VECTOR INDEX` は失敗することがあります。スクリプトは警告だけ出して続行します。数十件規模ではインデックス無しの `VECTOR_SEARCH` で十分です。

投入確認:

```bash
bq query --use_legacy_sql=false --location="${REGION}" "
SELECT category, COUNT(*) AS n
FROM \`${PROJECT_ID}.${DATASET_ID}.fraud_investigation_knowledge\`
GROUP BY category
"
```

## 8.6 ローカル動作確認（必須）

GCP を使う前に、グラフが mock で閉じることを確認します。

```bash
export PYTHONPATH="$(pwd):$(pwd)/evaluate"
python -m unittest discover -s agent/tests -v
python -m unittest discover -s evaluate -v

export AGENT_BACKEND=mock
python scripts/local_query.py --backend mock \
  --query "不正確率0.85以上かつAmount200ドル以上を調査し遮断SQLを提案して"
```

JSON に `final_report`・`generated_sql`・`statistical_summary.strong_separators` が入れば成功です。

ADC が通って推論テーブルがある場合は local モードで実データを読めます。

```bash
export AGENT_BACKEND=local
export PROJECT_ID="your-gcp-project-id"
python scripts/local_query.py --backend local
```

権限エラーのときは、自分のユーザーに `bigquery.jobUser` とデータセット閲覧、および `aiplatform.user` があるか確認してください。サービスアカウントで動かす場合は `sa-fraud-agent` を impersonate します。

```bash
gcloud iam service-accounts add-iam-policy-binding \
  sa-fraud-agent@${PROJECT_ID}.iam.gserviceaccount.com \
  --member="user:YOU@example.com" \
  --role="roles/iam.serviceAccountTokenCreator"

gcloud auth application-default login --impersonate-service-account \
  sa-fraud-agent@${PROJECT_ID}.iam.gserviceaccount.com
```

## 8.7 経路 A: Cloud Run に UI + LangGraph を載せる（推奨 PoC）

Agent Engine を待たずに、アナリスト向け画面とエージェント実行を同じサービスにまとめます。コールドスタートはありますが、`min_instances = 0` のためアイドル課金はほぼゼロです。

### 8.7.1 コンテナを Artifact Registry へ

第7節で `my-repo` が無い場合は先に作成します。**ビルドコンテキストはリポジトリルート**です（`agent/` `evaluate/` `knowledge/` `app/` を同梱するため）。設定ファイルは `cloudbuild.agent.yaml` です。

```bash
gcloud artifacts repositories create my-repo \
    --repository-format=docker \
    --location="${REGION}" \
    --description="credit_detect images" \
  || true

gcloud builds submit . --config=cloudbuild.agent.yaml \
  --substitutions=_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/my-repo/fraud-agent-ui:latest"
```

ローカル Docker を使う場合:

```bash
gcloud auth configure-docker "${REGION}-docker.pkg.dev"
docker build -f app/Dockerfile \
  -t "${REGION}-docker.pkg.dev/${PROJECT_ID}/my-repo/fraud-agent-ui:latest" .
docker push "${REGION}-docker.pkg.dev/${PROJECT_ID}/my-repo/fraud-agent-ui:latest"
```

### 8.7.2 Cloud Run サービスを Terraform または gcloud で公開

イメージが registry に載ってから:

```hcl
# terraform.tfvars
enable_agent_cloud_run   = true
agent_ui_unauthenticated = false
```

```bash
cd terraform && terraform apply
terraform output agent_ui_uri
```

または gcloud（Terraform の SA を実行ユーザーにする）:

```bash
gcloud run deploy fraud-agent-ui \
  --image="${REGION}-docker.pkg.dev/${PROJECT_ID}/my-repo/fraud-agent-ui:latest" \
  --region="${REGION}" \
  --service-account="sa-fraud-agent@${PROJECT_ID}.iam.gserviceaccount.com" \
  --cpu=1 --memory=1Gi --timeout=300 \
  --min-instances=0 --max-instances=2 \
  --set-env-vars="PROJECT_ID=${PROJECT_ID},LOCATION=${REGION},DATASET_ID=${DATASET_ID},AGENT_BACKEND=local,MODEL_NAME=gemini-2.0-flash" \
  --no-allow-unauthenticated
```

自分のアカウントに実行権限を付けます。

```bash
gcloud run services add-iam-policy-binding fraud-agent-ui \
  --region="${REGION}" \
  --member="user:YOU@example.com" \
  --role="roles/run.invoker"

gcloud run services proxy fraud-agent-ui --region="${REGION}" --port=8080
```

デモで一時的に公開する場合のみ（データが公開データ由来でも、生成 SQL と規程が外に出ます）:

```bash
gcloud run services add-iam-policy-binding fraud-agent-ui \
  --region="${REGION}" \
  --member="allUsers" \
  --role="roles/run.invoker"
```

### 8.7.3 画面での確認手順

1. サイドバー **バックエンド** を `local`（Cloud Run 既定）または手元なら `mock`
2. Project ID / Location / Dataset が `dwh_prod` になっていること
3. 既定の調査文のまま「自律調査を開始」
4. 「調査レポート」にエグゼクティブサマリーが出ること
5. 「実行プロセス」に Plan、`fraud_probability` を含む SELECT、統計の `strong_separators`
6. 「即時是正SQL」が `SELECT` であり `DELETE` でないこと
7. 任意: `knowledge/` 以外の `.md` をアップロードし「ベクトル化して登録」後、もう一度調査して「参照規程」に新しい title が出ること

## 8.8 経路 B: Vertex AI Agent Engine（Reasoning Engine）

Cloud Run を UI 専用にし、グラフをマネージド実行する場合です。ステージングバケットと `sa-fraud-agent` の権限は 8.4 で済んでいます。

```bash
source .venv/bin/activate
export PROJECT_ID="your-gcp-project-id"
export LOCATION="asia-northeast1"
export STAGING_BUCKET="gs://${PROJECT_ID}-fraud-agent-staging"
export MODEL_NAME="gemini-2.0-flash"

python scripts/deploy_reasoning_engine.py \
  --project-id "${PROJECT_ID}" \
  --location "${LOCATION}" \
  --staging-bucket "${STAGING_BUCKET}" \
  --model-name "${MODEL_NAME}"
```

成功すると次のようなリソース名が印字されます。

```text
projects/PROJECT_NUMBER/locations/asia-northeast1/reasoningEngines/RESOURCE_ID
```

コンソール確認: Vertex AI → Agent Engine（または Reasoning Engine）→ `fraud-investigation-agent`。

Cloud Run から呼ぶ場合は環境変数を足してリビジョンを出します。

```bash
gcloud run services update fraud-agent-ui \
  --region="${REGION}" \
  --set-env-vars="AGENT_BACKEND=reasoning_engine,REASONING_ENGINE_RESOURCE_NAME=projects/.../reasoningEngines/..."
```

UI サイドバーのバックエンドを `reasoning_engine` にし、同じリソース名を貼って調査を実行します。

`agent_engines.create` がリージョンや SDK 差で失敗する場合、スクリプトは `vertexai.preview.reasoning_engines.ReasoningEngine.create` にフォールバックします。それでも失敗するときは経路 A を正とし、Issue に SDK バージョンとエラー全文を残してください。

必要な IAM（Terraform 済みの再掲）:

- デプロイするユーザー: `roles/aiplatform.admin`, ステージングバケットへの objectAdmin
- `service-{NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com`: `roles/aiplatform.user`, `roles/bigquery.jobUser`, データセット `dataEditor`, ステージング objectAdmin
- Cloud Run SA `sa-fraud-agent`: `roles/aiplatform.user`（リモート query 用）

## 8.9 Gemini モデル名

設計メモはコスト優先で `gemini-1.5-flash-002` です。2026-09 時点の東京リージョンでは `gemini-2.0-flash` を既定にしています。

```bash
gcloud alpha ai models list --region="${REGION}" | grep -i gemini
```

使える Flash 系があれば `MODEL_NAME` と Cloud Run の env を合わせてください。Pro は PoC 予算（月 2 万円）を圧迫しやすいので使わないでください。

## 8.10 コスト監視（FinOps）

1. Billing → 予算 → 月 20,000 JPY、80% でメール
2. 対象サービス: Vertex AI, BigQuery, Cloud Run, Cloud Storage
3. エージェントは SELECT に 10GiB billed キャップ、結果 200 行
4. ナレッジ再埋め込みは差分 UPSERT だが、全文書を頻繁にやり直すと Embedding API が嵩む
5. Cloud Run `min_instances=0`、Agent Engine もリクエスト課金。検証後はサービスを止める（8.12）

## 8.11 よくある失敗

| 症状 | 対処 |
| --- | --- |
| `404` Gemini | `LOCATION` とモデルのリージョン対応を確認。`us-central1` へだけ逃がすのは最終手段（BQ は東京のままクロスリージョン課金） |
| `Access Denied` VECTOR_SEARCH | `sa-fraud-agent` にデータセット dataEditor / Viewer。テーブルが terraform で出来ているか |
| `maximumBytesBilled exceeded` | 生成 SQL が公開全表スキャン。プロンプトは `predictions` を優先。必要ならテーブルを `Date` で絞る |
| SQL が `predicted_Class_probs` を参照 | 推論テーブルにその列は無い。`fraud_probability` を使う。Reflection が再生成する |
| Cloud Run タイムアウト | `--timeout=300`。それでも足りなければ調査 SQL を狭める |
| Agent Engine の import エラー | `extra_packages` に `agent` と `evaluate`。経路 A で切り分け |
| ベクトルインデックス作成失敗 | 行数不足。無視してよい |

## 8.12 破棄

```bash
# Cloud Run UI
gcloud run services delete fraud-agent-ui --region="${REGION}" --quiet

# Agent Engine（リソース名はデプロイ時の出力）
gcloud ai reasoning-engines delete RESOURCE_ID --region="${REGION}" --quiet \
  || echo "コンソールの Vertex AI > Agent Engine から削除"

cd terraform
terraform apply -var="enable_agent=false" -var="enable_agent_cloud_run=false"
# または Agent ごとプロジェクトを検証用にしている場合
# terraform destroy
```

ナレッジテーブルだけ消す場合:

```bash
bq rm -f -t "${PROJECT_ID}:${DATASET_ID}.fraud_investigation_knowledge"
```

日次パイプライン（Scheduler / Workflows / モデル）は Agent 破棄後も残します。

---

# 9. 開発者向けクイックスタート（画面のみ）

```bash
pip install -r app/requirements.txt
export PYTHONPATH="$(pwd):$(pwd)/evaluate"
export AGENT_BACKEND=mock
streamlit run app/app.py --server.port=8080
```

ブラウザで `http://localhost:8080` を開き、第 8.7.3 節と同じ操作をします。
