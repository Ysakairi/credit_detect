#!/usr/bin/env bash
# 既存 GCP リソースを Terraform state に取り込む。
# terraform.tfstate が無い clone / 別ディレクトリから apply すると 409 alreadyExists になる。
# このスクリプトは state に無いアドレスだけ import する（destroy はしない）。
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -f terraform.tfvars ]]; then
  echo "terraform/terraform.tfvars がありません。README ④ を先に用意してください。" >&2
  exit 1
fi

PROJECT_ID="$(python3 - <<'PY'
import re
text = open("terraform.tfvars", encoding="utf-8").read()
m = re.search(r'(?m)^project_id\s*=\s*"([^"]+)"', text)
if not m:
    raise SystemExit("terraform.tfvars に project_id がありません")
print(m.group(1))
PY
)"
REGION="$(python3 - <<'PY'
import re
text = open("terraform.tfvars", encoding="utf-8").read()
m = re.search(r'(?m)^region\s*=\s*"([^"]+)"', text)
print(m.group(1) if m else "asia-northeast1")
PY
)"

PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
RUN_SA="serviceAccount:sa-run-jobs-executor@${PROJECT_ID}.iam.gserviceaccount.com"
WF_SA="serviceAccount:sa-workflows-orchestrator@${PROJECT_ID}.iam.gserviceaccount.com"
SCH_SA="serviceAccount:sa-scheduler-trigger@${PROJECT_ID}.iam.gserviceaccount.com"
DF_SA="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-dataform.iam.gserviceaccount.com"
TF_VAR=(-var-file=terraform.tfvars)

in_state() {
  terraform state show "$1" >/dev/null 2>&1
}

import_one() {
  local addr="$1"
  local id="$2"
  if in_state "$addr"; then
    echo "skip (already in state): ${addr}"
    return
  fi
  echo "import ${addr}"
  terraform import "${TF_VAR[@]}" "$addr" "$id"
}

echo "project_id=${PROJECT_ID} region=${REGION}"
echo "state にあるリソース:"
terraform state list || true
echo

for api in \
  bigquery.googleapis.com \
  run.googleapis.com \
  workflows.googleapis.com \
  cloudscheduler.googleapis.com \
  dataform.googleapis.com \
  iam.googleapis.com \
  secretmanager.googleapis.com
do
  import_one "google_project_service.apis[\"${api}\"]" "${PROJECT_ID}/${api}"
done

import_one google_project_service_identity.dataform \
  "projects/${PROJECT_ID}/services/dataform.googleapis.com"

import_one google_service_account.run_jobs_sa \
  "projects/${PROJECT_ID}/serviceAccounts/sa-run-jobs-executor@${PROJECT_ID}.iam.gserviceaccount.com"
import_one google_service_account.workflows_sa \
  "projects/${PROJECT_ID}/serviceAccounts/sa-workflows-orchestrator@${PROJECT_ID}.iam.gserviceaccount.com"
import_one google_service_account.scheduler_sa \
  "projects/${PROJECT_ID}/serviceAccounts/sa-scheduler-trigger@${PROJECT_ID}.iam.gserviceaccount.com"

import_one google_bigquery_dataset.dwh_prod \
  "projects/${PROJECT_ID}/datasets/dwh_prod"

import_one google_bigquery_dataset_iam_member.run_jobs_bq_editor \
  "projects/${PROJECT_ID}/datasets/dwh_prod roles/bigquery.dataEditor ${RUN_SA}"
import_one google_project_iam_member.run_jobs_bq_job_user \
  "${PROJECT_ID} roles/bigquery.jobUser ${RUN_SA}"
import_one google_project_iam_member.scheduler_workflows_invoker \
  "${PROJECT_ID} roles/workflows.invoker ${SCH_SA}"
import_one google_project_iam_member.workflows_dataform_editor \
  "${PROJECT_ID} roles/dataform.editor ${WF_SA}"
import_one google_bigquery_dataset_iam_member.dataform_bq_editor \
  "projects/${PROJECT_ID}/datasets/dwh_prod roles/bigquery.dataEditor ${DF_SA}"
import_one google_project_iam_member.dataform_bq_job_user \
  "${PROJECT_ID} roles/bigquery.jobUser ${DF_SA}"
import_one google_project_iam_member.dataform_bq_data_viewer \
  "${PROJECT_ID} roles/bigquery.dataViewer ${DF_SA}"

import_one google_cloud_run_v2_job.daily_ingest \
  "projects/${PROJECT_ID}/locations/${REGION}/jobs/daily-ingest-job"
import_one google_cloud_run_v2_job_iam_member.workflows_job_developer \
  "projects/${PROJECT_ID}/locations/${REGION}/jobs/daily-ingest-job roles/run.developer ${WF_SA}"

import_one google_dataform_repository.fraud_pipeline_repo \
  "projects/${PROJECT_ID}/locations/${REGION}/repositories/fraud-pipeline-repo"

import_one google_workflows_workflow.fraud_detection_pipeline \
  "projects/${PROJECT_ID}/locations/${REGION}/workflows/fraud-detection-pipeline"

import_one google_cloud_scheduler_job.daily_trigger \
  "projects/${PROJECT_ID}/locations/${REGION}/jobs/daily-fraud-pipeline-trigger"

echo
echo "import 完了。次:"
echo "  terraform plan -var-file=terraform.tfvars"
echo "  terraform apply -var-file=terraform.tfvars"
echo "plan で Workflow の source_contents 更新だけなら、Job / Dataform は再作成されません。"
