# LinkedIn room pipeline — skip completed steps (new runs)

After step **N** fails, you fix code and start a **new** workflow run. You should **not** re-run steps **1…N−1** if their S3 artifacts are already valid.

This API provides **run planning** based on canonical S3 outputs under  
`{enterprise}/linkedin-audience/{roomId}/tiered_posts/`.

## Step numbers

| Step | `stepId` | FastAPI (existing) |
|------|----------|-------------------|
| 1 | `rebuild` | `POST .../rebuild-tiered-posts` |
| 2 | `filter` | `POST .../filter-tiered-posts` |
| 3 | `theme` | `POST .../theme_category_discovery/async` (+ poll status) |
| 4 | `stimulus` | `POST .../contextual-stimulus-categorization/async` (+ `/async/process` chunks) |
| 5 | `ground_truth` | `POST .../ground-truth-extraction/async` (+ chunks) |
| 6 | `initial_prediction` | `POST .../linkedin-initial-prediction/async` (+ chunks) |
| 7 | `sgo` | `POST .../fargate/start` + poll `GET .../fargate/pipeline-status` (default), or legacy `POST .../async` + chunks |

## Endpoints (Audience-workflow / vectorial-api-code)

### Step status

```bash
curl -sS "https://vectorial-api-code.vercel.app/api/v1/audience-rooms/{ROOM_ID}/linkedin-room-pipeline/step-status?enterpriseName=beta"
```

### Run plan (use before executing workflow steps)

```bash
curl -sS -X POST "https://vectorial-api-code.vercel.app/api/v1/audience-rooms/{ROOM_ID}/linkedin-room-pipeline/plan" \
  -H "content-type: application/json" \
  -d '{
    "enterpriseName": "beta",
    "startFromStep": 5,
    "skipCompletedSteps": true,
    "runSgoPipeline": false
  }'
```

Example response:

```json
{
  "stepsToRun": [5, 6],
  "stepsSkipped": [1, 2, 3, 4],
  "startFromStep": 5,
  "pipelineThroughStep": 6,
  "stepStatus": [ ... ]
}
```

Query-param variant:

```bash
curl -sS -X POST ".../plan/query?enterpriseName=beta&startFromStep=5&skipCompletedSteps=true"
```

## Workflow integration (implemented)

**App:** `apps/vectorial_sgo_linkedin_testing` → deploys to `vectorial-sgo-linkedin-testing.vercel.app`.

The workflow `linkedinRoomPipelineWorkflow` calls `stepFetchPipelinePlan` first, then runs only steps in `plan.stepsToRun` (see `apps/vectorial_sgo_linkedin_testing/workflows/linkedin-room-pipeline.ts`).

Step 7 (SGO) uses **Fargate + poll-only** by default: `fargate/start` → `fargate/pipeline-status`.

### Trigger body (recommended)

```json
{
  "audienceRoomId": "...",
  "enterpriseName": "beta",
  "runSgoPipeline": false,
  "startFromStep": 1,
  "skipCompletedSteps": true
}
```

After **step 5 failed** and you deployed a fix:

```json
{
  "audienceRoomId": "...",
  "enterpriseName": "beta",
  "runSgoPipeline": false,
  "startFromStep": 5,
  "skipCompletedSteps": true
}
```

To **force** re-run step 5 even if a partial artifact exists:

```json
{
  "startFromStep": 5,
  "skipCompletedSteps": true,
  "forceSteps": [5]
}
```

## Rules

- **`requirePrerequisites`** (default `true`): `startFromStep: 5` requires steps 1–4 complete on S3.
- Completion is based on **canonical** artifact files (not `.partial` checkpoints).
- Tier **1** artifacts are used for steps 2–7 checks (matches default Phase 1).
- This does **not** replace SGO **checkpoint resume** (same `jobId`); use that for retry within one SGO job.
