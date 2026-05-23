# LinkedIn SGO on AWS Fargate

Long-running SGO training runs on **AWS ECS Fargate** instead of Vercel serverless HTTP chunks. The API (Vercel) only creates the job, starts the task, and handles the webhook—same pattern as **Apify** (external worker) + **poll/webhook** completion.

## Flow

```text
Vercel FastAPI                    AWS Fargate                      Vercel FastAPI
     |                                 |                                |
     | POST .../fargate/start          |                                |
     | (or .../async?externalWorker=   |                                |
     |  fargate)                       |                                |
     |----------------RunTask-------->|                                |
     |<------- job_id (immediate) ------|                                |
     |                                 | run_sgo_worker.py              |
     |                                 | (all SGO chunks, S3 checkpoints)|
     |                                 |----------------POST webhook--->|
     |                                 |                                | resume workflow
```

## API endpoints

| Method | Path | Role |
|--------|------|------|
| `POST` | `/api/v1/audience-rooms/{id}/linkedin-sgo-pipeline/fargate/start` | Create job + launch Fargate |
| `POST` | `/api/v1/audience-rooms/{id}/linkedin-sgo-pipeline/async?externalWorker=fargate` | Same (workflow-friendly alias) |
| `GET` | `/api/v1/audience-rooms/{id}/linkedin-sgo-pipeline/fargate/pipeline-status` | Poll tier1+tier2 rollup |
| `GET` | `/api/v1/audience-rooms/{id}/linkedin-sgo-pipeline/fargate/resume-status` | Check if failed jobs can resume from S3 |
| `POST` | `/api/v1/audience-rooms/{id}/linkedin-sgo-pipeline/fargate/resume` | Re-launch Fargate on existing job ids (checkpoint resume) |
| `POST` | `/api/v1/sgo/fargate/webhook` | Optional worker callback (`notifyWebhook=true`) |
| `GET` | `/api/v1/audience-rooms/{id}/linkedin-sgo-pipeline/async/status/{job_id}` | Poll single job |

### Start (Fargate, poll-only — default)

```bash
curl -sS -X POST \
  "https://vectorial-api-code.vercel.app/api/v1/audience-rooms/{ROOM_ID}/linkedin-sgo-pipeline/fargate/start?enterpriseName=beta&tierMode=both&numIterations=5"
```

Response includes `tier_results[].job_id`, `tier_results[].poll`, and `poll_pipeline` for tier1→tier2.

Poll rollup:

```bash
curl -sS "https://vectorial-api-code.vercel.app/api/v1/audience-rooms/{ROOM_ID}/linkedin-sgo-pipeline/fargate/pipeline-status?tier1JobId={T1}&tier2JobId={T2}&enterpriseName=beta"
```

Stop when `pipeline_status` is `COMPLETED` or `FAILED`. Webhook is **off** unless `notifyWebhook=true`.

### Resume from checkpoint (after failure)

If a run failed mid-tier but wrote S3 checkpoints (partial or completed outer iteration), reuse the **same** `tier1JobId` / `tier2JobId` from the original start response:

Check eligibility:

```bash
curl -sS ".../fargate/resume-status?tier1JobId={T1}&tier2JobId={T2}&enterpriseName=beta"
```

Resume (re-launch Fargate; no new DB jobs):

```bash
curl -sS -X POST ".../fargate/resume?tier1JobId={T1}&tier2JobId={T2}&enterpriseName=beta"
```

Poll `pipeline-status` as usual. Checkpoints live under
`{enterprise}/linkedin-audience/{roomId}/tiered_posts/linkedin_sgo/checkpoints/{job_id}/`.

Rebuild/push the Docker image after fixing pipeline bugs before resuming.

### Start with webhook (optional)

```bash
curl -sS -X POST \
  ".../fargate/start?...&notifyWebhook=true"
```

## Configuration

### `config/runtime.yaml`

```yaml
sgo_fargate:
  enabled: true
  ecs_cluster: "vectorial-ai-cluster"
  task_definition: "sgo-worker-task"
  container_name: "sgo-python-container"
  subnet_ids: ["subnet-xxx"]
  security_group_ids: ["sg-xxx"]
  task_role_arn: "arn:aws:iam::...:role/sgo-task-role"
  execution_role_arn: "arn:aws:iam::...:role/sgo-execution-role"
```

### Environment overrides (Vercel)

| Variable | Purpose |
|----------|---------|
| `SGO_FARGATE_ENABLED` | `true` to enable |
| `SGO_FARGATE_ECS_CLUSTER` | ECS cluster name |
| `SGO_FARGATE_TASK_DEFINITION` | Task definition family:revision |
| `SGO_FARGATE_CONTAINER_NAME` | Container name in task def |
| `SGO_FARGATE_SUBNET_IDS` | Comma-separated subnet IDs |
| `SGO_FARGATE_SECURITY_GROUP_IDS` | Comma-separated SG IDs |
| `SGO_FARGATE_TASK_ROLE_ARN` | Task IAM role |
| `SGO_FARGATE_EXECUTION_ROLE_ARN` | Execution IAM role |
| `SGO_FARGATE_WEBHOOK_SECRET` | Shared secret (header `X-SGO-Webhook-Secret`) |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | Vercel user with `ecs:RunTask`, `iam:PassRole` |
| `AUDIENCE_API_BASE_URL` | Public URL for webhook (if not using `VERCEL_URL`) |

Worker container receives: `WEBHOOK_URL`, `AUDIENCE_ROOM_ID`, `TIER_MODE` (default `both` = tier1 then tier2), `NUM_ITERATIONS`, optional `JOB_ID` (single-tier only), `ENTERPRISE_NAME`, `MODEL`, plus existing DB/S3/OpenAI env vars.

**Code parity:** production runs `app/.../vendored_sgo` (synced from `scripts/scripts_sgo`). See `docs/SGO_VENDORED_SYNC.md`. Rebuild the image after `bash scripts/sync_linkedin_sgo_vendored.sh`.

## Docker / ECR

```bash
cd /path/to/Audience-workflow
docker build -f docker/sgo-worker/Dockerfile -t sgo-worker:latest .
# push to ECR; point ECS task definition image at that URI
```

Entrypoint: `run_sgo_worker.py`.

## AWS checklist

1. **ECR** — Push `sgo-worker` image.
2. **ECS task definition** — Enough CPU/memory for LLM work; same env as Vercel API for DB/S3/OpenAI.
3. **IAM (Vercel)** — `ecs:RunTask`, `iam:PassRole` on task + execution roles.
4. **IAM (task role)** — S3 read/write for audience bucket, DB network access if applicable.
5. **Networking** — `assignPublicIp: ENABLED` (or NAT) so Fargate can call OpenAI and the Vercel webhook.

## Same job model as serverless SGO

- `job_id` in `LinkedInRoomPipelineJob`
- S3 checkpoints under `{enterprise}/linkedin-audience/{roomId}/tiered_posts/linkedin_sgo/checkpoints/{job_id}/`
- Failed-job resume rules unchanged when re-running `/async/process` on Vercel (Fargate path runs full loop in one task)
