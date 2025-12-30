# Why Do We Need a Database? (Prisma + PostgreSQL)

## The Problem Without Database

**Without database**, when you start a scraping job:
1. ✅ You get `job_id` immediately
2. ❌ **BUT** if server restarts → job_id is lost
3. ❌ **BUT** if you refresh page → can't check status
4. ❌ **BUT** results are only in memory → lost forever
5. ❌ **BUT** no history of past jobs

## What Prisma/PostgreSQL Does

### 1. **Stores Job Records** 📝
When you call `POST /api/v1/scrape`, it creates a record:

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "PENDING",
  "linkedinUrls": ["https://linkedin.com/in/example"],
  "maxPosts": 25,
  "apifyRunId": null,
  "result": null,
  "error": null,
  "createdAt": "2024-11-30T18:00:00Z"
}
```

### 2. **Tracks Job Status** 🔄
Updates status as scraping progresses:
- `PENDING` → Job created, waiting to start
- `PROCESSING` → Apify is scraping (5-7 minutes)
- `COMPLETED` → Done! Results saved
- `FAILED` → Error occurred

### 3. **Stores Apify Run ID** 🔗
Links your job to Apify's scraping run:
```json
{
  "apifyRunId": "abc123xyz",
  "status": "PROCESSING"
}
```
This lets you check Apify status later.

### 4. **Caches Results** 💾
When scraping completes, saves results in database:
```json
{
  "status": "COMPLETED",
  "result": {
    "posts_found": 15,
    "data": [
      {"text": "Post 1...", "url": "..."},
      {"text": "Post 2...", "url": "..."}
    ]
  }
}
```

**Why cache?** 
- ✅ Fast retrieval (no need to call Apify again)
- ✅ Works even if Apify deletes old runs
- ✅ Can retrieve results days/weeks later

### 5. **Persistent Storage** 💪
- ✅ Server restarts? Database still has your jobs
- ✅ Refresh page? Can still check status with `job_id`
- ✅ Deploy new code? Old jobs still accessible
- ✅ Multiple users? Each gets their own `job_id`

## Real-World Example

### Scenario: User starts scraping

**Step 1: Create Job**
```python
# User calls: POST /api/v1/scrape
job = await prisma.scrapejob.create({
    "status": "PENDING",
    "linkedinUrls": ["https://linkedin.com/in/john"]
})
# Returns: job_id = "abc-123"
```

**Step 2: Start Apify**
```python
# Start Apify scraping (non-blocking)
apify_run = apify_client.actor().start(...)

# Update job with Apify ID
await prisma.scrapejob.update({
    "status": "PROCESSING",
    "apifyRunId": "apify-xyz-789"
})
```

**Step 3: User Polls Status**
```python
# User calls: GET /api/v1/scrape/status/abc-123
job = await prisma.scrapejob.find_unique(id="abc-123")

if job.status == "PROCESSING":
    # Check Apify status
    apify_status = check_apify(job.apifyRunId)
    if apify_status == "SUCCEEDED":
        # Fetch results and save to DB
        results = fetch_from_apify()
        await prisma.scrapejob.update({
            "status": "COMPLETED",
            "result": results
        })
```

**Step 4: User Gets Results**
```python
# User calls again: GET /api/v1/scrape/status/abc-123
job = await prisma.scrapejob.find_unique(id="abc-123")
# Returns: {status: "COMPLETED", result: {...}}
# Fast! No need to call Apify again
```

## Database Schema

```sql
ScrapeJob {
  id              UUID      -- Unique job identifier
  status          String    -- PENDING/PROCESSING/COMPLETED/FAILED
  linkedinUrls    JSON      -- Which URLs to scrape
  maxPosts        Int       -- How many posts per profile
  apifyRunId      String?   -- Link to Apify run
  result          JSON?     -- Cached scraping results
  error           String?   -- Error message if failed
  createdAt       DateTime  -- When job was created
  updatedAt       DateTime  -- Last update time
}
```

## Benefits Summary

| Without Database | With Database (Prisma) |
|------------------|------------------------|
| ❌ Job lost on restart | ✅ Jobs persist |
| ❌ Can't check status later | ✅ Check anytime with job_id |
| ❌ Results lost | ✅ Results cached forever |
| ❌ No job history | ✅ Full history available |
| ❌ Must call Apify every time | ✅ Fast cached retrieval |

## When Database is Used

1. **Creating scraping job** → Save to DB
2. **Updating job status** → Update DB
3. **Checking job status** → Read from DB
4. **Saving results** → Store in DB
5. **Retrieving results** → Read from DB (fast!)

## Could We Skip the Database?

**Technically yes**, but you'd lose:
- ❌ Ability to check status after page refresh
- ❌ Job history
- ❌ Cached results (must call Apify every time)
- ❌ Persistence across server restarts

**With database**, you get a **production-ready, scalable solution**! 🚀

