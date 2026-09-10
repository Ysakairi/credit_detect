#!/usr/bin/env bash
# 結合試験ランナー（要 GCP 認証）
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p test/results

PYTHON="${PYTHON:-python3}"
"$PYTHON" -m pip install -q -r test/requirements-integration.txt

if [[ -z "${GCP_PROJECT_ID:-}" && -z "${GOOGLE_CLOUD_PROJECT:-}" ]]; then
  echo "GCP_PROJECT_ID または GOOGLE_CLOUD_PROJECT を設定してください。" | tee test/results/integration_latest.txt
  echo "認証が無い場合は項目が SKIP され、終了コード 2 になります。" | tee -a test/results/integration_latest.txt
fi

set +e
{
  echo "credit_detect integration tests"
  echo "GCP_PROJECT_ID=${GCP_PROJECT_ID:-}"
  echo "GOOGLE_CLOUD_PROJECT=${GOOGLE_CLOUD_PROJECT:-}"
  echo "GCP_REGION=${GCP_REGION:-asia-northeast1}"
  echo
  "$PYTHON" -m unittest discover -s test/integration -p 'test_*.py' -v
} 2>&1 | tee test/results/integration_latest.txt
status=${PIPESTATUS[0]}
set -e

if [[ $status -ne 0 ]]; then
  exit "$status"
fi

if grep -q "skipped=" test/results/integration_latest.txt || grep -q "^SKIP" test/results/integration_latest.txt; then
  if ! grep -Eq "FAILED|ERROR" test/results/integration_latest.txt; then
    # 認証なしで全 SKIP のときは結合未実施
    if grep -q "GCP 認証" test/results/integration_latest.txt || grep -q "未設定" test/results/integration_latest.txt; then
      exit 2
    fi
  fi
fi

exit 0
