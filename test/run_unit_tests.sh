#!/usr/bin/env bash
# 単体試験ランナー（GCP 不要）
set -euo pipefail

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

"$PYTHON" -m pip install -q -r test/requirements-unit.txt
"$PYTHON" -m pip install -q -r agent/requirements.txt

{
  echo "credit_detect unit tests"
  echo "cwd=$ROOT"
  echo "python=$("$PYTHON" --version 2>&1)"
  echo "python_bin=$PYTHON"
  echo
  "$PYTHON" -m unittest discover -s test/unit -p 'test_*.py' -v
  echo
  PYTHONPATH="$ROOT:$ROOT/evaluate" "$PYTHON" -m unittest discover -s agent/tests -p 'test_*.py' -v
} 2>&1 | tee test/results/unit_latest.txt
