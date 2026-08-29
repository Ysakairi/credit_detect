# クレジットカード不正監視データパイプライン構築

# 1. プロジェクト概要

公開されているクレジットカード取引データセットを活用し、日次のデータ取り込みからデータ加工、機械学習モデルによる不正検知、そしてダッシュボードでの可視化までを一貫して行う自動化パイプラインを構築します。
実運用を想定し、インフラストラクチャはすべてTerraformを用いてコード化（IaC）しています。

# 2. アーキテクチャ

![credit_check.png](credit_check.png)

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

## Artifact RegistryにCloud Run Jobsのファイル一式を登録する

```bash
# 1. 必要なAPIを有効化する
gcloud services enable artifactregistry.googleapis.com cloudbuild.googleapis.com

# 2. Docker用のリポジトリ（my-repo とする）を作成する
gcloud artifacts repositories create my-repo \
    --repository-format=docker \
    --location=asia-northeast1 \
    --description="Repository for fraud detection pipeline"
    
# 3. ビルド＆プッシュの実行
gcloud builds submit --tag asia-northeast1-docker.pkg.dev/YOUR_PROJECT/my-repo/daily-ingest:latest
```

## main.tfにプロジェクトの情報を登録する

```bash
variable "project_id" {
  description = "GCP Project ID"
  type        = string
}

variable "region" {
  default = "asia-northeast1"
}

# Artifact Registryの登録名
variable "repo_docker" {
  default = "my-repo"
}
```

## Dataformにgithubの情報を登録する

追記中