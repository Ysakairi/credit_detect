terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
}

variable "project_id" {
  description = "GCP Project ID"
  type        = string
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "asia-northeast1"
}

variable "repo_docker" {
  description = "Artifact Registry repository name"
  type        = string
  default     = "my-repo"
}

variable "dataform_git_url" {
  description = "HTTPS URL of the GitHub repository with sqlx at the repo root (credit_detect_dataform). Do not use credit_detect; Dataform only compiles definitions/ at the root."
  type        = string
  default     = "https://github.com/Ysakairi/credit_detect_dataform.git"

  validation {
    condition     = !can(regex("/credit_detect(\\.git)?/?$", var.dataform_git_url))
    error_message = "Dataform Git URL must be credit_detect_dataform (sqlx at repo root), not credit_detect."
  }
}

variable "dataform_github_token_secret" {
  description = "Secret Manager version resource name for the GitHub PAT used by Dataform (projects/.../secrets/.../versions/...). Empty skips Git on create; later applies do not clear an existing console Git link."
  type        = string
  default     = ""
  sensitive   = true

  validation {
    condition = var.dataform_github_token_secret == "" || can(regex(
      "^projects/[^/]+/secrets/[^/]+/versions/[^/]+$",
      var.dataform_github_token_secret,
    ))
    error_message = "dataform_github_token_secret must be empty or a Secret Manager version name (projects/.../secrets/.../versions/...)."
  }
}

resource "google_project_service_identity" "dataform" {
  provider   = google-beta
  project    = var.project_id
  service    = "dataform.googleapis.com"
  depends_on = [google_project_service.apis]
}

locals {
  dataform_sa = "serviceAccount:${google_project_service_identity.dataform.email}"
  # Empty token → try() returns "" so the IAM binding is skipped.
  dataform_github_token_secret_id = try(
    regex("secrets/([^/]+)/", var.dataform_github_token_secret),
    "",
  )
  apis = [
    "bigquery.googleapis.com",
    "run.googleapis.com",
    "workflows.googleapis.com",
    "cloudscheduler.googleapis.com",
    "dataform.googleapis.com",
    "iam.googleapis.com",
    "secretmanager.googleapis.com",
  ]
}

# ==========================================
# 0. API 有効化
# ==========================================
resource "google_project_service" "apis" {
  for_each           = toset(local.apis)
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# ==========================================
# 1. IAM サービスアカウント
# ==========================================
resource "google_service_account" "run_jobs_sa" {
  account_id   = "sa-run-jobs-executor"
  display_name = "Service Account for Cloud Run Jobs"
  depends_on   = [google_project_service.apis]
}

resource "google_bigquery_dataset_iam_member" "run_jobs_bq_editor" {
  dataset_id = google_bigquery_dataset.dwh_prod.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.run_jobs_sa.email}"
}

# 公開データセットへのクエリ実行には jobs.create が必要
resource "google_project_iam_member" "run_jobs_bq_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.run_jobs_sa.email}"
}

resource "google_service_account" "workflows_sa" {
  account_id   = "sa-workflows-orchestrator"
  display_name = "Service Account for Workflows"
  depends_on   = [google_project_service.apis]
}

resource "google_service_account" "scheduler_sa" {
  account_id   = "sa-scheduler-trigger"
  display_name = "Service Account for Cloud Scheduler to trigger Workflows"
  depends_on   = [google_project_service.apis]
}

resource "google_project_iam_member" "scheduler_workflows_invoker" {
  project = var.project_id
  role    = "roles/workflows.invoker"
  member  = "serviceAccount:${google_service_account.scheduler_sa.email}"
}

resource "google_project_iam_member" "workflows_dataform_editor" {
  project = var.project_id
  role    = "roles/dataform.editor"
  member  = "serviceAccount:${google_service_account.workflows_sa.email}"
}

# Dataform 実行基盤 SA（BQML CREATE MODEL / テーブル作成）
# google_project_service_identity はメールを返すが、SA 本体はリポジトリ作成後に
# 見えることが多い。先に repository を作り、それでも無い場合は README ⑤ の
# `gcloud beta services identity create` を先に実行する。
resource "google_bigquery_dataset_iam_member" "dataform_bq_editor" {
  dataset_id = google_bigquery_dataset.dwh_prod.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = local.dataform_sa
  depends_on = [
    google_project_service_identity.dataform,
    google_dataform_repository.fraud_pipeline_repo,
  ]
}

resource "google_project_iam_member" "dataform_bq_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = local.dataform_sa
  depends_on = [
    google_project_service_identity.dataform,
    google_dataform_repository.fraud_pipeline_repo,
  ]
}

# 公式は Dataform 実行 ID に dataViewer も要求する（参照専用ソース用）。
# 公開プロジェクト bigquery-public-data には付けられない。公開表は README ⑦
# で dwh_prod へコピーしてから読む。
resource "google_project_iam_member" "dataform_bq_data_viewer" {
  project = var.project_id
  role    = "roles/bigquery.dataViewer"
  member  = local.dataform_sa
  depends_on = [
    google_project_service_identity.dataform,
    google_dataform_repository.fraud_pipeline_repo,
  ]
}

