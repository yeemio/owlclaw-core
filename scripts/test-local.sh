#!/usr/bin/env bash
# Local test runner for OwlClaw.
# Usage:
#   ./scripts/test-local.sh [--unit-only] [--keep-up]

set -euo pipefail

UNIT_ONLY=false
KEEP_UP=false

for arg in "$@"; do
  case "$arg" in
    --unit-only) UNIT_ONLY=true ;;
    --keep-up) KEEP_UP=true ;;
    *)
      echo "Unknown option: $arg"
      echo "Usage: ./scripts/test-local.sh [--unit-only] [--keep-up]"
      exit 2
      ;;
  esac
done

cleanup() {
  if [[ "$KEEP_UP" == "false" ]]; then
    docker compose -f docker-compose.test.yml down || true
  fi
}
trap cleanup EXIT

docker compose -f docker-compose.test.yml up -d

if [[ "$UNIT_ONLY" == "true" ]]; then
  poetry run pytest tests/unit/ -q
else
  poetry run pytest tests/unit/ tests/integration/ -q
fi
