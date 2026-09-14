# Agent PoC: Vertex AI, Cloud Storage, Cloud Run UI, knowledge table, IAM.
# Existing pipeline resources stay in main.tf. This file is additive.

variable "enable_agent" {
  description = "Provision Autonomous Fraud Investigation Agent resources"
  type        = bool
  default     = true
}

variable "enable_agent_cloud_run" {
  description = "Create the Streamlit Cloud Run service (requires fraud-agent-ui image)"
  type        = bool
  default     = false
}

variable "agent_ui_unauthenticated" {
  description = "If true, allow unauthenticated access to the Streamlit demo"
  type        = bool
  default     = false
}

locals {
  agent_staging_bucket = "${var.project_id}-fraud-agent-staging"
  agent_apis = [
    "aiplatform.googleapis.com",
    "storage.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
  ]
}

resource "google_project_service" "agent_apis" {
  for_each           = var.enable_agent ? toset(local.agent_apis) : toset([])
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

data "google_project" "current" {
  project_id = var.project_id
}

# Vertex AI Agent Engine (Reasoning Engine) Google-managed SA
locals {
  agent_engine_sa = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"
}

resource "google_storage_bucket" "agent_staging" {
  count                       = var.enable_agent ? 1 : 0
  name                        = local.agent_staging_bucket
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = true
  depends_on                  = [google_project_service.agent_apis]
}

resource "google_service_account" "agent_runtime" {
  count        = var.enable_agent ? 1 : 0
  account_id   = "sa-fraud-agent"
  display_name = "Fraud investigation agent (Cloud Run / local Vertex calls)"
  depends_on   = [google_project_service.agent_apis]
}

resource "google_project_iam_member" "agent_runtime_roles" {
  for_each = var.enable_agent ? toset([
    "roles/aiplatform.user",
    "roles/bigquery.jobUser",
    "roles/bigquery.dataViewer",
  ]) : toset([])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.agent_runtime[0].email}"
}

resource "google_bigquery_dataset_iam_member" "agent_runtime_editor" {
  count      = var.enable_agent ? 1 : 0
  dataset_id = google_bigquery_dataset.dwh_prod.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.agent_runtime[0].email}"
}

resource "google_storage_bucket_iam_member" "agent_runtime_staging" {
  count  = var.enable_agent ? 1 : 0
  bucket = google_storage_bucket.agent_staging[0].name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.agent_runtime[0].email}"
}

resource "google_project_iam_member" "agent_engine_roles" {
  for_each = var.enable_agent ? toset([
    "roles/aiplatform.user",
    "roles/bigquery.jobUser",
  ]) : toset([])
  project = var.project_id
  role    = each.value
  member  = local.agent_engine_sa
}

resource "google_bigquery_dataset_iam_member" "agent_engine_editor" {
  count      = var.enable_agent ? 1 : 0
  dataset_id = google_bigquery_dataset.dwh_prod.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = local.agent_engine_sa
}

resource "google_storage_bucket_iam_member" "agent_engine_staging" {
  count  = var.enable_agent ? 1 : 0
  bucket = google_storage_bucket.agent_staging[0].name
  role   = "roles/storage.objectAdmin"
  member = local.agent_engine_sa
}

resource "google_bigquery_table" "fraud_investigation_knowledge" {
  count      = var.enable_agent ? 1 : 0
  dataset_id = google_bigquery_dataset.dwh_prod.dataset_id
  table_id   = "fraud_investigation_knowledge"
  deletion_protection = false

  schema = jsonencode([
    { name = "doc_id", type = "STRING", mode = "REQUIRED" },
    { name = "category", type = "STRING", mode = "NULLABLE" },
    { name = "title", type = "STRING", mode = "NULLABLE" },
    { name = "content", type = "STRING", mode = "NULLABLE" },
    { name = "source_uri", type = "STRING", mode = "NULLABLE" },
    { name = "embedding", type = "FLOAT64", mode = "REPEATED" }
  ])
}

resource "google_cloud_run_v2_service" "agent_ui" {
  count    = var.enable_agent && var.enable_agent_cloud_run ? 1 : 0
  name     = "fraud-agent-ui"
  location = var.region
  # deletion_protection は google provider 6.0 で追加。本構成は ~> 5.0 のため指定しない。
  depends_on = [google_project_service.agent_apis]

  template {
    service_account = google_service_account.agent_runtime[0].email
    timeout         = "300s"
    max_instance_request_concurrency = 10

    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }

    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/${var.repo_docker}/fraud-agent-ui:latest"
      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
      }
      env {
        name  = "PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "LOCATION"
        value = var.region
      }
      env {
        name  = "DATASET_ID"
        value = google_bigquery_dataset.dwh_prod.dataset_id
      }
      env {
        name  = "AGENT_BACKEND"
        value = "local"
      }
      env {
        name  = "MODEL_NAME"
        value = "gemini-2.0-flash"
      }
      env {
        name  = "STAGING_BUCKET"
        value = "gs://${local.agent_staging_bucket}"
      }
    }
  }
}

resource "google_cloud_run_v2_service_iam_member" "agent_ui_invoker_self" {
  count    = var.enable_agent && var.enable_agent_cloud_run ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.agent_ui[0].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.agent_runtime[0].email}"
}

resource "google_cloud_run_v2_service_iam_member" "agent_ui_public" {
  count    = var.enable_agent && var.enable_agent_cloud_run && var.agent_ui_unauthenticated ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.agent_ui[0].name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

output "agent_staging_bucket" {
  value       = var.enable_agent ? google_storage_bucket.agent_staging[0].url : null
  description = "GCS bucket for Agent Engine staging"
}

output "agent_runtime_sa" {
  value       = var.enable_agent ? google_service_account.agent_runtime[0].email : null
  description = "Service account used by Cloud Run UI and local Vertex calls"
}

output "knowledge_table" {
  value       = var.enable_agent ? "${var.project_id}.${google_bigquery_dataset.dwh_prod.dataset_id}.fraud_investigation_knowledge" : null
}

output "agent_ui_uri" {
  value       = var.enable_agent && var.enable_agent_cloud_run ? google_cloud_run_v2_service.agent_ui[0].uri : null
}
