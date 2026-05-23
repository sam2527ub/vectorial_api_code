# Post Dimension Tagging API

API documentation for the Post Dimension Tagging service. Use these endpoints to start a dimension tagging job and poll for its status.

**Base URL:** `https://vectorial-reddit-pipeline.vercel.app`

---

## Overview

The Dimension Tagging API uses an **async job pattern**:

1. **POST** to create a job → receive `job_id` immediately
2. **GET** with `job_id` to poll for status and results

Processing runs in the background. Poll the status endpoint until `status` is `COMPLETED` or `FAILED`.

---

## 1. Create Dimension Tagging Job

Creates a new dimension tagging job and returns immediately with a `job_id`. Processing starts in the background.

### Endpoint

```
POST /api/v1/posts/dimension-tagging
```

### Request Body (JSON)

| Field             | Type   | Required | Default | Description                                      |
|-------------------|--------|----------|---------|--------------------------------------------------|
| `audienceRoomId`  | string | Yes      | —       | Audience room ID whose posts will be tagged      |
| `enterpriseName`  | string | No       | `null`  | Enterprise name for database routing             |
| `chunkSize`       | int    | No       | `10`    | Profiles per chunk (1–50). Affects processing.   |

### cURL Example

```bash
curl -X POST "https://vectorial-reddit-pipeline.vercel.app/api/v1/posts/dimension-tagging" \
  -H "Content-Type: application/json" \
  -d '{"audienceRoomId": "abc123"}'
```

With optional fields:

```bash
curl -X POST "https://vectorial-reddit-pipeline.vercel.app/api/v1/posts/dimension-tagging" \
  -H "Content-Type: application/json" \
  -d '{"audienceRoomId": "abc123", "enterpriseName": "acme", "chunkSize": 15}'
```

### Success Response (200 OK)

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "PENDING",
  "message": "Job created successfully. Use GET endpoint to check status."
}
```

| Field     | Type   | Description                                              |
|----------|--------|----------------------------------------------------------|
| `job_id` | string | UUID of the created job. Use this for the status endpoint |
| `status` | string | Always `"PENDING"` on creation                           |
| `message`| string | Human-readable confirmation                              |

### Error Responses

| Status | Condition                                      | Response Body                                  |
|--------|------------------------------------------------|-----------------------------------------------|
| 400    | Invalid parameters (e.g. missing audienceRoomId)| `{"detail": "Validation error message"}`      |
| 503    | AI client not configured                        | `{"detail": "AI client not configured..."}`   |
| 503    | Storage client not configured                   | `{"detail": "Storage client not configured..."}` |
| 500    | Server error                                    | `{"detail": "Failed to create dimension tagging job: ..."}` |

---

## 2. Get Job Status

Returns the current status, progress, and (when completed) results of a dimension tagging job.

### Endpoint

```
GET /api/v1/posts/dimension-tagging/{job_id}
```

### Path Parameters

| Parameter | Type   | Required | Description      |
|-----------|--------|----------|------------------|
| `job_id`  | string | Yes      | Job ID from POST |

### Query Parameters

| Parameter       | Type   | Required | Default | Description                       |
|----------------|--------|----------|---------|-----------------------------------|
| `enterpriseName` | string | No       | `null`  | Enterprise name for DB routing    |

### cURL Example

```bash
curl -X GET "https://vectorial-reddit-pipeline.vercel.app/api/v1/posts/dimension-tagging/550e8400-e29b-41d4-a716-446655440000"
```

With enterprise:

```bash
curl -X GET "https://vectorial-reddit-pipeline.vercel.app/api/v1/posts/dimension-tagging/550e8400-e29b-41d4-a716-446655440000?enterpriseName=acme"
```

### Success Response (200 OK)

#### While Processing

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "audience_room_id": "abc123",
  "status": "PROCESSING",
  "total_posts": 150,
  "processed_posts": 45,
  "tagged_posts": 42,
  "failed_posts": 3,
  "created_at": "2025-02-18T10:00:00.000Z",
  "updated_at": "2025-02-18T10:02:15.000Z",
  "started_at": "2025-02-18T10:00:05.000Z",
  "completed_at": null
}
```

#### When Completed

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "audience_room_id": "abc123",
  "status": "COMPLETED",
  "total_posts": 150,
  "processed_posts": 150,
  "tagged_posts": 145,
  "failed_posts": 5,
  "created_at": "2025-02-18T10:00:00.000Z",
  "updated_at": "2025-02-18T10:05:30.000Z",
  "started_at": "2025-02-18T10:00:05.000Z",
  "completed_at": "2025-02-18T10:05:30.000Z",
  "result": {
    "status": "success",
    "total_posts": 150,
    "tagged_posts": 145,
    "failed_posts": 5,
    "updated_s3_urls": []
  }
}
```

#### When Failed

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "audience_room_id": "abc123",
  "status": "FAILED",
  "total_posts": 150,
  "processed_posts": 30,
  "tagged_posts": 28,
  "failed_posts": 2,
  "created_at": "2025-02-18T10:00:00.000Z",
  "updated_at": "2025-02-18T10:01:00.000Z",
  "started_at": "2025-02-18T10:00:05.000Z",
  "completed_at": "2025-02-18T10:01:00.000Z",
  "error": "Error message describing what went wrong"
}
```

### Response Fields

