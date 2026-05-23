#!/usr/bin/env bash
# Run tier1 → tier2 SGO locally (no Vercel, no Fargate).
#
# Jobs are created in Postgres via your local .env (BETA_DATABASE_URL, etc.).
# Tier2 seeds from tier1 evolution on S3 after tier1 completes.
#
# Prereqs in .env:
#   OPENAI_API_KEY, BETA_DATABASE_URL, AUDIENCE_BUCKET_NAME, AWS_* 
#
# Usage:
#   export AUDIENCE_ROOM_ID=2d164132-c86e-455e-924b-ee0affda78c3
#   export NUM_ITERATIONS=1          # start with 1 for debugging
#   ./scripts/run_sgo_local.sh

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

export AUDIENCE_ROOM_ID="${AUDIENCE_ROOM_ID:?Set AUDIENCE_ROOM_ID}"
export ENTERPRISE_NAME="${ENTERPRISE_NAME:-beta}"
export TIER_MODE="${TIER_MODE:-both}"
export NUM_ITERATIONS="${NUM_ITERATIONS:-1}"
export NOTIFY_WEBHOOK=false

# Job IDs optional: worker creates tier1+tier2 jobs in DB when unset.
unset JOB_ID
if [[ -z "${TIER1_JOB_ID:-}" || -z "${TIER2_JOB_ID:-}" ]]; then
  unset TIER1_JOB_ID TIER2_JOB_ID
  echo "No job IDs set — worker will create tier1 + tier2 jobs locally."
fi

echo "Local SGO: room=$AUDIENCE_ROOM_ID enterprise=$ENTERPRISE_NAME tier_mode=$TIER_MODE iterations=$NUM_ITERATIONS"
exec python3 run_sgo_worker.py
