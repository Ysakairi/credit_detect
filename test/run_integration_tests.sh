#!/usr/bin/env bash
# 結合試験ランナー（要 GCP 認証）
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p test/results

# Debian/Ubuntu の PEP 668 (externally-managed-environment) では
# システム python3 への pip install が拒否される。PYTHON 未指定時は
# test/.venv を作り、その中へ依存を入れる。
if [[ -z "${PYTHON:-}" ]]; then
  VENV="$ROOT/test/.venv"
  if [[ ! -x "$VENV/bin/python" ]]; then
    if ! python3 -m venv "$VENV"; then
      echo "error: python3 -m venv に失敗しました。Ubuntu では次を入れてください:" >&2
      echo "  sudo apt install python3-venv python3.12-venv python3-full" >&2
      exit 1
    fi
  fi
  PYTHON="$VENV/bin/python"
fi

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
  echo "python_bin=$PYTHON"
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
