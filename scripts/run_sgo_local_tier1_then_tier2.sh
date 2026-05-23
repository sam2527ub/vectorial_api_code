#!/usr/bin/env bash
# Alias for tier1 → tier2 local run (see run_sgo_local.sh).
exec "$(dirname "$0")/run_sgo_local.sh" "$@"
