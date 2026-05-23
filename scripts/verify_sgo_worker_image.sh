#!/usr/bin/env bash
# Verify the local or built SGO worker image contains namespace-default fixes.
# Usage:
#   ./scripts/verify_sgo_worker_image.sh                    # check repo source
#   ./scripts/verify_sgo_worker_image.sh linkedin-sgo-worker:latest  # check image

set -euo pipefail

IMAGE="${1:-}"
VENDORED="app/services/linkedin_sgo_pipeline_service_async/vendored_sgo/linkedin/tier1_delta_method_predictions.py"
CONFIG="app/services/linkedin_sgo_pipeline_service_async/linkedin_sgo_pipeline_async_config.py"

check_file() {
  local path="$1"
  local kind="${2:-vendored}"
  if grep -q 'getattr(args, "group_summary_max_words"' "$path" 2>/dev/null; then
    echo "OK  $path has getattr default for group_summary_max_words"
  elif [[ "$kind" == "vendored" ]]; then
    echo "FAIL $path still uses bare args.group_summary_max_words — rebuild required"
    return 1
  fi
  if [[ "$kind" == "config" ]]; then
    grep -q '_NAMESPACE_OPTIONAL_DEFAULTS' "$path" \
      && echo "OK  $path has _NAMESPACE_OPTIONAL_DEFAULTS" \
      || { echo "FAIL $path missing _NAMESPACE_OPTIONAL_DEFAULTS"; return 1; }
  fi
}

if [[ -n "$IMAGE" ]]; then
  echo "Checking Docker image: $IMAGE"
  tmp="$(mktemp)"
  docker run --rm --entrypoint cat "$IMAGE" "/app/$VENDORED" > "$tmp"
  check_file "$tmp"
  docker run --rm --entrypoint cat "$IMAGE" "/app/$CONFIG" | grep -q '_NAMESPACE_OPTIONAL_DEFAULTS' \
    && echo "OK  image config has namespace defaults" \
    || { echo "FAIL image config missing namespace defaults"; exit 1; }
  rm -f "$tmp"
else
  repo_root="$(cd "$(dirname "$0")/.." && pwd)"
  check_file "$repo_root/$VENDORED" vendored
  check_file "$repo_root/$CONFIG" config
fi

echo "Image/source verification passed."
