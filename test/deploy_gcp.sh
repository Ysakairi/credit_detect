#!/usr/bin/env bash
# main 相当のパイプラインを Google Cloud にデプロイする。
# 事前: gcloud auth application-default login（またはサービスアカウントキー）
#       terraform >= 1.5, gcloud CLI
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PROJECT_ID="${GCP_PROJECT_ID:-${GOOGLE_CLOUD_PROJECT:-}}"
REGION="${GCP_REGION:-asia-northeast1}"
AR_REPO="${GCP_AR_REPO:-my-repo}"
IMAGE_NAME="daily-ingest"
IMAGE_TAG="${IMAGE_TAG:-latest}"

if [[ -z "$PROJECT_ID" ]]; then
  echo "GCP_PROJECT_ID（または GOOGLE_CLOUD_PROJECT）を設定してください。" >&2
  echo "例: export GCP_PROJECT_ID=skir_sample_credit" >&2
  exit 1
fi

if ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud CLI が見つかりません。https://cloud.google.com/sdk/docs/install" >&2
  exit 1
fi
if ! command -v terraform >/dev/null 2>&1; then
  echo "terraform が見つかりません。https://developer.hashicorp.com/terraform/install" >&2
  exit 1
fi

gcloud config set project "$PROJECT_ID"

echo "==> API 有効化"
gcloud services enable \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  bigquery.googleapis.com \
  run.googleapis.com \
  workflows.googleapis.com \
  cloudscheduler.googleapis.com \
  dataform.googleapis.com \
  iam.googleapis.com \
  secretmanager.googleapis.com \
  iamcredentials.googleapis.com

echo "==> Artifact Registry"
if ! gcloud artifacts repositories describe "$AR_REPO" --location="$REGION" >/dev/null 2>&1; then
  gcloud artifacts repositories create "$AR_REPO" \
    --repository-format=docker \
    --location="$REGION" \
    --description="Repository for fraud detection pipeline"
fi

IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/${IMAGE_NAME}:${IMAGE_TAG}"
echo "==> Cloud Build: $IMAGE"
gcloud builds submit "$ROOT/batch_app" --tag "$IMAGE"

echo "==> Terraform apply"
TF_DIR="$ROOT/terraform"
terraform -chdir="$TF_DIR" init -input=false
terraform -chdir="$TF_DIR" apply -input=false -auto-approve \
  -var="project_id=${PROJECT_ID}" \
  -var="region=${REGION}" \
  -var="repo_docker=${AR_REPO}" \
  ${DATAFORM_GITHUB_TOKEN_SECRET:+-var="dataform_github_token_secret=${DATAFORM_GITHUB_TOKEN_SECRET}"} \
  ${DATAFORM_GIT_URL:+-var="dataform_git_url=${DATAFORM_GIT_URL}"}

echo
echo "デプロイ完了。"
echo "次の手順:"
echo "  1. dataform.json / workflow_settings.yaml の defaultProject を ${PROJECT_ID} と一致させる"
echo "  2. Dataform を GitHub 連携している場合は compilation が gitCommitish=main を取れること"
echo "  3. 初回のみ Dataform タグ initial_setup を実行して BQML モデルを作成する"
echo "  4. ./test/run_integration_tests.sh で結合試験"
echo "  5. 日次パイプライン全体は RUN_WORKFLOW=1 または Cloud Scheduler 02:00 JST"
