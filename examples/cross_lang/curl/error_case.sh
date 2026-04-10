#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"

# Invalid payload to verify protocol error model handling.
curl -sS -X POST "${BASE_URL}/v1/agent/trigger" \
  -H "Content-Type: application/json" \
  -d '{"invalid":"payload"}'

