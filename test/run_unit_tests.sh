#!/usr/bin/env bash
# 単体試験ランナー（GCP 不要）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p test/results

PYTHON="${PYTHON:-python3}"
"$PYTHON" -m pip install -q -r test/requirements-unit.txt

{
  echo "credit_detect unit tests"
  echo "cwd=$ROOT"
  echo "python=$("$PYTHON" --version 2>&1)"
  echo
  "$PYTHON" -m unittest discover -s test/unit -p 'test_*.py' -v
} 2>&1 | tee test/results/unit_latest.txt
