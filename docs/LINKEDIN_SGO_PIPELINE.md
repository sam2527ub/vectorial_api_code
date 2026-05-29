# LinkedIn SGO pipeline — full run (steps 1–7) on Vercel

Runs **rebuild → filter → theme → stimulus → ground truth → initial prediction → SGO (Fargate)** via one workflow command. Steps 1–6 are not run manually from this doc.

---

## Base URL (production)

```text
https://vectorial-api-code.vercel.app
```

**Swagger (try APIs in browser):**  
https://vectorial-api-code.vercel.app/docs

**ReDoc:**  
https://vectorial-api-code.vercel.app/redoc

Replace `YOUR_ROOM_ID` with your audience room UUID.  
Replace `beta` with your enterprise if different: `gamma` | `app` | `entelligence` | `beta`.

---

## Where is Vercel? (two apps)

There are **two** Vercel deployments. Do not mix them up.

| What | Vercel URL | Repo / project |
|------|------------|----------------|
| **FastAPI** (steps 1–7 HTTP APIs, Swagger) | https://vectorial-api-code.vercel.app | **This repo** (`Audience-workflow`) |
| **Workflow orchestrator** (one curl → runs many steps) | https://vectorial-sgo-linkedin-testing.vercel.app | Separate app: `apps/vectorial_sgo_linkedin_testing` (not in this repo root) |

