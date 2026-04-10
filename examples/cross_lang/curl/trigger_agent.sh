#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
AGENT_ID="${AGENT_ID:-demo-agent}"
MESSAGE="${MESSAGE:-hello from curl}"
IDEMPOTENCY_KEY="${IDEMPOTENCY_KEY:-idem-${AGENT_ID}}"

curl -sS -X POST "${BASE_URL}/v1/agent/trigger" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: ${IDEMPOTENCY_KEY}" \
  -d "{\"agent_id\":\"${AGENT_ID}\",\"message\":\"${MESSAGE}\"}"

