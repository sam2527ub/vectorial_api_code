# Async Scraper Architecture Explained

## 🏗️ Architecture Overview

### Old Architecture (Synchronous - ❌ Timeout Issues)
```
Frontend → POST /api/v1/scrape
           ↓
    [WAITS 5-7 MINUTES] ← Apify scraping
           ↓
    Returns results
```
**Problem**: Vercel timeout (60s Pro, 300s max) ❌

---

### New Architecture (Asynchronous - ✅ No Timeouts)
```
Frontend → POST /api/v1/scrape
           ↓
    [< 1 second] Create job in DB + Start Apify
           ↓
    Returns job_id immediately ✅
           ↓
Frontend → GET /api/v1/scrape/status/{job_id} (poll every 3-5s)
           ↓
    Check DB → If PROCESSING, check Apify status
           ↓
    When COMPLETED: Fetch from Apify → Save to DB → Return results
```

---

## 📊 Flow Diagram

```
┌─────────┐
│ Frontend│
└────┬────┘
     │
     │ 1. POST /api/v1/scrape
     │    {linkedin_urls, cookies, ...}
     ▼
┌─────────────────────────────────────┐
│   FastAPI Backend                   │
│                                     │
│  ┌──────────────────────────────┐  │
│  │ Create Job in Database       │  │
│  │ Status: PENDING              │  │
│  └───────────┬──────────────────┘  │
│              │                      │
│  ┌───────────▼──────────────────┐  │
│  │ Start Apify (non-blocking)    │  │
│  │ apify_client.actor().start()  │  │
│  └───────────┬──────────────────┘  │
│              │                      │
│  ┌───────────▼──────────────────┐  │
│  │ Update Job:                   │  │
│  │ - Status: PROCESSING          │  │
│  │ - apifyRunId: <run_id>        │  │
│  └───────────┬──────────────────┘  │
└──────────────┼──────────────────────┘
               │
               │ Returns: {job_id, status: "PENDING"}
               │
┌───────────────▼──────────────┐
│   PostgreSQL Database        │
│   ┌──────────────────────┐  │
│   │ ScrapeJob Table      │  │
│   │ - id (UUID)          │  │
│   │ - status             │  │
│   │ - apifyRunId         │  │
│   │ - result (JSON)       │  │
│   └──────────────────────┘  │
└──────────────────────────────┘
               │
               │
┌───────────────▼──────────────┐
│   Apify Platform             │
│   (Runs scraping in cloud)   │
│   - Scrapes LinkedIn posts   │
│   - Stores in Dataset        │
│   - Takes 5-7 minutes        │
└──────────────────────────────┘

     │
     │ 2. Poll: GET /api/v1/scrape/status/{job_id}
     │    (Every 3-5 seconds)
     ▼
┌─────────────────────────────────────┐
│   FastAPI Backend                   │
│                                     │
│  ┌──────────────────────────────┐  │
│  │ Check Job in Database        │  │
│  │ - If COMPLETED: Return cached│  │
│  │ - If PROCESSING: Check Apify │  │
│  └───────────┬──────────────────┘  │
│              │                      │
│  ┌───────────▼──────────────────┐  │
│  │ Query Apify API              │  │
│  │ apify_client.run(id).get()   │  │
│  └───────────┬──────────────────┘  │
│              │                      │
│  ┌───────────▼──────────────────┐  │
│  │ If SUCCEEDED:                 │  │
│  │ 1. Fetch from Apify Dataset   │  │
│  │ 2. Save to Database           │  │
│  │ 3. Return results             │  │
│  └──────────────────────────────┘  │
└─────────────────────────────────────┘
     │
     │ Returns: {status: "COMPLETED", data: [...]}
     ▼
┌─────────┐
│ Frontend│ (Displays results)
└─────────┘
```

---

## 🔄 State Machine

```
PENDING → PROCESSING → COMPLETED
   │           │
   │           └──→ FAILED
   │
   └──→ FAILED (if Apify start fails)
```

### Job States:
- **PENDING**: Job created, waiting for Apify to start
- **PROCESSING**: Apify run started, scraping in progress
- **COMPLETED**: Scraping done, results saved in DB
- **FAILED**: Error occurred (stored in `error` field)

---

## 💾 Database Schema

```sql
ScrapeJob {
  id          UUID      (Primary Key)
  status      String    (PENDING/PROCESSING/COMPLETED/FAILED)
  linkedinUrls JSON     (Array of URLs)
  maxPosts    Int
  apifyRunId  String?   (Apify run ID)
  result      JSON?     (Scraped posts data)
  error       String?   (Error message if failed)
  createdAt   DateTime
  updatedAt   DateTime
}
```

---

## 🎯 Key Benefits

1. **No Timeouts**: API responds in < 1 second
2. **Persistent**: Jobs survive server restarts
3. **Cached**: Results stored in DB (fast retrieval)
4. **Trackable**: Full job history
5. **Scalable**: Multiple concurrent jobs

---

## 📝 API Endpoints

### 1. Start Scraping Job
```http
POST /api/v1/scrape
Content-Type: application/json

{
  "linkedin_urls": ["https://linkedin.com/in/profile1"],
  "max_posts": 25,
  "cookies": [...],
  "user_agent": "..."
}

Response (immediate):
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "PENDING",
  "message": "Scraping job started..."
}
```

### 2. Check Job Status
```http
GET /api/v1/scrape/status/{job_id}

Response (if processing):
{
  "job_id": "...",
  "status": "PROCESSING",
  "apify_status": "RUNNING",
  "message": "Scraping in progress..."
}

Response (if completed):
{
  "job_id": "...",
  "status": "COMPLETED",
  "posts_found": 15,
  "data": [...],
  "created_at": "...",
  "updated_at": "..."
}
```

---

## 🔑 Key Differences from Old Code

| Old Code | New Code |
|----------|----------|
| `apify_client.actor().call()` | `apify_client.actor().start()` |
| Blocks for 5-7 minutes | Returns immediately |
| No database | Prisma + PostgreSQL |
| No job tracking | Full job history |
| Timeout on Vercel | No timeout issues |

---

## 🧪 Testing Locally

See `TESTING.md` for detailed testing instructions.

