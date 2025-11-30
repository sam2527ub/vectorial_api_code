# API Documentation - Profile Engine Backend

**Base URL**: `https://your-project.vercel.app` (or `http://localhost:8000` for local development)

**Content-Type**: `application/json`

---

## Table of Contents

1. [Health Check](#1-health-check)
2. [Job Title Enrichment](#2-job-title-enrichment)
3. [Extract Filters from Description](#3-extract-filters-from-description)
4. [Search Profiles](#4-search-profiles)
5. [Start Scraping Job (Async)](#5-start-scraping-job-async)
6. [Check Scraping Job Status](#6-check-scraping-job-status)
7. [Error Handling](#error-handling)
8. [Frontend Integration Examples](#frontend-integration-examples)

---

## 1. Health Check

Check if the API is running.

### Endpoint
```
GET /
```

### Request
No request body required.

### Response
```json
{
  "status": "ok",
  "message": "Backend is running"
}
```

### Example
```javascript
const response = await fetch('https://your-project.vercel.app/');
const data = await response.json();
console.log(data); // { status: "ok", message: "Backend is running" }
```

---

## 2. Job Title Enrichment

Enrich a job title using People Data Labs API to get standardized job title information.

### Endpoint
```
POST /api/v1/enrich
```

### Request Body
```json
{
  "job_title": "Machine Learning Engineer"
}
```

### Request Schema
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `job_title` | string | ✅ Yes | The job title to enrich |

### Response
```json
{
  "name": "Machine Learning Engineer",
  "cleaned_name": "machine learning engineer",
  "related_titles": ["ML Engineer", "AI Engineer", ...],
  "skills": ["Python", "TensorFlow", ...],
  ...
}
```

### Example
```javascript
const response = await fetch('https://your-project.vercel.app/api/v1/enrich', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    job_title: "Machine Learning Engineer"
  })
});

const enrichedData = await response.json();
```

### Error Responses
- **400 Bad Request**: Invalid job title or PDL API error
- **500 Internal Server Error**: Server error

---

## 3. Extract Filters from Description

Extract structured search filters from natural language description using OpenAI.

### Endpoint
```
POST /api/v1/extract-filters
```

### Request Body
```json
{
  "description": "Software Engineers in SF working at Series B companies"
}
```

### Request Schema
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `description` | string | ✅ Yes | Natural language description of desired profiles |

### Response
```json
{
  "job_titles": ["Software Engineer", "Senior Software Engineer"],
  "skills": ["Python", "JavaScript", "React"],
  "locations": ["San Francisco", "United States"],
  "company_names": [],
  "industries": ["Technology", "Software"],
  "company_sizes": ["51-200", "201-500"],
  "education_degrees": ["Bachelors", "Masters"],
  "seniority_levels": ["Senior", "Mid-level"],
  "job_roles": ["Engineer", "Developer"],
  "role_search_type": "Current Role Only",
  "company_search_type": "Current Company Only"
}
```

### Response Schema
| Field | Type | Description |
|-------|------|-------------|
| `job_titles` | string[] | Extracted job titles |
| `skills` | string[] | Extracted skills |
| `locations` | string[] | Extracted locations (cities/countries) |
| `company_names` | string[] | Extracted company names |
| `industries` | string[] | Extracted industries |
| `company_sizes` | string[] | Company size ranges (e.g., "1-10", "11-50", "10000+") |
| `education_degrees` | string[] | Education degrees (e.g., "Bachelors", "Masters") |
| `seniority_levels` | string[] | Seniority levels |
| `job_roles` | string[] | Job roles |
| `role_search_type` | string | "Current Role Only" or "Entire History" |
| `company_search_type` | string | "Current Company Only" or "Entire History" |

### Example
```javascript
const response = await fetch('https://your-project.vercel.app/api/v1/extract-filters', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    description: "Software Engineers in SF working at Series B companies"
  })
});

const filters = await response.json();
// Use filters in search endpoint
```

### Error Responses
- **500 Internal Server Error**: OpenAI API error or server error

---

## 4. Search Profiles

Search for profiles using People Data Labs with filters.

### Endpoint
```
POST /api/v1/search
```

### Request Body
```json
{
  "titles": ["Software Engineer", "Senior Software Engineer"],
  "skills": ["Python", "JavaScript"],
  "locations": ["United States", "San Francisco"],
  "industries": ["Technology"],
  "company_names": ["Google", "Microsoft"],
  "company_sizes": ["10000+"],
  "education_degrees": ["Bachelors", "Masters"],
  "seniority_levels": ["Senior"],
  "job_roles": ["Engineer"],
  "role_search_type": "Current Role Only",
  "company_search_type": "Current Company Only",
  "limit": 10,
  "experience_bucket": "Any"
}
```

**⚠️ Important**: All array values must be strings in quotes. Examples:
- ✅ Correct: `"titles": ["backend engineer"]`
- ❌ Wrong: `"titles": [backend engineer]` (missing quotes)
- ✅ Correct: `"titles": ["Backend Engineer", "Software Engineer"]`
- ✅ Correct: `"skills": []` (empty array is fine)

**💡 Tip for Tech Roles**: When searching for engineering/developer roles, add industry filter to get more relevant results:
```json
{
  "titles": ["backend engineer"],
  "industries": ["Technology", "Computer Software", "Internet", "Information Technology and Services"]
}
```
This filters out engineers working in non-tech industries (retail, real estate, etc.).

### Request Schema
| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `titles` | string[] | ❌ No | `[]` | Job titles to search |
| `skills` | string[] | ❌ No | `[]` | Skills to search |
| `locations` | string[] | ❌ No | `[]` | Locations (countries/cities) |
| `industries` | string[] | ❌ No | `[]` | Industries |
| `company_names` | string[] | ❌ No | `[]` | Company names |
| `company_sizes` | string[] | ❌ No | `[]` | Company sizes (e.g., "1-10", "11-50", "10000+") |
| `education_degrees` | string[] | ❌ No | `[]` | Education degrees |
| `seniority_levels` | string[] | ❌ No | `[]` | Seniority levels |
| `job_roles` | string[] | ❌ No | `[]` | Job roles |
| `role_search_type` | string | ❌ No | `"Current Role Only"` | "Current Role Only" or "Entire History" |
| `company_search_type` | string | ❌ No | `"Current Company Only"` | "Current Company Only" or "Entire History" |
| `limit` | number | ❌ No | `10` | Maximum number of results |
| `experience_bucket` | string | ❌ No | `"Any"` | Experience filter (handled client-side) |

### Response
```json
{
  "count": 10,
  "sql_generated": "SELECT * FROM person WHERE job_title IN ('Software Engineer') AND skills IN ('Python')",
  "profiles": [
    {
      "age": 26,
      "current_company": "Google",
      "current_location": "San Francisco, California, United States",
      "total_years_experience": 4.5,
      "industry": "Technology",
      "education": "Bachelors from Stanford University (Computer Science)",
      "linkedin_profile_url": "https://linkedin.com/in/johndoe"
    },
    {
      "age": null,
      "current_company": "Microsoft",
      "current_location": "Seattle, Washington, United States",
      "total_years_experience": 6.2,
      "industry": "Technology",
      "education": "Masters from MIT (Software Engineering)",
      "linkedin_profile_url": "https://linkedin.com/in/janedoe"
    },
    {
      "age": 30,
      "current_company": "Amazon",
      "current_location": "Seattle, Washington, United States",
      "total_years_experience": 8.1,
      "industry": "Technology",
      "education": null,
      "linkedin_profile_url": "https://linkedin.com/in/janedoe2"
    }
  ]
}
```

### Response Schema

**Top Level:**
| Field | Type | Description |
|-------|------|-------------|
| `count` | number | Number of profiles returned |
| `sql_generated` | string | SQL query that was executed |
| `profiles` | object[] | Array of simplified profile objects |

**Profile Object (Simplified):**
Each profile contains only the following fields:

| Field | Type | Description |
|-------|------|-------------|
| `age` | number \| null | Calculated age from birth_date (null if birth_date not available) |
| `current_company` | string \| null | Current job company name |
| `current_location` | string \| null | Current location (full location string) |
| `total_years_experience` | number | Calculated years of experience (excluding internships) |
| `industry` | string \| null | Industry |
| `education` | string \| null | Most recent/highest education formatted as "Degree from School (Major)" (e.g., "Bachelors from Stanford University (Computer Science)") |
| `linkedin_profile_url` | string \| null | LinkedIn profile URL |

### Example
```javascript
const response = await fetch('https://your-project.vercel.app/api/v1/search', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    titles: ["Software Engineer"],
    skills: ["Python"],
    locations: ["United States"],
    limit: 10
  })
});

const searchResults = await response.json();
console.log(`Found ${searchResults.count} profiles`);
```

### Error Responses
- **500 Internal Server Error**: PDL API error or server error

---

## 5. Start Scraping Job (Async)

Start an asynchronous scraping job for LinkedIn posts. Returns immediately with a `job_id` for polling.

### Endpoint
```
POST /api/v1/scrape
```

### Request Body
```json
{
  "linkedin_urls": [
    "https://linkedin.com/in/example-profile-1",
    "https://linkedin.com/in/example-profile-2"
  ],
  "max_posts": 25,
  "cookies": [
    {
      "domain": ".linkedin.com",
      "name": "li_at",
      "value": "AQEDAS...",
      "path": "/",
      "secure": true,
      "httpOnly": false,
      "hostOnly": false,
      "session": false
    }
  ],
  "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}
```

### Request Schema
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `linkedin_urls` | string[] | ✅ Yes | Array of LinkedIn profile URLs (min 1) |
| `max_posts` | number | ❌ No | Max posts per profile (1-100, default: 25) |
| `cookies` | Cookie[] | ✅ Yes | Array of cookie objects (min 1) |
| `user_agent` | string | ✅ Yes | User agent string |

### Cookie Object Schema
| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `domain` | string | ✅ Yes | - | Cookie domain (e.g., ".linkedin.com") |
| `name` | string | ✅ Yes | - | Cookie name (e.g., "li_at") |
| `value` | string | ✅ Yes | - | Cookie value |
| `path` | string | ❌ No | `"/"` | Cookie path |
| `secure` | boolean | ❌ No | `true` | Secure flag |
| `httpOnly` | boolean | ❌ No | `false` | HTTP only flag |
| `hostOnly` | boolean | ❌ No | `false` | Host only flag |
| `session` | boolean | ❌ No | `false` | Session cookie flag |
| `expirationDate` | number | ❌ No | `null` | Expiration timestamp |
| `sameSite` | string | ❌ No | `null` | SameSite attribute |
| `storeId` | string | ❌ No | `null` | Store ID |

### Response (Success)
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "PENDING",
  "message": "Scraping job started. Use /api/v1/scrape/status/{job_id} to check progress."
}
```

### Response Schema
| Field | Type | Description |
|-------|------|-------------|
| `job_id` | string (UUID) | Unique job identifier for polling |
| `status` | string | Job status ("PENDING") |
| `message` | string | Human-readable message |

### Example
```javascript
const response = await fetch('https://your-project.vercel.app/api/v1/scrape', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    linkedin_urls: ['https://linkedin.com/in/example'],
    max_posts: 25,
    cookies: [
      {
        domain: '.linkedin.com',
        name: 'li_at',
        value: 'YOUR_COOKIE_VALUE',
        path: '/',
        secure: true
      }
    ],
    user_agent: navigator.userAgent
  })
});

const { job_id } = await response.json();
// Use job_id to poll for status
```

### Error Responses

#### 400 Bad Request
```json
{
  "detail": "Cookies and User Agent are required for scraping."
}
```

#### 401 Unauthorized
```json
{
  "detail": {
    "error": "Apify authentication failed",
    "message": "...",
    "suggestion": "Please check your APIFY_API_TOKEN environment variable."
  }
}
```

#### 404 Not Found
```json
{
  "detail": {
    "error": "Apify actor not found or inaccessible",
    "message": "...",
    "suggestion": "Please verify the actor ID: curious_coder/linkedin-post-search-scraper"
  }
}
```

#### 429 Too Many Requests
```json
{
  "detail": {
    "error": "Apify usage limit exceeded",
    "message": "...",
    "suggestion": "Please check your Apify account usage limits..."
  }
}
```

#### 500 Internal Server Error
```json
{
  "detail": {
    "error": "Apify service error",
    "message": "...",
    "error_type": "..."
  }
}
```

---

## 6. Check Scraping Job Status

Poll this endpoint to check the status of a scraping job and get results when completed.

### Endpoint
```
GET /api/v1/scrape/status/{job_id}
```

### Path Parameters
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `job_id` | string (UUID) | ✅ Yes | Job ID returned from `/api/v1/scrape` |

### Response (Processing)
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "PROCESSING",
  "apify_status": "RUNNING",
  "message": "Scraping in progress..."
}
```

### Response (Completed)
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "COMPLETED",
  "posts_found": 15,
  "data": [
    {
      "text": "Excited to announce...",
      "url": "https://linkedin.com/posts/...",
      "author": "John Doe",
      "timestamp": "2024-01-15T10:30:00Z",
      "engagement": {
        "likes": 150,
        "comments": 25,
        "shares": 10
      },
      ...
    }
  ],
  "created_at": "2024-11-29T16:10:00.000Z",
  "updated_at": "2024-11-29T16:17:30.000Z"
}
```

### Response (Failed)
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "FAILED",
  "error": "Apify run failed: ...",
  "created_at": "2024-11-29T16:10:00.000Z",
  "updated_at": "2024-11-29T16:10:05.000Z"
}
```

### Response (Pending - No Apify Run ID yet)
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "PENDING",
  "message": "Waiting for Apify run to start..."
}
```

### Status Values
- `PENDING`: Job created, waiting for Apify to start
- `PROCESSING`: Apify scraping in progress
- `COMPLETED`: Scraping finished, results available
- `FAILED`: Scraping failed (check `error` field)

### Example
```javascript
const checkStatus = async (jobId) => {
  const response = await fetch(
    `https://your-project.vercel.app/api/v1/scrape/status/${jobId}`
  );
  const status = await response.json();
  
  if (status.status === 'COMPLETED') {
    console.log(`Found ${status.posts_found} posts`);
    return status.data;
  } else if (status.status === 'FAILED') {
    console.error('Scraping failed:', status.error);
    return null;
  } else {
    // Still processing, poll again
    return null;
  }
};
```

### Error Responses

#### 404 Not Found
```json
{
  "detail": "Job 550e8400-e29b-41d4-a716-446655440000 not found"
}
```

#### 500 Internal Server Error
```json
{
  "detail": "Error message"
}
```

---

## Error Handling

### Standard Error Response Format
```json
{
  "detail": "Error message or object"
}
```

### HTTP Status Codes
| Code | Meaning | When It Occurs |
|------|---------|----------------|
| 200 | Success | Request successful |
| 400 | Bad Request | Invalid request payload |
| 401 | Unauthorized | Authentication failed |
| 404 | Not Found | Resource not found |
| 429 | Too Many Requests | Rate limit exceeded |
| 500 | Internal Server Error | Server error |

### Error Handling Example
```javascript
try {
  const response = await fetch('https://your-project.vercel.app/api/v1/scrape', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail?.message || error.detail || 'Request failed');
  }
  
  const data = await response.json();
  return data;
} catch (error) {
  console.error('API Error:', error);
  // Handle error in UI
}
```

---

## Frontend Integration Examples

### React Hook for Async Scraping

```javascript
import { useState, useEffect } from 'react';

const useScrapingJob = () => {
  const [jobId, setJobId] = useState(null);
  const [status, setStatus] = useState(null);
  const [results, setResults] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  const startScraping = async (linkedinUrls, cookies, maxPosts = 25) => {
    setLoading(true);
    setError(null);
    
    try {
      const response = await fetch('https://your-project.vercel.app/api/v1/scrape', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          linkedin_urls: linkedinUrls,
          max_posts: maxPosts,
          cookies: cookies,
          user_agent: navigator.userAgent
        })
      });
      
      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail?.message || 'Failed to start scraping');
      }
      
      const data = await response.json();
      setJobId(data.job_id);
      setStatus(data.status);
      return data.job_id;
    } catch (err) {
      setError(err.message);
      throw err;
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!jobId) return;
    
    const pollStatus = async () => {
      try {
        const response = await fetch(
          `https://your-project.vercel.app/api/v1/scrape/status/${jobId}`
        );
        const statusData = await response.json();
        
        setStatus(statusData.status);
        
        if (statusData.status === 'COMPLETED') {
          setResults(statusData.data);
          setLoading(false);
        } else if (statusData.status === 'FAILED') {
          setError(statusData.error);
          setLoading(false);
        } else {
          // Still processing, poll again in 3 seconds
          setTimeout(pollStatus, 3000);
        }
      } catch (err) {
        setError(err.message);
        setLoading(false);
      }
    };
    
    const interval = setInterval(pollStatus, 3000);
    
    return () => clearInterval(interval);
  }, [jobId]);

  return {
    startScraping,
    status,
    results,
    error,
    loading,
    jobId
  };
};

