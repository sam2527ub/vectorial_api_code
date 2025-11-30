# Testing Guide - Local Development

## 🚀 Quick Start

### 1. Start the Server

```bash
# Make sure you're in the project directory
cd "/Users/tamannabansal/Desktop/pdl vercel backend"

# Start FastAPI server
python3 main.py
```

Server will start at: `http://localhost:8000`

### 2. Test Health Check

```bash
curl http://localhost:8000/
```

Expected response:
```json
{"status": "ok", "message": "Backend is running"}
```

---

## 🧪 Testing the Async Scraper

### Step 1: Start a Scraping Job

```bash
curl -X POST http://localhost:8000/api/v1/scrape \
  -H "Content-Type: application/json" \
  -d '{
    "linkedin_urls": ["https://linkedin.com/in/example"],
    "max_posts": 10,
    "cookies": [
      {
        "domain": ".linkedin.com",
        "name": "li_at",
        "value": "your_cookie_value",
        "path": "/",
        "secure": true
      }
    ],
    "user_agent": "Mozilla/5.0..."
  }'
```

**Expected Response** (returns immediately):
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "PENDING",
  "message": "Scraping job started. Use /api/v1/scrape/status/{job_id} to check progress."
}
```

**Save the `job_id`** - you'll need it for the next step!

---

### Step 2: Poll for Status

Replace `{job_id}` with the ID from Step 1:

```bash
# Check status
curl http://localhost:8000/api/v1/scrape/status/{job_id}
```

**Response (if still processing):**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "PROCESSING",
  "apify_status": "RUNNING",
  "message": "Scraping in progress..."
}
```

**Response (when completed):**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "COMPLETED",
  "posts_found": 15,
  "data": [
    {
      "text": "Post content...",
      "url": "https://linkedin.com/posts/...",
      ...
    }
  ],
  "created_at": "2024-11-29T16:10:00",
  "updated_at": "2024-11-29T16:17:30"
}
```

**Poll every 3-5 seconds** until status is `COMPLETED` or `FAILED`.

---

## 🐍 Python Testing Script

Create a test script:

```python
# test_scraper.py
import requests
import time
import json

BASE_URL = "http://localhost:8000"

# Step 1: Start scraping job
print("🚀 Starting scraping job...")
response = requests.post(
    f"{BASE_URL}/api/v1/scrape",
    json={
        "linkedin_urls": ["https://linkedin.com/in/example"],
        "max_posts": 10,
        "cookies": [
            {
                "domain": ".linkedin.com",
                "name": "li_at",
                "value": "your_cookie_value",
                "path": "/",
                "secure": True
            }
        ],
        "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)..."
    }
)

job_data = response.json()
job_id = job_data["job_id"]
print(f"✅ Job created: {job_id}")
print(f"   Status: {job_data['status']}")

# Step 2: Poll for status
print("\n⏳ Polling for status...")
while True:
    status_response = requests.get(f"{BASE_URL}/api/v1/scrape/status/{job_id}")
    status_data = status_response.json()
    
    current_status = status_data["status"]
    print(f"   Status: {current_status}")
    
    if current_status == "COMPLETED":
        print(f"\n✅ Scraping completed!")
        print(f"   Posts found: {status_data.get('posts_found', 0)}")
        print(f"   Data: {json.dumps(status_data.get('data', [])[:2], indent=2)}")  # First 2 posts
        break
    elif current_status == "FAILED":
        print(f"\n❌ Scraping failed: {status_data.get('error', 'Unknown error')}")
        break
    else:
        print(f"   Message: {status_data.get('message', 'Processing...')}")
        time.sleep(5)  # Wait 5 seconds before next poll
```

Run it:
```bash
python3 test_scraper.py
```

---

## 🗄️ Check Database Directly

### Using Prisma Studio (Visual Database Browser)

```bash
prisma studio
```

Opens at: `http://localhost:5555`

You can:
- View all `ScrapeJob` records
- See job status, results, errors
- Inspect the data structure

### Using Python

