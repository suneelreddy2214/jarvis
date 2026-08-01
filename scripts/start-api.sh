#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=backend
exec python3 -m uvicorn quantx.api.main:app --host 0.0.0.0 --port 8000 --reload
