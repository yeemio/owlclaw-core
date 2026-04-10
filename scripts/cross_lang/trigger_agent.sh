#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${OWLCLAW_GATEWAY_BASE_URL:-http://localhost:8000}"
TOKEN="${OWLCLAW_API_TOKEN:-}"

curl -sS -X POST "${BASE_URL}/api/v1/triggers" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${TOKEN}" \
  -d '{
    "trigger_type": "manual",
    "event": "cross_lang_smoke",
    "context": {"source": "curl"}
  }'
