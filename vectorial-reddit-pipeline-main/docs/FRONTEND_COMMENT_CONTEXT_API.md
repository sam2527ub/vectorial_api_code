# Frontend API Integration Guide - Comment Context Services

API documentation for integrating **Comment Context Scraping** and **Comment Context Summary** services into your frontend application.

**Base URL:** `https://vectorial-reddit-pipeline.vercel.app` (or your configured base URL)

---

## Table of Contents

1. [Overview](#overview)
2. [Service 1: Comment Context Scraping](#service-1-comment-context-scraping)
   - [Start Scraping Job](#11-start-scraping-job)
   - [Poll Scraping Status](#12-poll-scraping-status)
3. [Service 2: Comment Context Summary](#service-2-comment-context-summary)
4. [Complete Integration Flow](#complete-integration-flow)
5. [Error Handling](#error-handling)
6. [Integration Examples](#integration-examples)

---

## Overview

The Comment Context services work together in a **two-step process**:

1. **Scraping Service**: Scrapes Reddit post comments using Apify (async job pattern)
2. **Summary Service**: Enriches profiles' comments with context and AI-generated summaries (synchronous)

### Workflow

```
1. Start Scraping → Get job_id
2. Poll Status → Wait for scraping_complete
3. Generate Summary → Enrich comments with context & AI summaries
```

---

## Service 1: Comment Context Scraping

This service scrapes Reddit post comments from URLs found in audience room profiles. It uses an **async job pattern** - start a job, then poll for status.

### 1.1 Start Scraping Job

Starts Apify scraping runs for all unique post URLs found in the audience room's profiles. Returns immediately with a `job_id` and status URL.

#### Endpoint

```
POST /api/v1/audience-rooms/{audience_room_id}/comment-context/start
```

#### Request Parameters

| Parameter | Type | Location | Required | Description |
|-----------|------|----------|----------|-------------|
| `audience_room_id` | string | Path | Yes | The ID of the audience room to scrape |
| `enterpriseName` | string | Query | No | Enterprise name for database routing (default: "default") |

#### cURL Example

```bash
curl -X POST "https://vectorial-reddit-pipeline.vercel.app/api/v1/audience-rooms/abc-123-def-456/comment-context/start?enterpriseName=gamma" \
  -H "Content-Type: application/json"
```

With default enterprise:

```bash
curl -X POST "https://vectorial-reddit-pipeline.vercel.app/api/v1/audience-rooms/abc-123-def-456/comment-context/start" \
  -H "Content-Type: application/json"
```

#### Success Response (200 OK)

**Normal Response:**

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "audience_room_id": "abc-123-def-456",
  "status": "started",
  "run_ids": ["run-1", "run-2", "run-3"],
  "total_batches": 3,
  "total_post_urls": 45,
  "total_profiles": 50,
  "check_status_url": "/api/v1/audience-rooms/abc-123-def-456/comment-context/status?enterpriseName=gamma",
  "message": "Poll status until scraping_complete."
}
```

**No URLs Found:**

```json
{
  "job_id": null,
  "audience_room_id": "abc-123-def-456",
  "status": "no_urls",
  "message": "No post URLs found in comments.",
  "total_profiles": 50,
  "run_ids": [],
  "check_status_url": null
}
```

#### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `job_id` | string \| null | Unique job identifier (null if no URLs found) |
| `audience_room_id` | string | The audience room ID being processed |
| `status` | string | Status: `"started"` or `"no_urls"` |
| `run_ids` | array[string] | Array of Apify run IDs (empty if no URLs) |
| `total_batches` | integer | Number of batches created (null if no URLs) |
| `total_post_urls` | integer | Total unique post URLs found (null if no URLs) |
| `total_profiles` | integer | Total number of profiles in the audience room |
| `check_status_url` | string \| null | URL to poll for status (null if no URLs) |
| `message` | string | Human-readable message |

#### Error Responses

##### 404 Not Found - Room Not Found

```json
{
  "detail": "Audience room not found."
}
```

**Solution:** Verify the `audience_room_id` exists.

##### 404 Not Found - No Profiles

```json
{
  "detail": "No profiles in room."
}
```

**Solution:** Ensure the audience room has profiles before starting scraping.

##### 503 Service Unavailable - Storage Not Configured

```json
{
  "detail": "Storage client not configured."
}
```

**Solution:** Backend configuration issue - contact backend team.

##### 503 Service Unavailable - Scraping Client Not Configured

```json
{
  "detail": "Scraping client not configured. Set APIFY_API_TOKEN."
}
```

**Solution:** Backend configuration issue - contact backend team.

---

### 1.2 Poll Scraping Status

Poll this endpoint to check the status of the scraping job. Continue polling until `status` is `"scraping_complete"`.

#### Endpoint

```
GET /api/v1/audience-rooms/{audience_room_id}/comment-context/status
```

#### Request Parameters

| Parameter | Type | Location | Required | Description |
|-----------|------|----------|----------|-------------|
| `audience_room_id` | string | Path | Yes | The audience room ID |
| `enterpriseName` | string | Query | No | Enterprise name for database routing |

#### cURL Example

```bash
curl -X GET "https://vectorial-reddit-pipeline.vercel.app/api/v1/audience-rooms/abc-123-def-456/comment-context/status?enterpriseName=gamma"
```

#### Success Response (200 OK)

**While Running:**

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "audience_room_id": "abc-123-def-456",
  "status": "running",
  "run_ids": ["run-1", "run-2", "run-3"],
  "fetched_run_ids": ["run-1"],
  "running_runs": 2,
  "failed_runs": 0,
  "run_details": [
    {
      "run_id": "run-1",
      "status": "succeeded",
      "items_count": 150
    },
    {
      "run_id": "run-2",
      "status": "running"
    },
    {
      "run_id": "run-3",
      "status": "running"
    }
  ],
  "message": "Still running. Poll again.",
  "enterprise_name": "gamma"
}
```

**When Complete:**

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "audience_room_id": "abc-123-def-456",
  "status": "scraping_complete",
  "run_ids": ["run-1", "run-2", "run-3"],
  "fetched_run_ids": ["run-1", "run-2", "run-3"],
  "running_runs": 0,
  "failed_runs": 0,
  "run_details": [
    {
      "run_id": "run-1",
      "status": "succeeded",
      "items_count": 150
    },
    {
      "run_id": "run-2",
      "status": "succeeded",
      "items_count": 200
    },
    {
      "run_id": "run-3",
      "status": "succeeded",
      "items_count": 180
    }
  ],
  "message": "Scraping complete. Use comment-context-summary service with audience_room_id.",
  "enterprise_name": "gamma"
}
```

**With Failed Runs:**

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "audience_room_id": "abc-123-def-456",
  "status": "scraping_complete",
  "run_ids": ["run-1", "run-2", "run-3"],
  "fetched_run_ids": ["run-1", "run-3"],
  "running_runs": 0,
  "failed_runs": 1,
  "run_details": [
    {
      "run_id": "run-1",
      "status": "succeeded",
      "items_count": 150
    },
    {
      "run_id": "run-2",
      "status": "failed",
      "error": "Run timed out"
    },
    {
      "run_id": "run-3",
      "status": "succeeded",
      "items_count": 180
    }
  ],
  "message": "Scraping complete.",
  "enterprise_name": "gamma"
}
```

#### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `job_id` | string | Job identifier |
| `audience_room_id` | string | The audience room ID being processed |
| `status` | string | Status: `"running"` or `"scraping_complete"` |
| `run_ids` | array[string] | All Apify run IDs |
| `fetched_run_ids` | array[string] | Run IDs that have been fetched and stored |
| `running_runs` | integer | Number of runs still in progress |
| `failed_runs` | integer | Number of runs that failed |
| `run_details` | array[object] | Detailed status for each run |
| `run_details[].run_id` | string | Apify run ID |
| `run_details[].status` | string | Run status: `"fetched"`, `"succeeded"`, `"running"`, `"failed"`, `"aborted"`, `"timed-out"`, or `"error"` |
| `run_details[].items_count` | integer | Number of items fetched (only for succeeded runs) |
| `run_details[].error` | string | Error message (only for failed/error runs) |
| `message` | string | Human-readable status message |
| `enterprise_name` | string | Enterprise name used |

#### Status Values

- `"running"` - Scraping is still in progress, continue polling
- `"scraping_complete"` - All runs have completed (succeeded or failed), ready for summary service

#### Error Responses

##### 404 Not Found

```json
{
  "detail": "Job not found for audience room: abc-123-def-456."
}
```

**Solution:** Verify the scraping job was started successfully.

---

## Service 2: Comment Context Summary

This service loads the scraped Reddit post data from S3 and enriches each profile's comments with context and AI-generated summaries.

### Endpoint

```
POST /api/v1/audience-rooms/{audience_room_id}/comment-context-summary
```

### Request Parameters

| Parameter | Type | Location | Required | Description |
|-----------|------|----------|----------|-------------|
| `audience_room_id` | string | Path | Yes | The ID of the audience room to process |
| `enterpriseName` | string | Query | No | Enterprise name for database routing (default: "default") |

### cURL Example

```bash
curl -X POST "https://vectorial-reddit-pipeline.vercel.app/api/v1/audience-rooms/abc-123-def-456/comment-context-summary?enterpriseName=gamma" \
  -H "Content-Type: application/json"
```

With default enterprise:

```bash
curl -X POST "https://vectorial-reddit-pipeline.vercel.app/api/v1/audience-rooms/abc-123-def-456/comment-context-summary" \
  -H "Content-Type: application/json"
```

### Success Response (200 OK)

```json
{
  "status": "succeeded",
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "audience_room_id": "abc-123-def-456",
  "total_profiles": 50,
  "successful_profiles": 50,
  "failed_profiles": 0,
  "total_comments_enriched": 234,
  "total_comments_skipped": 12,
  "errors": [],
  "message": "Enriched 234 comments across 50 profiles. Skipped 12 comments."
}
```

**Partial Success:**

```json
{
  "status": "partial",
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "audience_room_id": "abc-123-def-456",
  "total_profiles": 50,
  "successful_profiles": 45,
  "failed_profiles": 5,
  "total_comments_enriched": 200,
  "total_comments_skipped": 10,
  "errors": [
    {
      "error": "Profile processing error details"
    }
  ],
  "message": "Enriched 200 comments across 45 profiles. Skipped 10 comments."
}
```

### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Overall status: `"succeeded"`, `"partial"`, or `"failed"` |
| `job_id` | string | Job ID from the scraping service (for reference) |
| `audience_room_id` | string | The audience room ID that was processed |
| `total_profiles` | integer | Total number of profiles in the audience room |
| `successful_profiles` | integer | Number of profiles successfully enriched |
| `failed_profiles` | integer | Number of profiles that failed to process |
| `total_comments_enriched` | integer | Total number of comments that were enriched with context and summaries |
| `total_comments_skipped` | integer | Number of comments that were skipped (e.g., no matching post data found) |
| `errors` | array | Array of error objects if any failures occurred |
| `message` | string | Human-readable summary message |

### Error Responses

#### 404 Not Found - No Scraping Job

```json
{
  "detail": "No scraping job found for audience room abc-123-def-456. Run comment-context/start first."
}
```

**Solution:** Ensure you've run the scraping service first and it has completed.

#### 400 Bad Request - Scraping Not Complete

```json
{
  "detail": "Job scraping not complete. Poll comment-context status first."
}
```

**Solution:** Wait for the scraping job to complete (`status: "scraping_complete"`) before calling this endpoint.

#### 404 Not Found - Room Not Found

```json
{
  "detail": "Room or profiles not found."
}
```

**Solution:** Verify the `audience_room_id` exists and has profiles.

#### 503 Service Unavailable - Storage Not Configured

```json
{
  "detail": "Storage client not configured."
}
```

**Solution:** Backend configuration issue - contact backend team.

#### 503 Service Unavailable - AI Client Not Configured

```json
{
  "detail": "AI client not configured. Set GROQ_API_KEY."
}
```

**Solution:** Backend configuration issue - contact backend team.

### Notes

- **Idempotent:** This endpoint can be called multiple times safely. Each call will re-process all profiles.
- **Processing Time:** Can take 30 seconds to several minutes depending on the number of profiles and comments.
- **Prerequisites:** The scraping service must be run first and completed before calling this endpoint.

---

## Complete Integration Flow

### Step-by-Step Process

```javascript
// 1. Start scraping job
const startResponse = await fetch(
  `/api/v1/audience-rooms/${audienceRoomId}/comment-context/start?enterpriseName=${enterpriseName}`,
  { method: 'POST' }
);
const startData = await startResponse.json();

if (startData.status === 'no_urls') {
  console.log('No URLs found - cannot proceed');
  return;
}

// 2. Poll scraping status until complete
let scrapingComplete = false;
while (!scrapingComplete) {
  await sleep(5000); // Wait 5 seconds between polls
  
  const statusResponse = await fetch(
    `/api/v1/audience-rooms/${audienceRoomId}/comment-context/status?enterpriseName=${enterpriseName}`
  );
  const statusData = await statusResponse.json();
  
  if (statusData.status === 'scraping_complete') {
    scrapingComplete = true;
    console.log('Scraping complete!');
    console.log(`Fetched ${statusData.fetched_run_ids.length} runs`);
  } else {
    console.log(`Still running: ${statusData.running_runs} runs in progress`);
  }
}

// 3. Generate summary
const summaryResponse = await fetch(
  `/api/v1/audience-rooms/${audienceRoomId}/comment-context-summary?enterpriseName=${enterpriseName}`,
  { method: 'POST' }
);
const summaryData = await summaryResponse.json();

console.log(`Summary complete: ${summaryData.total_comments_enriched} comments enriched`);
```

---

## Error Handling

### HTTP Status Codes

| Code | Meaning | Action |
|------|---------|--------|
| 200 | Success | Process the response |
| 400 | Bad Request | Check request parameters |
| 404 | Not Found | Verify IDs exist |
| 500 | Internal Server Error | Retry after a delay or contact support |
| 503 | Service Unavailable | Backend configuration issue - contact backend team |

### Retry Strategy

For polling endpoints, implement exponential backoff:

```javascript
const pollWithRetry = async (url, maxAttempts = 60, initialDelay = 5000) => {
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    try {
      const response = await fetch(url);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      
      const data = await response.json();
      
      if (data.status === 'scraping_complete' || data.status === 'no_urls') {
        return data;
      }
      
      // Exponential backoff: 5s, 7.5s, 11.25s, etc.
      const delay = initialDelay * Math.pow(1.5, attempt);
      await new Promise(resolve => setTimeout(resolve, delay));
    } catch (error) {
      if (attempt === maxAttempts - 1) throw error;
      await new Promise(resolve => setTimeout(resolve, initialDelay));
    }
  }
  throw new Error('Polling timeout');
};
```

---

## Integration Examples

### JavaScript/TypeScript Example

```typescript
interface ScrapingStartResponse {
  job_id: string | null;
  audience_room_id: string;
  status: 'started' | 'no_urls';
  run_ids: string[];
  total_batches?: number;
  total_post_urls?: number;
  total_profiles: number;
  check_status_url: string | null;
  message: string;
}

interface ScrapingStatusResponse {
  job_id: string;
  audience_room_id: string;
  status: 'running' | 'scraping_complete';
  run_ids: string[];
  fetched_run_ids: string[];
  running_runs: number;
  failed_runs: number;
  run_details: Array<{
    run_id: string;
    status: string;
    items_count?: number;
    error?: string;
  }>;
  message: string;
  enterprise_name: string;
}

interface SummaryResponse {
  status: 'succeeded' | 'partial' | 'failed';
  job_id: string;
  audience_room_id: string;
  total_profiles: number;
  successful_profiles: number;
  failed_profiles: number;
  total_comments_enriched: number;
  total_comments_skipped: number;
  errors: Array<{ error: string }>;
  message: string;
}

// Start scraping job
async function startScraping(
  audienceRoomId: string,
  enterpriseName?: string
): Promise<ScrapingStartResponse> {
  const url = new URL(
    `/api/v1/audience-rooms/${audienceRoomId}/comment-context/start`,
    'https://vectorial-reddit-pipeline.vercel.app'
  );
  
  if (enterpriseName) {
    url.searchParams.set('enterpriseName', enterpriseName);
  }
  
  const response = await fetch(url.toString(), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Failed to start scraping');
  }
  
  return await response.json();
}

// Poll scraping status
async function pollScrapingStatus(
  audienceRoomId: string,
  enterpriseName?: string,
  maxAttempts: number = 60,
  initialDelay: number = 5000
): Promise<ScrapingStatusResponse> {
  const url = new URL(
    `/api/v1/audience-rooms/${audienceRoomId}/comment-context/status`,
    'https://vectorial-reddit-pipeline.vercel.app'
  );
  
  if (enterpriseName) {
    url.searchParams.set('enterpriseName', enterpriseName);
  }
  
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    const response = await fetch(url.toString());
    
    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || 'Failed to fetch status');
    }
    
    const data: ScrapingStatusResponse = await response.json();
    
    if (data.status === 'scraping_complete') {
      return data;
    }
    
    // Exponential backoff
    const delay = initialDelay * Math.pow(1.5, attempt);
    await new Promise(resolve => setTimeout(resolve, delay));
  }
  
  throw new Error('Polling timeout - scraping did not complete in time');
}

// Generate summary
async function generateSummary(
  audienceRoomId: string,
  enterpriseName?: string
): Promise<SummaryResponse> {
  const url = new URL(
    `/api/v1/audience-rooms/${audienceRoomId}/comment-context-summary`,
    'https://vectorial-reddit-pipeline.vercel.app'
  );
  
  if (enterpriseName) {
    url.searchParams.set('enterpriseName', enterpriseName);
  }
  
  const response = await fetch(url.toString(), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Failed to generate summary');
  }
  
  return await response.json();
}

// Complete flow
async function processCommentContext(
  audienceRoomId: string,
  enterpriseName?: string
) {
  try {
    // Step 1: Start scraping
    console.log('Starting scraping job...');
    const startResult = await startScraping(audienceRoomId, enterpriseName);
    
    if (startResult.status === 'no_urls') {
      console.log('No URLs found - cannot proceed');
      return;
    }
    
    console.log(`Job started: ${startResult.job_id}`);
    console.log(`Total URLs: ${startResult.total_post_urls}`);
    
    // Step 2: Poll until complete
    console.log('Polling scraping status...');
    const statusResult = await pollScrapingStatus(
      audienceRoomId,
      enterpriseName
    );
    
    console.log(`Scraping complete!`);
    console.log(`Fetched runs: ${statusResult.fetched_run_ids.length}`);
    console.log(`Failed runs: ${statusResult.failed_runs}`);
    
    // Step 3: Generate summary
    console.log('Generating summary...');
    const summaryResult = await generateSummary(audienceRoomId, enterpriseName);
    
    console.log(`Summary complete!`);
    console.log(`Enriched comments: ${summaryResult.total_comments_enriched}`);
    console.log(`Successful profiles: ${summaryResult.successful_profiles}`);
    console.log(`Failed profiles: ${summaryResult.failed_profiles}`);
    
    return summaryResult;
  } catch (error: any) {
    console.error('Error processing comment context:', error.message);
    throw error;
  }
}
```

### React Hook Example

```typescript
import { useState, useEffect, useCallback } from 'react';

interface UseCommentContextResult {
  startScraping: () => Promise<void>;
  scrapingStatus: 'idle' | 'starting' | 'polling' | 'complete' | 'error';
  scrapingProgress: {
    runningRuns: number;
    fetchedRuns: number;
    totalRuns: number;
  };
  generateSummary: () => Promise<void>;
  summaryStatus: 'idle' | 'processing' | 'complete' | 'error';
  summaryResult: SummaryResponse | null;
  error: string | null;
}

function useCommentContext(
  audienceRoomId: string,
  enterpriseName?: string
): UseCommentContextResult {
  const [scrapingStatus, setScrapingStatus] = useState<
    'idle' | 'starting' | 'polling' | 'complete' | 'error'
  >('idle');
  const [scrapingProgress, setScrapingProgress] = useState({
    runningRuns: 0,
    fetchedRuns: 0,
    totalRuns: 0,
  });
  const [summaryStatus, setSummaryStatus] = useState<
    'idle' | 'processing' | 'complete' | 'error'
  >('idle');
  const [summaryResult, setSummaryResult] = useState<SummaryResponse | null>(
    null
  );
  const [error, setError] = useState<string | null>(null);
  
  const startScraping = useCallback(async () => {
    try {
      setScrapingStatus('starting');
      setError(null);
      
      const result = await startScraping(audienceRoomId, enterpriseName);
      
      if (result.status === 'no_urls') {
        setError('No URLs found in comments');
        setScrapingStatus('error');
        return;
      }
      
      setScrapingProgress({
        runningRuns: result.run_ids.length,
        fetchedRuns: 0,
        totalRuns: result.run_ids.length,
      });
      
      setScrapingStatus('polling');
      
      // Poll until complete
      const statusResult = await pollScrapingStatus(
        audienceRoomId,
        enterpriseName
      );
      
      setScrapingProgress({
        runningRuns: 0,
        fetchedRuns: statusResult.fetched_run_ids.length,
        totalRuns: statusResult.run_ids.length,
      });
      
      setScrapingStatus('complete');
    } catch (err: any) {
      setError(err.message);
      setScrapingStatus('error');
    }
  }, [audienceRoomId, enterpriseName]);
  
  const generateSummary = useCallback(async () => {
    if (scrapingStatus !== 'complete') {
      setError('Scraping must be complete before generating summary');
      return;
    }
    
    try {
      setSummaryStatus('processing');
      setError(null);
      
      const result = await generateSummary(audienceRoomId, enterpriseName);
      
      setSummaryResult(result);
      setSummaryStatus('complete');
    } catch (err: any) {
      setError(err.message);
      setSummaryStatus('error');
    }
  }, [audienceRoomId, enterpriseName, scrapingStatus]);
  
  return {
    startScraping,
    scrapingStatus,
    scrapingProgress,
    generateSummary,
    summaryStatus,
    summaryResult,
    error,
  };
}

// Usage in component
function CommentContextComponent({ audienceRoomId }: { audienceRoomId: string }) {
  const {
    startScraping,
    scrapingStatus,
    scrapingProgress,
    generateSummary,
    summaryStatus,
    summaryResult,
    error,
  } = useCommentContext(audienceRoomId, 'gamma');
  
  return (
    <div>
      <button onClick={startScraping} disabled={scrapingStatus !== 'idle'}>
        Start Scraping
      </button>
      
      {scrapingStatus === 'polling' && (
        <div>
          <p>Scraping in progress...</p>
          <p>
            {scrapingProgress.fetchedRuns} / {scrapingProgress.totalRuns} runs
            fetched
          </p>
        </div>
      )}
      
      {scrapingStatus === 'complete' && (
        <div>
          <p>Scraping complete!</p>
          <button onClick={generateSummary} disabled={summaryStatus === 'processing'}>
            Generate Summary
          </button>
        </div>
      )}
      
      {summaryStatus === 'complete' && summaryResult && (
        <div>
          <p>Summary complete!</p>
          <p>Enriched: {summaryResult.total_comments_enriched} comments</p>
          <p>Profiles: {summaryResult.successful_profiles} / {summaryResult.total_profiles}</p>
        </div>
      )}
      
      {error && <div className="error">{error}</div>}
    </div>
  );
}
```

---

## Summary

### Comment Context Scraping Service
- **Endpoints:** 
  - `POST /api/v1/audience-rooms/{audience_room_id}/comment-context/start` (start job)
  - `GET /api/v1/audience-rooms/{audience_room_id}/comment-context/status` (poll status)
- **Pattern:** Async job (start → poll)
- **Use Case:** Scrape Reddit post comments from URLs found in profiles
- **Completion:** Poll until `status === "scraping_complete"`

### Comment Context Summary Service
- **Endpoint:** `POST /api/v1/audience-rooms/{audience_room_id}/comment-context-summary`
- **Pattern:** Synchronous (waits for completion)
- **Use Case:** Enrich comments with context and AI summaries
- **Prerequisites:** Scraping must be complete
- **Can be called multiple times:** Yes (idempotent)

---

## Support

For questions or issues, contact the backend team or refer to the API documentation at:
`https://vectorial-reddit-pipeline.vercel.app/docs` (if Swagger/OpenAPI docs are enabled)
