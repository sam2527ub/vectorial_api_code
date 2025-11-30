# API Quick Reference Guide

**Base URL**: `https://pdl-workflow-api.vercel.app`

---

## Endpoints Summary

| Method | Endpoint | Description | Response Time |
|--------|----------|-------------|---------------|
| `GET` | `/` | Health check | < 1s |
| `POST` | `/api/v1/enrich` | Enrich job title | 1-2s |
| `POST` | `/api/v1/extract-filters` | Extract filters from text | 2-5s |
| `POST` | `/api/v1/search` | Search profiles | 2-5s |
| `POST` | `/api/v1/scrape` | Start scraping job | < 1s (async) |
| `GET` | `/api/v1/scrape/status/{job_id}` | Check job status | < 1s |

---

## Request/Response Examples

### 1. Health Check
```http
GET /
```
```json
{"status": "ok", "message": "Backend is running"}
```

---

### 2. Enrich Job Title
```http
POST /api/v1/enrich
Content-Type: application/json

{
  "job_title": "Machine Learning Engineer"
}
```
```json
{
  "name": "Machine Learning Engineer",
  "cleaned_name": "machine learning engineer",
  "related_titles": [...],
  "skills": [...]
}
```

---

### 3. Extract Filters
```http
POST /api/v1/extract-filters
Content-Type: application/json

{
  "description": "Software Engineers in SF"
}
```
```json
{
  "job_titles": ["Software Engineer"],
  "skills": ["Python"],
  "locations": ["San Francisco"],
  "role_search_type": "Current Role Only",
  "company_search_type": "Current Company Only"
}
```

---

### 4. Search Profiles
```http
POST /api/v1/search
Content-Type: application/json

{
  "titles": ["Software Engineer"],
  "skills": ["Python"],
  "limit": 10
}
```

**Response** (simplified - only essential fields):
```json
{
  "count": 10,
  "sql_generated": "SELECT * FROM person WHERE...",
  "profiles": [
    {
      "age": 26,
      "current_company": "Google",
      "current_location": "San Francisco, California, United States",
      "total_years_experience": 4.5,
      "industry": "Technology",
      "education": "Bachelors from Stanford University (Computer Science)",
      "linkedin_profile_url": "https://linkedin.com/in/johndoe"
    }
  ]
}
```

**Note**: All array values must be quoted strings:
- ✅ `"titles": ["backend engineer"]`
- ❌ `"titles": [backend engineer]` (invalid JSON)
```json
{
  "count": 10,
  "sql_generated": "SELECT * FROM person WHERE...",
  "profiles": [...]
}
```

---

### 5. Start Scraping (Async)
```http
POST /api/v1/scrape
Content-Type: application/json

{
  "linkedin_urls": ["https://linkedin.com/in/example"],
  "max_posts": 25,
  "cookies": [{
    "domain": ".linkedin.com",
    "name": "li_at",
    "value": "YOUR_COOKIE",
    "path": "/",
    "secure": true
  }],
  "user_agent": "Mozilla/5.0..."
}
```
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "PENDING",
  "message": "Scraping job started..."
}
```

---

### 6. Check Scraping Status
```http
GET /api/v1/scrape/status/{job_id}
```

**Processing:**
```json
{
  "job_id": "...",
  "status": "PROCESSING",
  "apify_status": "RUNNING",
  "message": "Scraping in progress..."
}
```

**Completed:**
```json
{
  "job_id": "...",
  "status": "COMPLETED",
  "posts_found": 15,
  "data": [...]
}
```

**Failed:**
```json
{
  "job_id": "...",
  "status": "FAILED",
  "error": "Error message"
}
```

---

## JavaScript Quick Examples

### Start Scraping & Poll
```javascript
// 1. Start scraping
const { job_id } = await fetch('/api/v1/scrape', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    linkedin_urls: ['https://linkedin.com/in/example'],
    max_posts: 25,
    cookies: [{ domain: '.linkedin.com', name: 'li_at', value: '...', path: '/', secure: true }],
    user_agent: navigator.userAgent
  })
}).then(r => r.json());

// 2. Poll for status
const poll = async () => {
  const status = await fetch(`/api/v1/scrape/status/${job_id}`).then(r => r.json());
  
  if (status.status === 'COMPLETED') {
    console.log('Done!', status.data);
  } else if (status.status === 'FAILED') {
    console.error('Failed:', status.error);
  } else {
    setTimeout(poll, 3000); // Poll again in 3s
  }
};
poll();
```

### Search Profiles
```javascript
const results = await fetch('/api/v1/search', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    titles: ['Software Engineer'],
    skills: ['Python'],
    limit: 10
  })
}).then(r => r.json());

console.log(`Found ${results.count} profiles`);
```

---

## Status Codes

| Code | Meaning |
|------|---------|
| 200 | Success |
| 400 | Bad Request |
| 401 | Unauthorized |
| 404 | Not Found |
| 429 | Rate Limited |
| 500 | Server Error |

---

## Important Notes

- **Scraping is async**: Returns `job_id` immediately, poll for status
- **Poll interval**: Check status every 3-5 seconds
- **Scraping time**: Takes 5-7 minutes per profile
- **CORS**: Enabled for all origins
- **Cookies**: Required for scraping, get from user's browser

---

For detailed documentation, see `API_DOCUMENTATION.md`