// Usage in component
function ScrapingComponent() {
  const { startScraping, status, results, error, loading } = useScrapingJob();
  
  const handleScrape = async () => {
    try {
      await startScraping(
        ['https://linkedin.com/in/example'],
        [{ domain: '.linkedin.com', name: 'li_at', value: '...', path: '/', secure: true }],
        25
      );
    } catch (err) {
      console.error(err);
    }
  };
  
  return (
    <div>
      <button onClick={handleScrape} disabled={loading}>
        {loading ? 'Scraping...' : 'Start Scraping'}
      </button>
      {status && <p>Status: {status}</p>}
      {error && <p>Error: {error}</p>}
      {results && (
        <div>
          <h3>Results ({results.length} posts)</h3>
          {results.map((post, i) => (
            <div key={i}>{post.text}</div>
          ))}
        </div>
      )}
    </div>
  );
}
```

### Complete Integration Example

```javascript
class ProfileEngineAPI {
  constructor(baseURL = 'https://your-project.vercel.app') {
    this.baseURL = baseURL;
  }

  async healthCheck() {
    const response = await fetch(`${this.baseURL}/`);
    return await response.json();
  }

  async enrichJobTitle(jobTitle) {
    const response = await fetch(`${this.baseURL}/api/v1/enrich`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_title: jobTitle })
    });
    if (!response.ok) throw new Error('Enrichment failed');
    return await response.json();
  }

  async extractFilters(description) {
    const response = await fetch(`${this.baseURL}/api/v1/extract-filters`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ description })
    });
    if (!response.ok) throw new Error('Filter extraction failed');
    return await response.json();
  }

  async searchProfiles(filters) {
    const response = await fetch(`${this.baseURL}/api/v1/search`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(filters)
    });
    if (!response.ok) throw new Error('Search failed');
    return await response.json();
  }

  async startScraping(linkedinUrls, cookies, maxPosts = 25) {
    const response = await fetch(`${this.baseURL}/api/v1/scrape`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        linkedin_urls: linkedinUrls,
        max_posts: maxPosts,
        cookies: cookies,
        user_agent: navigator.userAgent
      })
    });
    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail?.message || 'Scraping failed');
    }
    return await response.json();
  }

  async checkScrapingStatus(jobId) {
    const response = await fetch(`${this.baseURL}/api/v1/scrape/status/${jobId}`);
    if (!response.ok) throw new Error('Status check failed');
    return await response.json();
  }

  async pollScrapingStatus(jobId, onUpdate, interval = 3000) {
    return new Promise((resolve, reject) => {
      const poll = async () => {
        try {
          const status = await this.checkScrapingStatus(jobId);
          onUpdate?.(status);
          
          if (status.status === 'COMPLETED') {
            resolve(status);
          } else if (status.status === 'FAILED') {
            reject(new Error(status.error));
          } else {
            setTimeout(poll, interval);
          }
        } catch (error) {
          reject(error);
        }
      };
      poll();
    });
  }
}