- **One command for full pipeline (steps 1–7)** → use `vectorial-sgo-linkedin-testing.vercel.app` (recommended)
- **Step 7 only (Fargate)** → direct curls on `vectorial-api-code` — [Option C](#option-c--direct-fargate-only-step-7)
- **Swagger / debug** → https://vectorial-api-code.vercel.app/docs

Deploy **this API** from this folder:

```bash
cd "/media/samanvitha/Seagate HDD/Audience-workflow"
vercel login
vercel --prod
```

Dashboard: https://vercel.com/dashboard → project linked to `vectorial-api-code`.

---

## Run full pipeline on Vercel (steps 1–7)

The workflow app runs **all** steps: rebuild → filter → theme → stimulus → ground truth → initial prediction → **SGO on Fargate**.

### 1) Deploy workflow app (once)

If you have `apps/vectorial_sgo_linkedin_testing` in your monorepo:

```bash
cd apps/vectorial_sgo_linkedin_testing
vercel login
vercel --prod
```

URL: https://vectorial-sgo-linkedin-testing.vercel.app

Also deploy the API + configure Fargate: [Setup SGO Fargate](#setup-sgo-fargate-required-for-step-7) and `vercel --prod` on this repo.

### 2) Trigger full pipeline

```bash
export ROOM_ID="YOUR_ROOM_ID"
export ENTERPRISE="beta"

curl -sS -X POST "https://vectorial-sgo-linkedin-testing.vercel.app/api/workflows/linkedin-room-pipeline" \
  -H "Content-Type: application/json" \
  -d "{
    \"audienceRoomId\": \"${ROOM_ID}\",
    \"enterpriseName\": \"${ENTERPRISE}\",
    \"startFromStep\": 1,
    \"skipCompletedSteps\": true,
    \"runSgoPipeline\": true
  }"
```

| Field | Value for full run |
|-------|-------------------|
| `audienceRoomId` | Your room UUID |
| `enterpriseName` | `beta` (or your tenant) |
| `startFromStep` | `1` |
| `skipCompletedSteps` | `true` (skip steps already on S3) |
| `runSgoPipeline` | **`true`** (required — includes step 7 Fargate) |

### 3) Monitor progress

Check which steps finished on S3:

```bash
export BASE="https://vectorial-api-code.vercel.app"

curl -sS "${BASE}/api/v1/audience-rooms/${ROOM_ID}/linkedin-room-pipeline/step-status?enterpriseName=${ENTERPRISE}"
```

Each step shows `"complete": true` when its artifact exists. Step 7 completes when Fargate SGO finishes (workflow polls internally; you can also check SGO job status in Swagger if needed).

**Resume full pipeline** after a failure (re-run from a step, still including SGO when step 7 was not done):

```bash
curl -sS -X POST "https://vectorial-sgo-linkedin-testing.vercel.app/api/workflows/linkedin-room-pipeline" \
  -H "Content-Type: application/json" \
  -d "{
    \"audienceRoomId\": \"${ROOM_ID}\",
    \"enterpriseName\": \"${ENTERPRISE}\",
    \"startFromStep\": 5,
    \"skipCompletedSteps\": true,
    \"runSgoPipeline\": true
  }"
```

Change `startFromStep` to the step that failed (e.g. `5`). Keep `runSgoPipeline: true` for a full run through step 7.

### Option C — Direct Fargate only (step 7)

Use when **steps 1–6 are already on S3** and you want to run SGO without the workflow app.

**Prerequisite:** `step-status` shows steps 1–6 `complete: true`. Fargate configured: [Setup SGO Fargate](#setup-sgo-fargate-required-for-step-7).

```bash
export BASE="https://vectorial-api-code.vercel.app"
export ROOM_ID="YOUR_ROOM_ID"
export ENTERPRISE="beta"
```

**Start** (tier1 → tier2 on Fargate):

```bash
curl -sS -X POST \
  "${BASE}/api/v1/audience-rooms/${ROOM_ID}/linkedin-sgo-pipeline/fargate/start?enterpriseName=${ENTERPRISE}&tierMode=both&numIterations=5" \
  -H "Content-Type: application/json" \
  -d '{}'
```

Save `tier1_job_id` and `tier2_job_id` from the response.

**Poll** until `pipeline_status` is `COMPLETED` or `FAILED`:

```bash
export T1_JOB="<tier1_job_id from start>"
export T2_JOB="<tier2_job_id from start>"

curl -sS \
  "${BASE}/api/v1/audience-rooms/${ROOM_ID}/linkedin-sgo-pipeline/fargate/pipeline-status?tier1JobId=${T1_JOB}&tier2JobId=${T2_JOB}&enterpriseName=${ENTERPRISE}"
```

**Resume** after a failed run (same job IDs, S3 checkpoints):

```bash
curl -sS -X POST \
  "${BASE}/api/v1/audience-rooms/${ROOM_ID}/linkedin-sgo-pipeline/fargate/resume?tier1JobId=${T1_JOB}&tier2JobId=${T2_JOB}&enterpriseName=${ENTERPRISE}"
```

**Alias** (same as `fargate/start`):

```text
POST https://vectorial-api-code.vercel.app/api/v1/audience-rooms/YOUR_ROOM_ID/linkedin-sgo-pipeline/async?enterpriseName=beta&externalWorker=fargate&tierMode=both&numIterations=5
```

---

## Setup SGO Fargate (required for step 7)

Step 7 does **not** run on Vercel serverless (it times out). Vercel only **starts** an AWS ECS Fargate task; the worker container runs `run_sgo_worker.py` and writes to S3 + Postgres.

You need: **Docker image on ECR** + **ECS task definition** + **Vercel env vars** so the API can call `ecs:RunTask`.

### Overview

```text
Vercel (vectorial-api-code)  --RunTask-->  ECS Fargate (sgo-worker container)
       |                                          |
       |                                          +--> S3, Postgres, OpenAI
       +--> poll GET .../fargate/pipeline-status
```

### 1) Sync SGO code (before building the image)

From this repo root:

```bash
cd "/media/samanvitha/Seagate HDD/Audience-workflow"
bash scripts/sync_linkedin_sgo_vendored.sh
```

### 2) Build and push the worker Docker image

```bash
cd "/media/samanvitha/Seagate HDD/Audience-workflow"

# Build (must run from repo root)
docker build -f docker/sgo-worker/Dockerfile -t sgo-worker:latest .

# ECR login (replace ACCOUNT_ID and REGION)
aws ecr get-login-password --region us-west-2 | \
  docker login --username AWS --password-stdin ACCOUNT_ID.dkr.ecr.us-west-2.amazonaws.com

# Tag and push
docker tag sgo-worker:latest ACCOUNT_ID.dkr.ecr.us-west-2.amazonaws.com/sgo-worker:latest
docker push ACCOUNT_ID.dkr.ecr.us-west-2.amazonaws.com/sgo-worker:latest
```

Create the ECR repo in AWS Console if it does not exist: **ECR → Create repository → `sgo-worker`**.

### 3) Create ECS task definition (Fargate)

In **AWS Console → ECS → Task definitions → Create**:

| Setting | Suggested value |
|---------|-----------------|
| Launch type | Fargate |
| CPU / Memory | e.g. 4 vCPU / 8 GB (LLM-heavy; increase if OOM) |
| Container name | `sgo-python-container` (must match `SGO_FARGATE_CONTAINER_NAME`) |
| Image | `ACCOUNT_ID.dkr.ecr.us-west-2.amazonaws.com/sgo-worker:latest` |
| Command | default (`python run_sgo_worker.py`) |

**Container environment variables** (same secrets as Vercel API — worker reads DB/S3/OpenAI from env):

| Variable | Required | Notes |
|----------|----------|--------|
| `OPENAI_API_KEY` | yes | |
| `BETA_DATABASE_URL` (or your enterprise URL) | yes | Same Postgres as API |
| `AUDIENCE_BUCKET_NAME` or `VECTOR_BUCKET_NAME` | yes | S3 audience bucket |
| `AWS_REGION` | yes | e.g. `us-west-2` |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | yes* | Or use task IAM role for S3 (preferred) |

\*If the **task role** has S3 access, you can omit static keys on the task and use IAM only.

`AUDIENCE_ROOM_ID`, `JOB_ID`, `TIER_MODE`, etc. are injected by Vercel when it calls `RunTask` — do not hardcode them on the task definition.

**Networking:** Fargate task must reach **OpenAI**, **S3**, **Postgres**, and (optional) **Vercel** for webhooks.

- Use **public subnet** + `assignPublicIp: ENABLED`, or private subnet + NAT.

Note the **task definition ARN** (family:revision), e.g. `sgo-worker-task:3`.

### 4) IAM roles

**Execution role** (pull image from ECR, write logs):

- AWS managed: `AmazonECSTaskExecutionRolePolicy`

**Task role** (worker at runtime):

- S3 read/write on audience bucket
- (Optional) same DB access if not using URL in env

**Vercel IAM user** (keys in Vercel env `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`):

- `ecs:RunTask`
- `iam:PassRole` on execution role + task role

### 5) Collect AWS IDs for config

You need:

| ID | Where to find |
|----|----------------|
| ECS cluster name | ECS → Clusters |
| Task definition | e.g. `sgo-worker-task:3` |
| Subnet IDs | VPC → Subnets (comma-separated) |
| Security group IDs | VPC → Security groups (allow outbound HTTPS) |
| Task role ARN | IAM → Roles |
| Execution role ARN | IAM → Roles |

### 6) Enable Fargate on Vercel (`vectorial-api-code`)

**Option A — Environment variables (recommended for production)**

Vercel Dashboard → **vectorial-api-code** → Settings → Environment Variables → add for **Production**:

```bash
SGO_FARGATE_ENABLED=true
SGO_FARGATE_ECS_CLUSTER=vectorial-ai-cluster
SGO_FARGATE_TASK_DEFINITION=sgo-worker-task:3
SGO_FARGATE_CONTAINER_NAME=sgo-python-container
SGO_FARGATE_SUBNET_IDS=subnet-aaa,subnet-bbb
SGO_FARGATE_SECURITY_GROUP_IDS=sg-ccc
SGO_FARGATE_TASK_ROLE_ARN=arn:aws:iam::ACCOUNT:role/sgo-task-role
SGO_FARGATE_EXECUTION_ROLE_ARN=arn:aws:iam::ACCOUNT:role/sgo-execution-role

AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=us-west-2

AUDIENCE_API_BASE_URL=https://vectorial-api-code.vercel.app
```

Optional (only if using webhooks instead of poll-only):

```bash
SGO_FARGATE_WEBHOOK_SECRET=some-long-random-secret
```

**Option B — `config/runtime.yaml` in the repo**

Edit `config/runtime.yaml` (defaults are `enabled: false`):

```yaml
sgo_fargate:
  enabled: true
  ecs_cluster: "vectorial-ai-cluster"
  task_definition: "sgo-worker-task:3"
  container_name: "sgo-python-container"
  subnet_ids: ["subnet-aaa", "subnet-bbb"]
  security_group_ids: ["sg-ccc"]
  task_role_arn: "arn:aws:iam::ACCOUNT:role/sgo-task-role"
  execution_role_arn: "arn:aws:iam::ACCOUNT:role/sgo-execution-role"
```

Env vars **override** YAML on Vercel. Redeploy after changes:

```bash
vercel --prod
```

### 7) Verify Fargate is configured

After deploy, call step 7 start. If Fargate is **not** set up you get **503**:

```text
SGO Fargate is not enabled. Set sgo_fargate.enabled and ECS settings...
```

**Test Fargate launch** (after full pipeline setup):

```bash
export BASE="https://vectorial-api-code.vercel.app"
export ROOM_ID="YOUR_ROOM_ID"
export ENTERPRISE="beta"

curl -sS -X POST \
  "${BASE}/api/v1/audience-rooms/${ROOM_ID}/linkedin-sgo-pipeline/fargate/start?enterpriseName=${ENTERPRISE}&tierMode=both&numIterations=5" \
  -H "Content-Type: application/json" \
  -d '{}'
```

**Success:** JSON includes `"compute_backend": "fargate"` and `"fargate_task_arn": "arn:aws:ecs:..."`.

**Check task in AWS:** ECS → Clusters → your cluster → Tasks → running `sgo-worker`.

### 8) After code changes

1. `bash scripts/sync_linkedin_sgo_vendored.sh`
2. Rebuild + push Docker image
3. Register new ECS task definition revision (or update `:latest` tag)
4. `vercel --prod` if only API changed

More detail: [SGO_FARGATE.md](./SGO_FARGATE.md).

---

## Pipeline steps (what the workflow runs)

| Step | Name | Where it runs |
|------|------|----------------|
| 1 | Rebuild tiered posts | Vercel FastAPI |
| 2 | Filter tiered posts | Vercel FastAPI |
| 3 | Theme category discovery | Vercel FastAPI (async) |
| 4 | Contextual stimulus | Vercel FastAPI (async) |
| 5 | Ground truth | Vercel FastAPI (async) |
| 6 | Initial prediction (i0) | Vercel FastAPI (async) |
| 7 | SGO training | **AWS Fargate** |

## API index (reference — use Swagger to try)

Base: `https://vectorial-api-code.vercel.app`

| Step | What | Method | Path (after base URL) |
|------|------|--------|------------------------|
| — | Check S3 progress | `GET` | `/api/v1/audience-rooms/YOUR_ROOM_ID/linkedin-room-pipeline/step-status` |
| — | Plan which steps to run | `POST` | `/api/v1/audience-rooms/YOUR_ROOM_ID/linkedin-room-pipeline/plan` |
| **1** | Rebuild tiers | `POST` | `/api/v1/audience-rooms/YOUR_ROOM_ID/rebuild-tiered-posts` |
| **2** | Filter tiers | `POST` | `/api/v1/audience-rooms/YOUR_ROOM_ID/filter-tiered-posts` |
| **3** | Theme (start) | `POST` | `/api/v1/audience-rooms/YOUR_ROOM_ID/theme_category_discovery/async` |
| **3** | Theme (poll) | `GET` | `/api/v1/audience-rooms/YOUR_ROOM_ID/theme_category_discovery/async/status/JOB_ID` |
| **4** | Stimulus (start) | `POST` | `/api/v1/audience-rooms/YOUR_ROOM_ID/contextual-stimulus-categorization/async` |
| **4** | Stimulus (poll) | `GET` | `/api/v1/audience-rooms/YOUR_ROOM_ID/contextual-stimulus-categorization/async/status/JOB_ID` |
| **5** | Ground truth (start) | `POST` | `/api/v1/audience-rooms/YOUR_ROOM_ID/ground-truth-extraction/async` |
| **5** | Ground truth (poll) | `GET` | `/api/v1/audience-rooms/YOUR_ROOM_ID/ground-truth-extraction/async/status/JOB_ID` |
| **6** | Initial prediction i0 (start) | `POST` | `/api/v1/audience-rooms/YOUR_ROOM_ID/linkedin-initial-prediction/async` |
| **6** | Initial prediction (poll) | `GET` | `/api/v1/audience-rooms/YOUR_ROOM_ID/linkedin-initial-prediction/async/status/JOB_ID` |
| **7** | SGO training (start) | `POST` | `/api/v1/audience-rooms/YOUR_ROOM_ID/linkedin-sgo-pipeline/fargate/start` |
| **7** | SGO training (poll) | `GET` | `/api/v1/audience-rooms/YOUR_ROOM_ID/linkedin-sgo-pipeline/fargate/pipeline-status` |

Steps **1–6** are invoked by the workflow (no manual curls needed). Step **7** is Fargate SGO.

Per-step API paths (Swagger only): https://vectorial-api-code.vercel.app/docs

---

## Run on Vercel (details)

See **[Run full pipeline on Vercel](#run-full-pipeline-on-vercel-steps-17)** above.

**Swagger:** https://vectorial-api-code.vercel.app/docs

**Env vars** on `vectorial-api-code` (Vercel → Settings → Environment Variables):

| Variable | Used for |
|----------|----------|
| `BETA_DATABASE_URL` / `GAMMA_DATABASE_URL` / etc. | Enterprise audience DB |
| `AUDIENCE_BUCKET_NAME` or `VECTOR_BUCKET_NAME` | S3 tier artifacts |
| `OPENAI_API_KEY` | Steps 3–7 |
| `AUDIENCE_API_BASE_URL` | `https://vectorial-api-code.vercel.app` |
| `SGO_FARGATE_*` + `AWS_*` | Step 7 Fargate — see **[Setup SGO Fargate](#setup-sgo-fargate-required-for-step-7)** |

**Deploy workflow app** (if you have `apps/vectorial_sgo_linkedin_testing` in your monorepo):

```bash
cd apps/vectorial_sgo_linkedin_testing
vercel --prod
```

---

## Helper APIs

### Check which steps are done (S3)

**API**

```text
GET https://vectorial-api-code.vercel.app/api/v1/audience-rooms/YOUR_ROOM_ID/linkedin-room-pipeline/step-status?enterpriseName=beta
```

**Query:** `enterpriseName` (optional)

**Body:** none

```bash
curl -sS "${BASE}/api/v1/audience-rooms/${ROOM_ID}/linkedin-room-pipeline/step-status?enterpriseName=${ENTERPRISE}"
```

---

### Plan run (skip completed steps)

**API**

```text
POST https://vectorial-api-code.vercel.app/api/v1/audience-rooms/YOUR_ROOM_ID/linkedin-room-pipeline/plan
```

**Body (JSON):**

```json
{
  "enterpriseName": "beta",
  "startFromStep": 1,
  "skipCompletedSteps": true,
  "runSgoPipeline": true,
  "forceSteps": null,
  "requirePrerequisites": true
}
```

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `enterpriseName` | string | null | Enterprise DB |
| `startFromStep` | int 1–7 | `1` | First step to consider |
| `skipCompletedSteps` | bool | `true` | Skip if S3 artifact exists |
| `runSgoPipeline` | bool | `false` | If `true`, plan through step 7 |
| `forceSteps` | int[] | null | e.g. `[5]` to re-run step 5 |
| `requirePrerequisites` | bool | `true` | Steps before `startFromStep` must be complete |

```bash
curl -sS -X POST "${BASE}/api/v1/audience-rooms/${ROOM_ID}/linkedin-room-pipeline/plan" \
  -H "Content-Type: application/json" \
  -d '{"enterpriseName":"beta","startFromStep":1,"skipCompletedSteps":true,"runSgoPipeline":true}'
```

---

## Step 7 — SGO training (Fargate)

Called automatically by the full pipeline workflow. Reference below if debugging in Swagger.

Runs on **AWS Fargate** (not Vercel serverless). **Start** returns immediately; **poll** until done.

**Start API**

```text
POST https://vectorial-api-code.vercel.app/api/v1/audience-rooms/YOUR_ROOM_ID/linkedin-sgo-pipeline/fargate/start?enterpriseName=beta&tierMode=both&numIterations=5
```

| Query param | Values | Default |
|-------------|--------|---------|
| `enterpriseName` | string | — |
| `model` | string | — |
| `tierMode` | `both` \| `tier1` \| `tier2` | `both` |
| `numIterations` | 1–50 | `5` |
| `notifyWebhook` | bool | `false` |

**Body (optional JSON):**

```json
{
  "workflowResumeUrl": "https://your-workflow-callback-url"
}
```

Or: `{}` / omit body

**Start response:**

```json
{
  "tier1_job_id": "uuid-1",
  "tier2_job_id": "uuid-2",
  "pipeline_mode": "tier1_then_tier2",
  "compute_backend": "fargate",
  "poll_pipeline": "/api/v1/audience-rooms/.../fargate/pipeline-status?..."
}
```

```bash
curl -sS -X POST \
  "${BASE}/api/v1/audience-rooms/${ROOM_ID}/linkedin-sgo-pipeline/fargate/start?enterpriseName=${ENTERPRISE}&tierMode=both&numIterations=5" \
  -H "Content-Type: application/json" \
  -d '{}'
```

Save `tier1_job_id` and `tier2_job_id` from the response.

**Poll API**

```text
GET https://vectorial-api-code.vercel.app/api/v1/audience-rooms/YOUR_ROOM_ID/linkedin-sgo-pipeline/fargate/pipeline-status?tier1JobId=TIER1_JOB_ID&tier2JobId=TIER2_JOB_ID&enterpriseName=beta
```

**Body:** none

**Poll response:** `pipeline_status` = `PROCESSING` | `COMPLETED` | `FAILED`

```bash
curl -sS \
  "${BASE}/api/v1/audience-rooms/${ROOM_ID}/linkedin-sgo-pipeline/fargate/pipeline-status?tier1JobId=${T1_JOB}&tier2JobId=${T2_JOB}&enterpriseName=${ENTERPRISE}"
```

**Needs:** Step 6 done. Fargate configured — see **[Setup SGO Fargate](#setup-sgo-fargate-required-for-step-7)**.

**Resume after failure (same job IDs):**

```text
POST https://vectorial-api-code.vercel.app/api/v1/audience-rooms/YOUR_ROOM_ID/linkedin-sgo-pipeline/fargate/resume?tier1JobId=TIER1_JOB_ID&tier2JobId=TIER2_JOB_ID&enterpriseName=beta
```

**Body:** none

---

## S3 outputs (tier 1)

```text
beta/linkedin-audience/YOUR_ROOM_ID/tiered_posts/
  manifest.json
  tier_1_authored_filtered.json
  discovered_category_topic_mapping_tier1_filtered.json
  contextual_stimulus_extraction_tier1_filtered.json
  ground_truth_extraction_tier1_filtered.json
  Predictions/initial_prediction_tier1.json
  linkedin_sgo/delta_method_predictions_tier1.json
```

---

## More detail

- [SGO_FARGATE.md](./SGO_FARGATE.md) — Fargate env & Docker  
- [LINKEDIN_PIPELINE_SKIP_STEPS.md](./LINKEDIN_PIPELINE_SKIP_STEPS.md) — workflow skip logic  
- [DEPLOYMENT.md](./DEPLOYMENT.md) — Vercel deploy checklist