| Field             | Type   | Present         | Description                                      |
|-------------------|--------|-----------------|--------------------------------------------------|
| `job_id`          | string | Always          | Job UUID                                         |
| `audience_room_id`| string | Always          | Audience room ID                                 |
| `status`          | string | Always          | `PENDING`, `PROCESSING`, `COMPLETED`, or `FAILED` |
| `total_posts`     | int    | Always          | Total posts to process                           |
| `processed_posts` | int    | Always          | Posts processed so far                           |
| `tagged_posts`    | int    | Always          | Successfully tagged posts                        |
| `failed_posts`    | int    | Always          | Posts that failed to tag                        |
| `created_at`       | string | Always          | ISO 8601 timestamp                               |
| `updated_at`      | string | Always          | ISO 8601 timestamp                               |
| `started_at`      | string\|null | Always  | When processing started (null if not started)     |
| `completed_at`    | string\|null | Always  | When job finished (null if not finished)          |
| `error`           | string | If failed        | Error message                                    |
| `result`          | object | If completed     | Result payload (see below)                        |

### `result` Object (when `status` is `COMPLETED`)

| Field             | Type   | Description                             |
|-------------------|--------|-----------------------------------------|
| `status`          | string | `"success"`                             |
| `total_posts`     | int    | Total posts processed                   |
| `tagged_posts`    | int    | Successfully tagged posts               |
| `failed_posts`    | int    | Posts that failed to tag                |
| `updated_s3_urls` | array  | S3 URLs updated (usually empty array)   |
| `message`         | string | Optional message (e.g. "No posts found")|

### Error Responses

| Status | Condition   | Response Body                         |
|--------|-------------|---------------------------------------|
| 404    | Job not found | `{"detail": "Job {job_id} not found"}` |
| 500    | Server error | `{"detail": "Failed to fetch job status: ..."}` |

---

## Job Status Values

| Status      | Meaning                                                       |
|-------------|---------------------------------------------------------------|
| `PENDING`   | Job created, processing not yet started                       |
| `PROCESSING`| Job is running; keep polling                                  |
| `COMPLETED` | Job finished successfully; `result` is present                |
| `FAILED`    | Job failed; `error` is present                                |

---

## Frontend Integration Guide

### 1. Start a Job

```javascript
async function startDimensionTagging(audienceRoomId, enterpriseName = null, chunkSize = 10) {
  const res = await fetch(
    'https://vectorial-reddit-pipeline.vercel.app/api/v1/posts/dimension-tagging',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        audienceRoomId,
        ...(enterpriseName && { enterpriseName }),
        ...(chunkSize !== 10 && { chunkSize }),
      }),
    }
  );
  if (!res.ok) throw new Error(await res.text());
  const data = await res.json();
  return data.job_id;
}
```

### 2. Poll for Status

```javascript
async function getJobStatus(jobId, enterpriseName = null) {
  const params = enterpriseName ? `?enterpriseName=${encodeURIComponent(enterpriseName)}` : '';
  const res = await fetch(
    `https://vectorial-reddit-pipeline.vercel.app/api/v1/posts/dimension-tagging/${jobId}${params}`
  );
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// Poll every 3 seconds until done
async function waitForCompletion(jobId, enterpriseName = null) {
  while (true) {
    const status = await getJobStatus(jobId, enterpriseName);
    if (status.status === 'COMPLETED' || status.status === 'FAILED') {
      return status;
    }
    await new Promise((r) => setTimeout(r, 3000));
  }
}
```

### 3. Full Flow Example

```javascript
async function runDimensionTagging(audienceRoomId) {
  const jobId = await startDimensionTagging(audienceRoomId);
  console.log('Job started:', jobId);

  const finalStatus = await waitForCompletion(jobId);

  if (finalStatus.status === 'COMPLETED') {
    console.log('Done!', finalStatus.result);
    return finalStatus.result;
  } else {
    throw new Error(finalStatus.error || 'Job failed');
  }
}
```

### Recommended Polling Interval

- **3–5 seconds** is usually enough
- Processing time depends on `total_posts` and `chunkSize`

---

## Process Dimension Tagging Chunk (Internal)

Used by the service to self-trigger the next chunk. Clients typically do not call this directly.

### Endpoint

```
POST /api/v1/posts/dimension-tagging/process
```

### Request Body (JSON)

| Field               | Type   | Required | Default | Description                         |
|---------------------|--------|----------|---------|-------------------------------------|
| `jobId`             | string | Yes      | —       | Job ID from create endpoint         |
| `audienceRoomId`    | string | Yes      | —       | Audience room ID                    |
| `startProfileIndex` | int    | No       | `0`     | Profile index to start from         |
| `chunkSize`         | int    | No       | `10`    | Profiles per chunk (1–50)           |
| `enterpriseName`    | string | No       | `null`  | Enterprise name for DB routing      |

### cURL Example

```bash
curl -X POST "https://vectorial-reddit-pipeline.vercel.app/api/v1/posts/dimension-tagging/process" \
  -H "Content-Type: application/json" \
  -d '{"jobId": "550e8400-e29b-41d4-a716-446655440000", "audienceRoomId": "abc123", "startProfileIndex": 0, "chunkSize": 10}'
```

---

## TypeScript Types (Optional)

```typescript
type JobStatus = 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'FAILED';

interface CreateJobRequest {
  audienceRoomId: string;
  enterpriseName?: string | null;
  chunkSize?: number; // 1–50, default 10
}

interface CreateJobResponse {
  job_id: string;
  status: 'PENDING';
  message: string;
}

interface JobStatusResponse {
  job_id: string;
  audience_room_id: string;
  status: JobStatus;
  total_posts: number;
  processed_posts: number;
  tagged_posts: number;
  failed_posts: number;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  completed_at: string | null;
  error?: string;
  result?: {
    status: 'success';
    total_posts: number;
    tagged_posts: number;
    failed_posts: number;
    updated_s3_urls: string[];
    message?: string;
  };
}
```