// Usage
const api = new ProfileEngineAPI();

// Start scraping and poll for results
const { job_id } = await api.startScraping(
  ['https://linkedin.com/in/example'],
  [{ domain: '.linkedin.com', name: 'li_at', value: '...', path: '/', secure: true }]
);

const results = await api.pollScrapingStatus(job_id, (status) => {
  console.log(`Status: ${status.status}`);
});
console.log('Scraping completed!', results.data);
```

---

## Important Notes

### Async Scraping Pattern

1. **Start Job**: Call `POST /api/v1/scrape` → Get `job_id` immediately (< 1 second)
2. **Poll Status**: Call `GET /api/v1/scrape/status/{job_id}` every 3-5 seconds
3. **Get Results**: When `status === "COMPLETED"`, results are in `data` field

### Scraping Duration
- Scraping typically takes **5-7 minutes** per profile
- The API responds immediately with `job_id`
- Frontend should show progress UI while polling

### CORS
- CORS is enabled for all origins (`allow_origins=["*"]`)
- In production, you may want to restrict this to your frontend domain

### Rate Limiting
- Be mindful of Apify rate limits
- Handle 429 errors gracefully with retry logic

### Cookie Management
- LinkedIn cookies (`li_at`) are required for scraping
- Cookies should be obtained from user's browser session
- Never store cookies in frontend code - get them dynamically

---

## Support

For issues or questions, contact the backend team or check the deployment logs in Vercel dashboard.


