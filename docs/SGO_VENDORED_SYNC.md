# LinkedIn SGO: scripts mirror vs production (vendored)

## Source of truth

| Environment | Path |
|-------------|------|
| **Edit here** | `scripts/scripts_sgo/` |
| **Production runtime** | `app/services/linkedin_sgo_pipeline_service_async/vendored_sgo/` |
| **Docker / Fargate image** | `app/` only (includes `vendored_sgo`; does **not** copy `scripts/`) |

## After any change to prompts or pipeline code

```bash
bash scripts/sync_linkedin_sgo_vendored.sh
python3 scripts/verify_sgo_vendored_parity.py
docker build -f docker/sgo-worker/Dockerfile -t linkedin-sgo-worker:latest .
```

## Tier order (default: tier1 → tier2)

1. **Tier1** SGO runs all outer iterations → promotes artifacts to S3, including `linkedin_sgo_evolution_state_tier1.json`.
2. **Tier2** loads tier1 evolution from S3 into the workdir, then runs tier2 iterations (same code path as local `outputs_okayish/linkedin_tier2_sgo/my_run` seeded from tier1).

Fargate: `TIER_MODE=both` (default).  
API: `POST .../fargate/start?tierMode=both`.

## Production readiness checklist

- [ ] `verify_sgo_vendored_parity.py` passes after every scripts_sgo change
- [ ] Room has S3 inputs for both tiers (stimulus, mapping, i0 per tier) before SGO
- [ ] Secrets on ECS task: `OPENAI_API_KEY`, DB URLs, `AUDIENCE_BUCKET_NAME`, `AUDIENCE_WORKFLOW_ROOT=/app`
- [ ] Fargate env: `TIER_MODE=both`, `NUM_ITERATIONS=5` (or override)
- [ ] Smoke test: tier1 COMPLETED → tier2 COMPLETED on one room
- [ ] Poll `GET .../async/status/{job_id}` per tier job id from webhook `tier_results`
