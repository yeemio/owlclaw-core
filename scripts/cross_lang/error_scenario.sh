#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${OWLCLAW_GATEWAY_BASE_URL:-http://localhost:8000}"

# Intentionally send an invalid payload to verify error contract shape.
curl -sS -X POST "${BASE_URL}/api/v1/triggers" \
  -H "Content-Type: application/json" \
  -d '{"invalid": true}'