```python
import asyncio
from prisma import Prisma

async def view_jobs():
    prisma = Prisma()
    await prisma.connect()
    
    # Get all jobs
    jobs = await prisma.scrapejob.find_many(
        order={'createdAt': 'desc'},
        take=10  # Last 10 jobs
    )
    
    for job in jobs:
        print(f"Job {job.id}: {job.status}")
        print(f"  Created: {job.createdAt}")
        print(f"  URLs: {len(job.linkedinUrls)}")
        if job.result:
            print(f"  Posts: {job.result.get('posts_found', 0)}")
        print()
    
    await prisma.disconnect()

asyncio.run(view_jobs())
```

---

## 📊 API Documentation

FastAPI automatically generates interactive docs:

1. **Swagger UI**: http://localhost:8000/docs
   - Interactive API testing
   - Try endpoints directly in browser
   - See request/response schemas

2. **ReDoc**: http://localhost:8000/redoc
   - Alternative documentation view

---

## 🔍 Debugging Tips

### 1. Check Logs

The server logs all important events:
```
INFO: Created job abc123 for 1 URLs
INFO: Started Apify run xyz789 for job abc123
INFO: Job abc123 completed with 15 posts
```

### 2. Check Database

```python
# Count jobs by status
from prisma import Prisma
import asyncio

async def debug():
    prisma = Prisma()
    await prisma.connect()
    
    pending = await prisma.scrapejob.count(where={'status': 'PENDING'})
    processing = await prisma.scrapejob.count(where={'status': 'PROCESSING'})
    completed = await prisma.scrapejob.count(where={'status': 'COMPLETED'})
    failed = await prisma.scrapejob.count(where={'status': 'FAILED'})
    
    print(f"Pending: {pending}")
    print(f"Processing: {processing}")
    print(f"Completed: {completed}")
    print(f"Failed: {failed}")
    
    await prisma.disconnect()

asyncio.run(debug())
```

### 3. Test Individual Components

```python
# Test database connection
python3 -c "from prisma import Prisma; import asyncio; asyncio.run(Prisma().connect())"

# Test Apify connection
python3 -c "from apify_client import ApifyClient; import os; client = ApifyClient(os.getenv('APIFY_API_TOKEN')); print('✅ Apify connected')"
```

---

## ⚠️ Common Issues

### Issue: "Prisma client not connected"
**Solution**: Make sure you run `prisma generate` and the server has `DATABASE_URL` in `.env`

### Issue: "Apify authentication failed"
**Solution**: Check `APIFY_API_TOKEN` in `.env`

### Issue: "Job stuck in PROCESSING"
**Solution**: 
- Check Apify dashboard: https://console.apify.com
- The status endpoint will automatically check Apify and update DB

### Issue: "Database connection timeout"
**Solution**: 
- Use pooled connection: `DATABASE_URL` (not `DATABASE_URL_UNPOOLED`)
- Check Neon dashboard for connection limits

---

## 🎯 Next Steps

1. ✅ Test locally with real LinkedIn URLs
2. ✅ Verify jobs are saved in database
3. ✅ Test polling mechanism
4. ✅ Deploy to Vercel
5. ✅ Add `DATABASE_URL` to Vercel environment variables

---

## 📝 Example Frontend Integration

```javascript
// React example
const startScraping = async () => {
  const response = await fetch('/api/v1/scrape', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      linkedin_urls: ['https://linkedin.com/in/example'],
      max_posts: 25,
      cookies: [...],
      user_agent: navigator.userAgent
    })
  });
  
  const { job_id } = await response.json();
  
  // Poll for status
  const pollStatus = async () => {
    const statusRes = await fetch(`/api/v1/scrape/status/${job_id}`);
    const status = await statusRes.json();
    
    if (status.status === 'COMPLETED') {
      setResults(status.data);
    } else if (status.status === 'FAILED') {
      setError(status.error);
    } else {
      setTimeout(pollStatus, 3000); // Poll again in 3s
    }
  };
  
  pollStatus();
};
```


