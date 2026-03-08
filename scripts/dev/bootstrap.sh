#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-/opt/homebrew/bin/python3.11}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python 3.11 not found at $PYTHON_BIN"
  echo "Install with: brew install python@3.11"
  exit 1
fi

"$PYTHON_BIN" -m venv --clear .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements/lock/base.lock -r requirements/lock/dev.lock

echo "Environment ready: $(python --version)"
