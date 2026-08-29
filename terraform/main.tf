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

# ==========================================
# 1. IAM サービスアカウントの作成
# ==========================================
# Run Jobs実行用のSA
resource "google_service_account" "run_jobs_sa" {
  account_id   = "sa-run-jobs-executor"
  display_name = "Service Account for Cloud Run Jobs"
}
resource "google_project_iam_member" "run_jobs_bq_admin" {
  project = var.project_id
  role    = "roles/bigquery.dataEditor"
  member  = "serviceAccount:${google_service_account.run_jobs_sa.email}"
}

# Workflows実行用のSA
resource "google_service_account" "workflows_sa" {
  account_id   = "sa-workflows-orchestrator"
  display_name = "Service Account for Workflows"
}
resource "google_project_iam_member" "workflows_run_invoker" {
  project = var.project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:${google_service_account.workflows_sa.email}"
}
resource "google_project_iam_member" "workflows_dataform_editor" {
  project = var.project_id
  role    = "roles/dataform.editor"
  member  = "serviceAccount:${google_service_account.workflows_sa.email}"
}

# ==========================================
# 2. BigQuery データセットの作成 (IaC網羅性の向上)
# ==========================================

resource "google_bigquery_dataset" "dwh_prod" {
dataset_id    = "dwh_prod"
friendly_name = "DWH Production Dataset"
description   = "Dataset for fraud detection data pipeline"
location      = var.region
}

# ==========================================
# 3. Cloud Run Jobs
# ==========================================
resource "google_cloud_run_v2_job" "daily_ingest" {
  name     = "daily-ingest-job"
  location = var.region

  template {
    template {
      service_account = google_service_account.run_jobs_sa.email
      containers {
        # 事前にビルド・プッシュしたイメージを指定
        image = "${var.region}-docker.pkg.dev/${var.project_id}/${var.repo_docker}/daily-ingest:latest"
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

# ==========================================
# 4. Workflows
# ==========================================
resource "google_workflows_workflow" "fraud_detection_pipeline" {
  name            = "fraud-detection-pipeline"
  region          = var.region
  description     = "Daily fraud detection ingestion and ML pipeline"
  service_account = google_service_account.workflows_sa.id

  # 上記の YAML 定義ファイルを読み込む
  source_contents = templatefile("${path.module}/workflow.yaml", {
    project_id    = var.project_id
    region        = var.region
    dataform_repo = google_dataform_repository.fraud_pipeline_repo.name
  })

  depends_on = [
    google_cloud_run_v2_job.daily_ingest,
    google_bigquery_dataset.dwh_prod,
    google_dataform_repository.fraud_pipeline_repo
  ]
}

# ==========================================
# 5. Cloud Scheduler (トリガー)
# ==========================================
resource "google_cloud_scheduler_job" "daily_trigger" {
  name        = "daily-fraud-pipeline-trigger"
  region      = var.region
  description = "Trigger workflows daily at 2:00 AM JST"
  schedule    = "0 2 * * *"
  time_zone   = "Asia/Tokyo"

  http_target {
    http_method = "POST"
    uri         = "https://workflowexecutions.googleapis.com/v1/projects/${var.project_id}/locations/${var.region}/workflows/${google_workflows_workflow.fraud_detection_pipeline.name}/executions"

    oauth_token {
      service_account_email = google_service_account.workflows_sa.email
    }
  }
}