# ==========================================
# 2. BigQuery データセット
# ==========================================
resource "google_bigquery_dataset" "dwh_prod" {
  dataset_id    = "dwh_prod"
  friendly_name = "DWH Production Dataset"
  description   = "Dataset for fraud detection data pipeline"
  location      = var.region
  depends_on    = [google_project_service.apis]
}

# ==========================================
# 3. Cloud Run Jobs
# ==========================================
resource "google_cloud_run_v2_job" "daily_ingest" {
  name     = "daily-ingest-job"
  location = var.region
  # deletion_protection は google provider 6.0 で追加。本構成は ~> 5.0 のため指定しない。
  depends_on = [google_project_service.apis]

  template {
    template {
      # ADC 用 SA。キーはコンテナに埋め込まない。
      service_account = google_service_account.run_jobs_sa.email
      # 600s: 公開データ Query（US）+ 地域 Load。アプリ側 Job 待ちは 480s で先に落とす。
      timeout         = "600s"
      # 3: アプリの Exponential Backoff で足りない起動時障害向け。WRITE_TRUNCATE なので再実行はべき等。
      max_retries     = 3

      containers {
        image = "${var.region}-docker.pkg.dev/${var.project_id}/${var.repo_docker}/daily-ingest:latest"

        resources {
          # 日次スライスは約 1/50。全表ダウンロードをやめた前提の上限。
          limits = {
            cpu    = "1"
            memory = "1Gi"
          }
        }

        # 機密ではない構成値。Secret Manager は GitHub PAT（Dataform）側で使う。
        env {
          name  = "PROJECT_ID"
          value = var.project_id
        }
        env {
          name  = "DESTINATION_TABLE"
          value = "${var.project_id}.${google_bigquery_dataset.dwh_prod.dataset_id}.ulb_fraud_detection_Batch"
        }
      }
    }
  }
}

# jobs.run と executions.get（ポーリング）の両方に必要
resource "google_cloud_run_v2_job_iam_member" "workflows_job_developer" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.daily_ingest.name
  role     = "roles/run.developer"
  member   = "serviceAccount:${google_service_account.workflows_sa.email}"
}

# ==========================================
# 4. Dataform リポジトリ
# ==========================================
resource "google_dataform_repository" "fraud_pipeline_repo" {
  provider     = google-beta
  project      = var.project_id
  region       = var.region
  name         = "fraud-pipeline-repo"
  display_name = "Credit fraud detection pipeline"
  depends_on = [
    google_project_service.apis,
    google_project_service_identity.dataform,
  ]

  # Token set: attach Git on create. Token empty: create without Git, then
  # connect in the console (README ⑥ B).
  dynamic "git_remote_settings" {
    for_each = var.dataform_github_token_secret == "" ? [] : [1]
    content {
      url                                 = var.dataform_git_url
      default_branch                      = "main"
      authentication_token_secret_version = var.dataform_github_token_secret
    }
  }

  # Apply with an empty token used to PATCH-clear git_remote_settings and
  # unlink Git. Daily compile then fails with "git reference 'main' could
  # not be resolved". Ignore updates so console- or create-time Git survives.
  lifecycle {
    ignore_changes = [git_remote_settings]
  }
}

# Terraform-managed Git needs the Dataform SA to read the PAT secret.
# Console linking still needs the same binding (README ⑥ step 3) if this
# resource is skipped because the token variable is empty.
resource "google_secret_manager_secret_iam_member" "dataform_github_token_accessor" {
  count     = local.dataform_github_token_secret_id == "" ? 0 : 1
  project   = var.project_id
  secret_id = local.dataform_github_token_secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = local.dataform_sa
  depends_on = [
    google_project_service_identity.dataform,
  ]
}

# ==========================================
# 5. Workflows
# ==========================================
resource "google_workflows_workflow" "fraud_detection_pipeline" {
  name            = "fraud-detection-pipeline"
  region          = var.region
  description     = "Daily fraud detection ingestion and ML pipeline"
  service_account = google_service_account.workflows_sa.email
  depends_on = [
    google_project_service.apis,
    google_cloud_run_v2_job.daily_ingest,
    google_dataform_repository.fraud_pipeline_repo,
  ]

  # Workflows 式は workflow.yaml 側で $${} エスケープ済み
  source_contents = templatefile("${path.module}/workflow.yaml", {
    project_id    = var.project_id
    region        = var.region
    dataform_repo = google_dataform_repository.fraud_pipeline_repo.name
  })
}

# ==========================================
# 6. Cloud Scheduler
# ==========================================
resource "google_cloud_scheduler_job" "daily_trigger" {
  name        = "daily-fraud-pipeline-trigger"
  region      = var.region
  description = "Trigger workflows daily at 2:00 AM JST"
  schedule    = "0 2 * * *"
  time_zone   = "Asia/Tokyo"
  depends_on  = [google_project_service.apis]

  http_target {
    http_method = "POST"
    uri         = "https://workflowexecutions.googleapis.com/v1/projects/${var.project_id}/locations/${var.region}/workflows/${google_workflows_workflow.fraud_detection_pipeline.name}/executions"

    oauth_token {
      service_account_email = google_service_account.scheduler_sa.email
    }
  }
}
