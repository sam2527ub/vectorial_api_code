#!/usr/bin/env bash
# Local ground-truth extraction (per-topic Yes/No + logprobs).
# Usage:
#   ./run_ground_truth_local.sh tier1
#   ./run_ground_truth_local.sh tier2
# Optional: GROUND_TRUTH_LOCAL_CONFIG=/path/to.yaml  OPENAI_MODEL=gpt-4o-mini

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"
PRESET="${1:-tier1}"
shift || true
exec python3 scripts/LInkedin_Category_Topic_Extraction/post_topic_classification/contextual_ground_truth_extraction.py \
  --preset "$PRESET" "$@"
