#!/usr/bin/env bash
# Sync scripts/scripts_sgo → app/services/.../vendored_sgo, then restore production-only layers:
#   - _paths.py (repo root for vendored layout)
#   - tier1_delta_method_predictions.py + tier1_sgo_feedback_loop.py patches (resume hooks, _paths import)
#
# Does not use rsync --delete so vendored-only prompts and files stay unless you remove them manually.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${ROOT}/scripts/scripts_sgo"
DEST="${ROOT}/app/services/linkedin_sgo_pipeline_service_async/vendored_sgo"
PATCH="${ROOT}/app/services/linkedin_sgo_pipeline_service_async/vendored_patches"

if [[ ! -d "$SRC" ]]; then
  echo "Missing source tree: $SRC" >&2
  exit 1
fi
mkdir -p "$DEST"

rsync -a \
  --exclude '__pycache__/' \
  --exclude 'outputs/' \
  --exclude 'outputs_one/' \
  "${SRC}/" "${DEST}/"

install -m0644 "${PATCH}/_paths.py" "${DEST}/_paths.py"

for p in tier1_delta_method_predictions.patch tier1_sgo_feedback_loop.patch; do
  echo "Applying ${p}..."
  (cd "$DEST" && patch -p0 --batch --forward < "${PATCH}/${p}")
done

echo "Done. Re-run tests / smoke-import vendored modules if patches failed (conflicts mean scripts drifted)."
