#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
RUN_ID="${RUN_ID:-demo-run-id}"

curl -sS "${BASE_URL}/v1/agent/status/${RUN_ID}"

