#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${OWLCLAW_GATEWAY_BASE_URL:-http://localhost:8000}"
TOKEN="${OWLCLAW_API_TOKEN:-}"
RUN_ID="${1:-}"

if [[ -z "${RUN_ID}" ]]; then
  echo "Usage: $0 <run_id>"
  exit 1
fi

curl -sS "${BASE_URL}/api/v1/runs/${RUN_ID}" \
  -H "Authorization: Bearer ${TOKEN}"
