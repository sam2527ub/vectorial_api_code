# vendored_sgo (production mirror)

**Do not edit this tree directly.** Edit `scripts/scripts_sgo/` and run:

```bash
bash scripts/sync_linkedin_sgo_vendored.sh
python3 scripts/verify_sgo_vendored_parity.py
```

Loaded at runtime by `linkedin_sgo_pipeline_async_runner.py` (Vercel + Fargate).

## Included

- `linkedin/` — delta-method pipeline (`tier1_delta_method_predictions.py`, `tier1_sgo_feedback_loop.py`, i0 helpers)
- `prompts/` — all prompt templates used by the pipeline
- `config/`, `llm/`, `utils/`, `feedback_loop/`, `generate_synthetic_review_and_memory_analysis/`, `sgo_training/`

## Excluded (dev-only, stay in scripts_sgo only)

- `outputs*` run artifacts
- `run_linkedin_*.py`, `linkedin_initial_prediction_*.py`, `*FULL_BACKUP*`
- `main.py` cluster training orchestrator

## Tier1 → tier2

Production Fargate default `TIER_MODE=both`: tier1 completes and uploads `linkedin_sgo_evolution_state_tier1` to S3; tier2 copies that into the workdir before iteration 1 (`prepare_tier2_evolution_baseline_dir_from_s3`).
