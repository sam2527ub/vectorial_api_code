#!/usr/bin/env bash
# Sync scripts/scripts_sgo → app/services/.../vendored_sgo (production mirror).
#
# Source of truth: scripts/scripts_sgo (prompts + linkedin pipeline code).
# Production loads: app/.../vendored_sgo via linkedin_sgo_pipeline_async_runner.py
#
# Usage: bash scripts/sync_linkedin_sgo_vendored.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${ROOT}/scripts/scripts_sgo"
DEST="${ROOT}/app/services/linkedin_sgo_pipeline_service_async/vendored_sgo"

if [[ ! -d "$SRC" ]]; then
  echo "Missing source tree: $SRC" >&2
  exit 1
fi
mkdir -p "$DEST"

RSYNC_EXCLUDES=(
  --exclude '__pycache__/'
  --exclude '*.pyc'
  --exclude '*.rej'
  --exclude '*.orig'
  --exclude 'outputs/'
  --exclude 'outputs_*/'
)

SYNC_DIRS=(
  config
  llm
  utils
  calculate_behaviour_loss
  feedback_loop
  generate_synthetic_review_and_memory_analysis
  sgo_training
  prompts
)

echo "Syncing package dirs (rsync --delete)..."
for d in "${SYNC_DIRS[@]}"; do
  if [[ -d "${SRC}/${d}" ]]; then
    mkdir -p "${DEST}/${d}"
    rsync -a --delete "${RSYNC_EXCLUDES[@]}" "${SRC}/${d}/" "${DEST}/${d}/"
  fi
done

echo "Syncing linkedin/ (production modules only)..."
mkdir -p "${DEST}/linkedin"
rsync -a --delete "${RSYNC_EXCLUDES[@]}" \
  "${SRC}/linkedin/tier1_delta_method_predictions.py" \
  "${SRC}/linkedin/tier1_sgo_feedback_loop.py" \
  "${SRC}/linkedin/i0_audience_room_helpers.py" \
  "${SRC}/linkedin/i0_linkedin_context.py" \
  "${SRC}/linkedin/i0_initial_prediction.py" \
  "${SRC}/linkedin/__init__.py" \
  "${DEST}/linkedin/"

# Remove dev-only / stale files from vendored linkedin/
rm -f "${DEST}/linkedin/"*FULL_BACKUP* 2>/dev/null || true
rm -f "${DEST}/linkedin/run_linkedin_"*.py 2>/dev/null || true
rm -f "${DEST}/linkedin/linkedin_initial_prediction"*.py 2>/dev/null || true
rm -f "${DEST}/linkedin/aggregate_iteration_stats_from_traces.py" 2>/dev/null || true

install -m0644 "${SRC}/_package_setup.py" "${DEST}/_package_setup.py"
install -m0644 "${SRC}/_paths.py" "${DEST}/_paths.py"
install -m0644 "${SRC}/sgo_errors.py" "${DEST}/sgo_errors.py"

# Purge accidental output trees from vendored (never used in production)
find "${DEST}" -maxdepth 1 -type d -name 'outputs*' -exec rm -rf {} + 2>/dev/null || true
rm -rf "${DEST}/__pycache__" 2>/dev/null || true

echo "Done. Verify: python3 scripts/verify_sgo_vendored_parity.py"
