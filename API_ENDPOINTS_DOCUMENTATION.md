# API Endpoints Documentation

This document provides comprehensive documentation for all API endpoints that the frontend needs to integrate.

**Base URL**: Your backend URL (e.g., `https://your-api.vercel.app` or `http://localhost:8000`)

**Content-Type**: All requests should use `application/json`

---

## Table of Contents

1. [POST /api/v1/search - Search Profiles](#1-post-apiv1search---search-profiles)
2. [POST /api/v1/audience-rooms - Create Audience Room](#2-post-apiv1audience-rooms---create-audience-room)
3. [GET /api/v1/audience-rooms/{audience_room_id}/profiles/{profile_id}/posts - Get Profile Posts](#3-get-apiv1audience-roomsaudience_room_idprofilesprofile_idposts---get-profile-posts)
4. [GET /api/v1/audience-rooms/{audience_room_id}/description - Get Audience Room Description](#4-get-apiv1audience-roomsaudience_room_iddescription---get-audience-room-description)
5. [GET /api/v1/audience-rooms/{audience_room_id}/profiles/{profile_id}/description - Get Profile Description](#5-get-apiv1audience-roomsaudience_room_idprofilesprofile_iddescription---get-profile-description)
6. [POST /api/v1/scrape - Trigger Scraping](#6-post-apiv1scrape---trigger-scraping)
7. [GET /api/v1/scrape/status/{job_id} - Get Scrape Status](#7-get-apiv1scrapestatusjob_id---get-scrape-status)
8. [POST /api/v1/audience-rooms/{audience_room_id}/generate-summaries - Generate Profile Summaries](#8-post-apiv1audience-roomsaudience_room_idgenerate-summaries---generate-profile-summaries)
9. [POST /api/classifier/run - Run Classifier](#9-post-apiclassifierrun---run-classifier)

**Note**: For detailed Classifier API documentation, see [CLASSIFIER_API_DOCUMENTATION.md](./CLASSIFIER_API_DOCUMENTATION.md)

---

## 1. POST /api/v1/search - Search Profiles

Search for professional profiles using People Data Labs (PDL) API with various filters.

### Endpoint
```
POST /api/v1/search
```

### Request Headers
```
Content-Type: application/json
```

### Request Body

All fields are optional except where noted. Arrays can be empty.

```json
{
  "titles": ["Software Engineer", "Product Manager"],
  "skills": ["Python", "JavaScript", "React"],
  "locations": ["United States", "San Francisco", "New York"],
  "industries": ["Technology", "Computer Software", "Internet"],
  "company_names": ["Google", "Microsoft", "Apple"],
  "company_sizes": ["10000+", "1001-5000"],
  "education_degrees": ["Bachelors", "Masters", "PhD"],
  "seniority_levels": ["Senior", "Lead", "Principal"],
  "job_roles": ["Engineer", "Developer", "Architect"],
  "role_search_type": "Current Role Only",
  "company_search_type": "Current Company Only",
  "limit": 10,
  "experience_bucket": "Any"
}
```

### Request Body Schema

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `titles` | `string[]` | No | `[]` | List of job titles to search for |
| `skills` | `string[]` | No | `[]` | List of skills to filter by |
| `locations` | `string[]` | No | `[]` | List of locations (countries/cities) |
| `industries` | `string[]` | No | `[]` | List of industries |
| `company_names` | `string[]` | No | `[]` | List of company names |
| `company_sizes` | `string[]` | No | `[]` | Company size ranges (e.g., "1-10", "11-50", "51-200", "201-500", "501-1000", "1001-5000", "5001-10000", "10000+") |
| `education_degrees` | `string[]` | No | `[]` | Education degrees (e.g., "Bachelors", "Masters", "PhD") |
| `seniority_levels` | `string[]` | No | `[]` | Seniority levels (e.g., "Entry", "Mid", "Senior", "Lead", "Principal") |
| `job_roles` | `string[]` | No | `[]` | Job roles (e.g., "Engineer", "Manager", "Director") |
| `role_search_type` | `string` | No | `"Current Role Only"` | Options: `"Current Role Only"` or `"Entire History"` |
| `company_search_type` | `string` | No | `"Current Company Only"` | Options: `"Current Company Only"` or `"Entire History"` |
| `limit` | `integer` | No | `10` | Maximum number of profiles to return |
| `experience_bucket` | `string` | No | `"Any"` | Experience bucket (handled client-side after fetch) |

### Response (200 OK)

```json
{
  "count": 10,
  "sql_generated": "SELECT * FROM person WHERE job_title IN ('Software Engineer') AND location_country IN ('United States')",
  "profiles": [
    {
      "name": "John Doe",
      "age": 32,
      "current_company": "Google",
      "current_location": "San Francisco, California, United States",
      "total_years_experience": 8.5,
      "industry": "Technology",
      "education": "Masters from Stanford University (Computer Science)",
      "linkedin_profile_url": "https://www.linkedin.com/in/johndoe"
    }
  ]
}
```

### Response Schema

| Field | Type | Description |
|-------|------|-------------|
| `count` | `integer` | Number of profiles returned |
| `sql_generated` | `string` | The SQL query that was generated and executed |
| `profiles` | `array` | Array of profile objects |

#### Profile Object Schema

| Field | Type | Description |
|-------|------|-------------|
| `name` | `string` | Full name of the person |
| `age` | `integer \| null` | Age (calculated from birth_date or inferred) |
| `current_company` | `string \| null` | Current company name |
| `current_location` | `string \| null` | Current location |
| `total_years_experience` | `float` | Total years of experience (excluding internships) |
| `industry` | `string \| null` | Industry |
| `education` | `string \| null` | Education summary (e.g., "Bachelors from Stanford University (Computer Science)") |
| `linkedin_profile_url` | `string \| null` | LinkedIn profile URL |

### Error Responses

- **500 Internal Server Error**: Server error or PDL API error
  ```json
  {
    "detail": "Error message"
  }
  ```

### Example Request (cURL)

```bash
curl -X POST "https://your-api.vercel.app/api/v1/search" \
  -H "Content-Type: application/json" \
  -d '{
    "titles": ["Software Engineer"],
    "locations": ["United States"],
    "industries": ["Technology"],
    "limit": 20
  }'
```

### Example Request (JavaScript/Fetch)

```javascript
const response = await fetch('https://your-api.vercel.app/api/v1/search', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    titles: ['Software Engineer'],
    locations: ['United States'],
    industries: ['Technology'],
    limit: 20
  })
});

const data = await response.json();
console.log(data.profiles);
```

### Notes

- If searching for tech roles without specifying industry, results may include engineers in non-tech industries. To get more relevant results, add industry filter: `["Technology", "Computer Software", "Internet"]`
- The `experience_bucket` field is handled client-side after the API fetch
- All array filters use `IN` clauses (OR logic within each filter, AND logic between filters)

---

## 2. POST /api/v1/audience-rooms - Create Audience Room

Create a new audience room with profiles. The audience description and profile data are stored in S3, and metadata is stored in the database.

### Endpoint
```
POST /api/v1/audience-rooms
```

### Request Headers
```
Content-Type: application/json
```

### Request Body

```json
{
  "audience_room_name": "Tech Engineers in SF",
  "audience_description": "Software Engineers in San Francisco working at Series B companies",
  "profiles": [
    {
      "name": "John Doe",
      "age": 32,
      "current_company": "Google",
      "current_location": "San Francisco, California",
      "total_years_experience": 8.5,
      "industry": "Technology",
      "education": "Masters from Stanford University (Computer Science)",
      "linkedin_profile_url": "https://www.linkedin.com/in/johndoe"
    },
    {
      "name": "Jane Smith",
      "age": 28,
      "current_company": "Microsoft",
      "current_location": "San Francisco, California",
      "total_years_experience": 5.0,
      "industry": "Technology",
      "education": "Bachelors from UC Berkeley (Computer Science)",
      "linkedin_profile_url": "https://www.linkedin.com/in/janesmith"
    }
  ]
}
```

### Request Body Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `audience_room_name` | `string` | Yes | Name of the audience room |
| `audience_description` | `string` | Yes | Plain-text description for the audience room |
| `profiles` | `array` | Yes | Array of profile objects (minimum 1 profile) |

#### Profile Object Schema (in Request)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | `string` | Yes | Full name of the profile |
| `age` | `integer` | No | Age if available |
| `current_company` | `string` | No | Current company |
| `current_location` | `string` | No | Current location |
| `total_years_experience` | `float` | No | Total years of experience |
| `industry` | `string` | No | Industry |
| `education` | `string` | No | Education summary |
| `linkedin_profile_url` | `string` | Yes | LinkedIn profile URL |

### Response (200 OK)

```json
{
  "audience_room_id": "550e8400-e29b-41d4-a716-446655440000",
  "audience_room_name": "Tech Engineers in SF",
  "description_s3_url": "https://bucket.s3.region.amazonaws.com/audiences/550e8400-e29b-41d4-a716-446655440000/description.json",
  "profiles_created": 2,
  "profiles": [
    {
      "profile_id": "660e8400-e29b-41d4-a716-446655440001",
      "profile_name": "John Doe",
      "linkedin_url": "https://www.linkedin.com/in/johndoe",
      "profile_description_s3_url": "https://bucket.s3.region.amazonaws.com/audiences/550e8400-e29b-41d4-a716-446655440000/profiles/660e8400-e29b-41d4-a716-446655440001/profile.json",
      "posts_s3_url": null
    },
    {
      "profile_id": "660e8400-e29b-41d4-a716-446655440002",
      "profile_name": "Jane Smith",
      "linkedin_url": "https://www.linkedin.com/in/janesmith",
      "profile_description_s3_url": "https://bucket.s3.region.amazonaws.com/audiences/550e8400-e29b-41d4-a716-446655440000/profiles/660e8400-e29b-41d4-a716-446655440002/profile.json",
      "posts_s3_url": null
    }
  ]
}
```

### Response Schema

| Field | Type | Description |
|-------|------|-------------|
| `audience_room_id` | `string` | UUID of the created audience room |
| `audience_room_name` | `string` | Name of the audience room |
| `description_s3_url` | `string` | S3 URL where the description JSON is stored |
| `profiles_created` | `integer` | Number of profiles created |
| `profiles` | `array` | Array of profile objects |

#### Profile Object Schema (in Response)

| Field | Type | Description |
|-------|------|-------------|
| `profile_id` | `string` | UUID of the profile |
| `profile_name` | `string` | Name of the profile |
| `linkedin_url` | `string` | LinkedIn profile URL |
| `profile_description_s3_url` | `string` | S3 URL where the profile description JSON is stored |
| `posts_s3_url` | `string \| null` | S3 URL where posts are stored (null if no posts yet) |

### Error Responses

- **503 Service Unavailable**: Audience database or S3 not configured
  ```json
  {
    "detail": "Audience database connection not available. Please set AUDIENCE_DATABASE_URL."
  }
  ```

- **500 Internal Server Error**: Failed to create audience room
  ```json
  {
    "detail": "Failed to create audience room"
  }
  ```

### Example Request (cURL)

```bash
curl -X POST "https://your-api.vercel.app/api/v1/audience-rooms" \
  -H "Content-Type: application/json" \
  -d '{
    "audience_room_name": "Tech Engineers in SF",
    "audience_description": "Software Engineers in San Francisco",
    "profiles": [
      {
        "name": "John Doe",
        "linkedin_profile_url": "https://www.linkedin.com/in/johndoe"
      }
    ]
  }'
```

### Example Request (JavaScript/Fetch)

```javascript
const response = await fetch('https://your-api.vercel.app/api/v1/audience-rooms', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    audience_room_name: 'Tech Engineers in SF',
    audience_description: 'Software Engineers in San Francisco',
    profiles: [
      {
        name: 'John Doe',
        linkedin_profile_url: 'https://www.linkedin.com/in/johndoe'
      }
    ]
  })
});

const data = await response.json();
console.log(data.audience_room_id);
```

### Notes

- The `audience_room_id` is a UUID generated by the backend
- Each profile gets a unique `profile_id` (UUID)
- Profile descriptions are stored in S3 at: `audiences/{audience_room_id}/profiles/{profile_id}/profile.json`
- Audience description is stored in S3 at: `audiences/{audience_room_id}/description.json`
- Initially, `posts_s3_url` will be `null` until posts are scraped and uploaded

---

## 3. GET /api/v1/audience-rooms/{audience_room_id}/profiles/{profile_id}/posts - Get Profile Posts

Fetch the posts JSON for a specific profile from S3.

### Endpoint
```
GET /api/v1/audience-rooms/{audience_room_id}/profiles/{profile_id}/posts
```

### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `audience_room_id` | `string` | Yes | UUID of the audience room |
| `profile_id` | `string` | Yes | UUID of the profile |

### Response (200 OK)

```json
{
  "profile_id": "660e8400-e29b-41d4-a716-446655440001",
  "audience_room_id": "550e8400-e29b-41d4-a716-446655440000",
  "linkedin_profile_url": "https://www.linkedin.com/in/johndoe",
  "posts": [
    {
      "inputUrl": "https://www.linkedin.com/in/johndoe",
      "text": "Excited to announce...",
      "timestamp": "2024-01-15T10:30:00Z",
      "likes": 150,
      "comments": 25,
      "shares": 10
    }
  ]
}
```

### Response Schema

| Field | Type | Description |
|-------|------|-------------|
| `profile_id` | `string` | UUID of the profile |
| `audience_room_id` | `string` | UUID of the audience room |
| `linkedin_profile_url` | `string` | LinkedIn profile URL |
| `posts` | `array` | Array of post objects (structure depends on scraper output) |

**Note**: The exact structure of post objects depends on the LinkedIn scraper output. Common fields may include:
- `inputUrl`: The LinkedIn profile URL
- `text`: Post content
- `timestamp`: Post timestamp
- `likes`: Number of likes
- `comments`: Number of comments
- `shares`: Number of shares
- Other fields as provided by the scraper

### Error Responses

- **404 Not Found**: Profile not found or doesn't belong to the room, or posts not found
  ```json
  {
    "detail": "Profile not found for given audience room"
  }
  ```
  or
  ```json
  {
    "detail": "Posts not found for this profile"
  }
  ```

- **503 Service Unavailable**: Audience database or S3 not configured
  ```json
  {
    "detail": "Audience database connection not available. Please set AUDIENCE_DATABASE_URL."
  }
  ```

- **500 Internal Server Error**: Failed to fetch posts
  ```json
  {
    "detail": "Failed to fetch posts"
  }
  ```

### Example Request (cURL)

```bash
curl "https://your-api.vercel.app/api/v1/audience-rooms/550e8400-e29b-41d4-a716-446655440000/profiles/660e8400-e29b-41d4-a716-446655440001/posts"
```

### Example Request (JavaScript/Fetch)

```javascript
const audienceRoomId = '550e8400-e29b-41d4-a716-446655440000';
const profileId = '660e8400-e29b-41d4-a716-446655440001';

const response = await fetch(
  `https://your-api.vercel.app/api/v1/audience-rooms/${audienceRoomId}/profiles/${profileId}/posts`
);

const data = await response.json();
console.log(data.posts);
```

### Notes

- This endpoint fetches the posts JSON directly from S3
- If posts haven't been scraped yet, this endpoint will return 404
- The posts structure matches the output from the LinkedIn scraper

---

## 4. GET /api/v1/audience-rooms/{audience_room_id}/description - Get Audience Room Description

Fetch the audience room description JSON from S3.

### Endpoint
```
GET /api/v1/audience-rooms/{audience_room_id}/description
```

### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `audience_room_id` | `string` | Yes | UUID of the audience room |

### Response (200 OK)

```json
{
  "audience_room_id": "550e8400-e29b-41d4-a716-446655440000",
  "audience_room_name": "Tech Engineers in SF",
  "description": "Software Engineers in San Francisco working at Series B companies"
}
```

### Response Schema

| Field | Type | Description |
|-------|------|-------------|
| `audience_room_id` | `string` | UUID of the audience room |
| `audience_room_name` | `string` | Name of the audience room |
| `description` | `string` | Plain-text description of the audience room |

### Error Responses

- **404 Not Found**: Audience room not found or description not found
  ```json
  {
    "detail": "Audience room 550e8400-e29b-41d4-a716-446655440000 not found"
  }
  ```
  or
  ```json
  {
    "detail": "Description not found for this audience room"
  }
  ```

- **503 Service Unavailable**: Audience database or S3 not configured
  ```json
  {
    "detail": "Audience database connection not available. Please set AUDIENCE_DATABASE_URL."
  }
  ```

- **500 Internal Server Error**: Failed to fetch description
  ```json
  {
    "detail": "Failed to fetch description"
  }
  ```

### Example Request (cURL)

```bash
curl "https://your-api.vercel.app/api/v1/audience-rooms/550e8400-e29b-41d4-a716-446655440000/description"
```

### Example Request (JavaScript/Fetch)

```javascript
const audienceRoomId = '550e8400-e29b-41d4-a716-446655440000';

const response = await fetch(
  `https://your-api.vercel.app/api/v1/audience-rooms/${audienceRoomId}/description`
);

const data = await response.json();
console.log(data.description);
```

### Notes

- This endpoint fetches the description JSON directly from S3
- The description is stored when the audience room is created

---

## 5. GET /api/v1/audience-rooms/{audience_room_id}/profiles/{profile_id}/description - Get Profile Description

Fetch the profile description JSON from S3.

### Endpoint
```
GET /api/v1/audience-rooms/{audience_room_id}/profiles/{profile_id}/description
```

### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `audience_room_id` | `string` | Yes | UUID of the audience room |
| `profile_id` | `string` | Yes | UUID of the profile |

### Response (200 OK)

```json
{
  "profile_id": "660e8400-e29b-41d4-a716-446655440001",
  "audience_room_id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "John Doe",
  "age": 32,
  "current_company": "Google",
  "current_location": "San Francisco, California",
  "total_years_experience": 8.5,
  "industry": "Technology",
  "education": "Masters from Stanford University (Computer Science)",
  "linkedin_profile_url": "https://www.linkedin.com/in/johndoe",
  "summary": null
}
```

### Response Schema

| Field | Type | Description |
|-------|------|-------------|
| `profile_id` | `string` | UUID of the profile |
| `audience_room_id` | `string` | UUID of the audience room |
| `name` | `string` | Full name of the profile |
| `age` | `integer \| null` | Age if available |
| `current_company` | `string \| null` | Current company |
| `current_location` | `string \| null` | Current location |
| `total_years_experience` | `float \| null` | Total years of experience |
| `industry` | `string \| null` | Industry |
| `education` | `string \| null` | Education summary |
| `linkedin_profile_url` | `string` | LinkedIn profile URL |
| `summary` | `string \| null` | AI-generated summary (null initially, may be populated later) |

### Error Responses

- **404 Not Found**: Profile not found or doesn't belong to the room, or description not found
  ```json
  {
    "detail": "Profile 660e8400-e29b-41d4-a716-446655440001 not found"
  }
  ```
  or
  ```json
  {
    "detail": "Profile description not found"
  }
  ```

- **503 Service Unavailable**: Audience database or S3 not configured
  ```json
  {
    "detail": "Audience database connection not available. Please set AUDIENCE_DATABASE_URL."
  }
  ```

- **500 Internal Server Error**: Failed to fetch profile description
  ```json
  {
    "detail": "Failed to fetch profile description"
  }
  ```

### Example Request (cURL)

```bash
curl "https://your-api.vercel.app/api/v1/audience-rooms/550e8400-e29b-41d4-a716-446655440000/profiles/660e8400-e29b-41d4-a716-446655440001/description"
```

### Example Request (JavaScript/Fetch)

```javascript
const audienceRoomId = '550e8400-e29b-41d4-a716-446655440000';
const profileId = '660e8400-e29b-41d4-a716-446655440001';

const response = await fetch(
  `https://your-api.vercel.app/api/v1/audience-rooms/${audienceRoomId}/profiles/${profileId}/description`
);

const data = await response.json();
console.log(data);
```

### Notes

- This endpoint fetches the profile description JSON directly from S3
- The `summary` field is initially `null` and may be populated later by AI processing
- Profile descriptions are stored when the audience room is created

---

## 6. POST /api/v1/scrape - Trigger Scraping

Trigger an asynchronous LinkedIn post scraping job using Apify. The job is started immediately and returns a job ID for status polling.

### Endpoint
```
POST /api/v1/scrape
```

### Request Headers
```
Content-Type: application/json
```

### Request Body

```json
{
  "linkedin_urls": [
    "https://www.linkedin.com/in/johndoe",
    "https://www.linkedin.com/in/janesmith"
  ],
  "max_posts": 25,
  "cookies": [
    {
      "domain": ".linkedin.com",
      "name": "li_at",
      "value": "AQEDAS...",
      "path": "/",
      "secure": true,
      "httpOnly": true,
      "hostOnly": false,
      "session": false
    }
  ],
  "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
  "audience_room_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

### Request Body Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `linkedin_urls` | `string[]` | Yes | List of LinkedIn profile URLs to scrape (minimum 1 URL) |
| `max_posts` | `integer` | No | Maximum number of posts to scrape per profile (1-100, default: 25) |
| `cookies` | `Cookie[]` | Yes | List of cookie objects for authentication (minimum 1 cookie) |
| `user_agent` | `string` | Yes | User agent string to use for scraping |
| `audience_room_id` | `string` | No | If provided, scraped posts will be auto-mapped to this audience room when the job completes |

#### Cookie Object Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `domain` | `string` | Yes | Cookie domain (e.g., ".linkedin.com") |
| `name` | `string` | Yes | Cookie name (e.g., "li_at") |
| `value` | `string` | Yes | Cookie value |
| `path` | `string` | No | Cookie path (default: "/") |
| `expirationDate` | `float` | No | Cookie expiration timestamp |
| `hostOnly` | `boolean` | No | Whether cookie is host-only (default: false) |
| `httpOnly` | `boolean` | No | Whether cookie is HTTP-only (default: false) |
| `secure` | `boolean` | No | Whether cookie is secure (default: true) |
| `session` | `boolean` | No | Whether cookie is a session cookie (default: false) |
| `sameSite` | `string` | No | SameSite attribute |
| `storeId` | `string` | No | Store ID |

### Response (200 OK)

```json
{
  "job_id": "770e8400-e29b-41d4-a716-446655440003",
  "status": "PENDING",
  "message": "Scraping job started. Use /api/v1/scrape/status/{job_id} to check progress."
}
```

### Response Schema

| Field | Type | Description |
|-------|------|-------------|
| `job_id` | `string` | UUID of the scraping job (use this to check status) |
| `status` | `string` | Initial status (usually "PENDING") |
| `message` | `string` | Human-readable message |

### Error Responses

- **503 Service Unavailable**: Database connection not available
  ```json
  {
    "detail": "Database connection not available. Please check server configuration."
  }
  ```

- **429 Too Many Requests**: Apify usage limit exceeded
  ```json
  {
    "error": "Apify usage limit exceeded",
    "message": "Error message from Apify",
    "suggestion": "Please check your Apify account usage limits or upgrade your plan."
  }
  ```

- **401 Unauthorized**: Apify authentication failed
  ```json
  {
    "error": "Apify authentication failed",
    "message": "Error message from Apify",
    "suggestion": "Please check your APIFY_API_TOKEN environment variable."
  }
  ```

- **404 Not Found**: Apify actor not found
  ```json
  {
    "error": "Apify actor not found or inaccessible",
    "message": "Error message from Apify",
    "suggestion": "Please verify the actor ID: curious_coder/linkedin-post-search-scraper"
  }
  ```

- **500 Internal Server Error**: Apify service error or other server error
  ```json
  {
    "error": "Apify service error",
    "message": "Error message",
    "error_type": "ErrorType"
  }
  ```

### Example Request (cURL)

```bash
curl -X POST "https://your-api.vercel.app/api/v1/scrape" \
  -H "Content-Type: application/json" \
  -d '{
    "linkedin_urls": ["https://www.linkedin.com/in/johndoe"],
    "max_posts": 25,
    "cookies": [
      {
        "domain": ".linkedin.com",
        "name": "li_at",
        "value": "AQEDAS...",
        "path": "/",
        "secure": true,
        "httpOnly": true
      }
    ],
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
  }'
```

### Example Request (JavaScript/Fetch)

```javascript
const response = await fetch('https://your-api.vercel.app/api/v1/scrape', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    linkedin_urls: ['https://www.linkedin.com/in/johndoe'],
    max_posts: 25,
    cookies: [
      {
        domain: '.linkedin.com',
        name: 'li_at',
        value: 'AQEDAS...',
        path: '/',
        secure: true,
        httpOnly: true
      }
    ],
    user_agent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    audience_room_id: '550e8400-e29b-41d4-a716-446655440000'
  })
});

const data = await response.json();
console.log(data.job_id);
```

### Notes

- The scraping job runs asynchronously. Use the returned `job_id` to poll for status
- If `audience_room_id` is provided, posts will be automatically mapped to profiles in that room when the job completes
- LinkedIn URLs are normalized automatically (scheme, www, trailing slashes)
- The job status will change from "PENDING" → "PROCESSING" → "COMPLETED" or "FAILED"
- Cookies are required for authentication with LinkedIn. Users should export cookies from their browser

---

## 7. GET /api/v1/scrape/status/{job_id} - Get Scrape Status

Check the status of a scraping job. If the job is completed, it will also return processing results.

### Endpoint
```
GET /api/v1/scrape/status/{job_id}
```

### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `job_id` | `string` | Yes | UUID of the scraping job (returned from POST /api/v1/scrape) |

### Response (200 OK) - Job Pending

```json
{
  "job_id": "770e8400-e29b-41d4-a716-446655440003",
  "status": "PENDING",
  "message": "Waiting for Apify run to start..."
}
```

### Response (200 OK) - Job Processing

```json
{
  "job_id": "770e8400-e29b-41d4-a716-446655440003",
  "status": "PROCESSING",
  "apify_status": "RUNNING",
  "message": "Scraping in progress..."
}
```

### Response (200 OK) - Job Completed

```json
{
  "job_id": "770e8400-e29b-41d4-a716-446655440003",
  "status": "COMPLETED",
  "audience_room_id": "550e8400-e29b-41d4-a716-446655440000",
  "posts_found": 45,
  "profiles_updated": 2,
  "profiles_missing": 0,
  "updated": [
    {
      "profile_id": "660e8400-e29b-41d4-a716-446655440001",
      "profile_name": "John Doe",
      "linkedin_url": "https://www.linkedin.com/in/johndoe",
      "audience_room_id": "550e8400-e29b-41d4-a716-446655440000",
      "posts_s3_url": "https://bucket.s3.region.amazonaws.com/audiences/550e8400-e29b-41d4-a716-446655440000/profiles/660e8400-e29b-41d4-a716-446655440001/posts.json"
    }
  ],
  "missing": [],
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-15T10:35:00Z"
}
```

### Response (200 OK) - Job Failed

```json
{
  "job_id": "770e8400-e29b-41d4-a716-446655440003",
  "status": "FAILED",
  "error": "Apify run failed: Error message",
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-15T10:32:00Z"
}
```

### Response Schema

#### Common Fields (All Statuses)

| Field | Type | Description |
|-------|------|-------------|
| `job_id` | `string` | UUID of the scraping job |
| `status` | `string` | Job status: `"PENDING"`, `"PROCESSING"`, `"COMPLETED"`, or `"FAILED"` |
| `message` | `string` | Human-readable status message (for PENDING/PROCESSING) |
| `created_at` | `string` | ISO 8601 timestamp of job creation (for COMPLETED/FAILED) |
| `updated_at` | `string` | ISO 8601 timestamp of last update (for COMPLETED/FAILED) |

#### Additional Fields (PROCESSING Status)

| Field | Type | Description |
|-------|------|-------------|
| `apify_status` | `string` | Apify run status (e.g., "RUNNING", "READY") |

#### Additional Fields (COMPLETED Status)

| Field | Type | Description |
|-------|------|-------------|
| `audience_room_id` | `string \| null` | Audience room ID if provided in scrape request |
| `posts_found` | `integer` | Total number of posts found across all profiles |
| `profiles_updated` | `integer` | Number of profiles that were successfully updated with posts |
| `profiles_missing` | `integer` | Number of profiles that couldn't be matched or updated |
| `updated` | `array` | Array of successfully updated profile objects |
| `missing` | `array` | Array of profiles that couldn't be matched (with reason) |

#### Updated Profile Object Schema

| Field | Type | Description |
|-------|------|-------------|
| `profile_id` | `string` | UUID of the profile |
| `profile_name` | `string` | Name of the profile |
| `linkedin_url` | `string` | LinkedIn profile URL |
| `audience_room_id` | `string` | Audience room ID |
| `posts_s3_url` | `string` | S3 URL where posts are stored |

#### Missing Profile Object Schema

| Field | Type | Description |
|-------|------|-------------|
| `profile_id` | `string` | UUID of the profile |
| `profile_name` | `string` | Name of the profile |
| `linkedin_url` | `string` | LinkedIn profile URL |
| `reason` | `string` | Reason why profile wasn't updated (e.g., "no_posts_found", "missing_linkedin_url", "db_update_failed") |

#### Additional Fields (FAILED Status)

| Field | Type | Description |
|-------|------|-------------|
| `error` | `string` | Error message describing why the job failed |

### Error Responses

- **404 Not Found**: Job not found
  ```json
  {
    "detail": "Job 770e8400-e29b-41d4-a716-446655440003 not found"
  }
  ```

- **503 Service Unavailable**: Database connection not available
  ```json
  {
    "detail": "Database connection not available. Please check server configuration."
  }
  ```

- **500 Internal Server Error**: Error checking job status
  ```json
  {
    "detail": "Error message"
  }
  ```

### Example Request (cURL)

```bash
curl "https://your-api.vercel.app/api/v1/scrape/status/770e8400-e29b-41d4-a716-446655440003"
```

### Example Request (JavaScript/Fetch)

```javascript
const jobId = '770e8400-e29b-41d4-a716-446655440003';

const response = await fetch(
  `https://your-api.vercel.app/api/v1/scrape/status/${jobId}`
);

const data = await response.json();

if (data.status === 'COMPLETED') {
  console.log(`Posts found: ${data.posts_found}`);
  console.log(`Profiles updated: ${data.profiles_updated}`);
} else if (data.status === 'FAILED') {
  console.error(`Job failed: ${data.error}`);
} else {
  console.log(`Job status: ${data.status}`);
}
```

### Polling Example (JavaScript)

```javascript
async function pollScrapeStatus(jobId, maxAttempts = 60, intervalMs = 5000) {
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    const response = await fetch(
      `https://your-api.vercel.app/api/v1/scrape/status/${jobId}`
    );
    const data = await response.json();

    if (data.status === 'COMPLETED') {
      return data;
    } else if (data.status === 'FAILED') {
      throw new Error(`Scraping failed: ${data.error}`);
    }

    // Wait before next poll
    await new Promise(resolve => setTimeout(resolve, intervalMs));
  }

  throw new Error('Polling timeout: job did not complete in time');
}

// Usage
try {
  const result = await pollScrapeStatus('770e8400-e29b-41d4-a716-446655440003');
  console.log('Scraping completed!', result);
} catch (error) {
  console.error('Error:', error.message);
}
```

### Notes

- Poll this endpoint periodically to check job status
- Recommended polling interval: 5-10 seconds
- When status is "COMPLETED", posts are automatically mapped to profiles if `audience_room_id` was provided
- The `updated` array contains profiles that were successfully matched and updated with posts
- The `missing` array contains profiles that couldn't be matched (e.g., no posts found, LinkedIn URL mismatch)
- Job statuses: `PENDING` → `PROCESSING` → `COMPLETED` or `FAILED`

---

## 8. POST /api/v1/audience-rooms/{audience_room_id}/generate-summaries - Generate Profile Summaries

Generate AI-powered summaries, keywords, and highlights for all profiles in an audience room based on their LinkedIn posts. This endpoint processes profiles in parallel and uses OpenAI to analyze post content.

### Endpoint
```
POST /api/v1/audience-rooms/{audience_room_id}/generate-summaries
```

### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `audience_room_id` | `string` | Yes | UUID of the audience room |

### Request Headers
```
Content-Type: application/json
```

### Request Body

This endpoint does not require a request body. All necessary data is retrieved from the database and S3.

### Response (200 OK)

```json
{
  "audience_room_id": "550e8400-e29b-41d4-a716-446655440000",
  "total_profiles": 5,
  "success_count": 3,
  "skipped_count": 1,
  "error_count": 1,
  "profiles": [
    {
      "profile_id": "660e8400-e29b-41d4-a716-446655440001",
      "profile_name": "John Doe",
      "status": "success",
      "summary": "John Doe is currently a Senior Software Engineer at Google, where he focuses on...",
      "highlights_count": 5,
      "keywords_count": 12,
      "error": null
    },
    {
      "profile_id": "660e8400-e29b-41d4-a716-446655440002",
      "profile_name": "Jane Smith",
      "status": "skipped",
      "reason": "no_posts",
      "error": null
    },
    {
      "profile_id": "660e8400-e29b-41d4-a716-446655440003",
      "profile_name": "Bob Johnson",
      "status": "error",
      "reason": "invalid_posts_url",
      "error": "Invalid S3 URL format for posts"
    }
  ]
}
```

### Response Schema

| Field | Type | Description |
|-------|------|-------------|
| `audience_room_id` | `string` | UUID of the audience room |
| `total_profiles` | `integer` | Total number of profiles in the audience room |
| `success_count` | `integer` | Number of profiles successfully processed |
| `skipped_count` | `integer` | Number of profiles skipped (no posts, no description URL, etc.) |
| `error_count` | `integer` | Number of profiles that encountered errors |
| `profiles` | `array` | Array of profile processing results |

#### Profile Result Object Schema

| Field | Type | Description |
|-------|------|-------------|
| `profile_id` | `string` | UUID of the profile |
| `profile_name` | `string` | Name of the profile |
| `status` | `string` | Processing status: `"success"`, `"skipped"`, or `"error"` |
| `summary` | `string \| null` | Truncated summary (first 100 characters) - only present if status is "success" |
| `highlights_count` | `integer \| null` | Number of highlights generated - only present if status is "success" |
| `keywords_count` | `integer \| null` | Number of keywords generated - only present if status is "success" |
| `reason` | `string \| null` | Reason for skip or error (only present if status is "skipped" or "error") |
| `error` | `string \| null` | Error message (only present if status is "error") |

#### Status Values

- **`success`**: Profile was successfully processed. Summary, highlights, and keywords were generated and saved to S3.
- **`skipped`**: Profile was skipped due to missing data. Common reasons:
  - `no_posts`: No posts found in S3
  - `no_posts_url`: Profile doesn't have a posts S3 URL
  - `no_description_url`: Profile doesn't have a description S3 URL
- **`error`**: An error occurred during processing. Common reasons:
  - `invalid_posts_url`: Invalid S3 URL format for posts
  - `invalid_description_url`: Invalid S3 URL format for description
  - `exception`: An exception occurred during processing (see `error` field for details)

### What Gets Generated

For each successfully processed profile, the following data is generated using OpenAI (GPT-4o-mini) and saved to the profile's description JSON in S3:

1. **Summary**: A comprehensive 5-8 sentence summary covering:
   - Current role and company context (including company stage if evident)
   - Main topics, themes, and subjects they frequently post about
   - Posting style and tone (technical, thought leadership, personal reflections, etc.)
   - Key insights, opinions, expertise areas, or perspectives
   - Notable patterns in content
   - Engagement patterns or community involvement
   - Unique value propositions or differentiators

2. **Highlights**: 4-6 key highlights/badges (e.g., "Early + Growth", "Fullstack", "Thought Leader", "Technical Expert", "Series B", "Problem Solver", "Startup Experience") based on:
   - Company stage mentioned
   - Technical skills and expertise demonstrated
   - Content themes and posting style
   - Career patterns or notable experiences
   - Industry recognition or patterns

3. **Keywords**: 10-15 important keywords/phrases for highlighting, including:
   - Technical skills, tools, frameworks, or technologies
   - Programming languages, platforms, or methodologies
   - Key themes, topics, or subject areas
   - Company names, industries, or domains
   - Concepts, practices, or philosophies

### Error Responses

- **404 Not Found**: Audience room not found or no profiles found
  ```json
  {
    "detail": "Audience room 550e8400-e29b-41d4-a716-446655440000 not found"
  }
  ```
  or
  ```json
  {
    "detail": "No profiles found in audience room 550e8400-e29b-41d4-a716-446655440000"
  }
  ```

- **503 Service Unavailable**: Required services not configured
  ```json
  {
    "detail": "Audience database connection not available. Please set AUDIENCE_DATABASE_URL."
  }
  ```
  or
  ```json
  {
    "detail": "OpenAI client not initialized. Please set OPENAI_API_KEY."
  }
  ```
  or
  ```json
  {
    "detail": "S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME."
  }
  ```

- **500 Internal Server Error**: Failed to generate summaries
  ```json
  {
    "detail": "Failed to generate summaries"
  }
  ```

### Example Request (cURL)

```bash
curl -X POST "https://your-api.vercel.app/api/v1/audience-rooms/550e8400-e29b-41d4-a716-446655440000/generate-summaries" \
  -H "Content-Type: application/json"
```

### Example Request (JavaScript/Fetch)

```javascript
const audienceRoomId = '550e8400-e29b-41d4-a716-446655440000';

const response = await fetch(
  `https://your-api.vercel.app/api/v1/audience-rooms/${audienceRoomId}/generate-summaries`,
  {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    }
  }
);

const data = await response.json();
console.log(`Successfully processed: ${data.success_count} profiles`);
console.log(`Skipped: ${data.skipped_count} profiles`);
console.log(`Errors: ${data.error_count} profiles`);

// Process results
data.profiles.forEach(profile => {
  if (profile.status === 'success') {
    console.log(`${profile.profile_name}: Generated summary with ${profile.highlights_count} highlights`);
  } else if (profile.status === 'skipped') {
    console.log(`${profile.profile_name}: Skipped - ${profile.reason}`);
  } else {
    console.error(`${profile.profile_name}: Error - ${profile.error}`);
  }
});
```

### Notes

- **Processing**: Profiles are processed in parallel to avoid timeouts. The endpoint will wait for all profiles to complete before returning results.
- **Prerequisites**: 
  - Profiles must have posts already scraped and stored in S3 (use `/api/v1/scrape` endpoint first)
  - Profiles must have a description JSON stored in S3 (created when the audience room is created)
- **Updates**: The generated summary, highlights, and keywords are automatically saved to the profile's description JSON in S3 and can be retrieved using `GET /api/v1/audience-rooms/{audience_room_id}/profiles/{profile_id}/description`
- **AI Model**: Uses OpenAI's GPT-4o-mini model with a temperature of 0.3 for consistent, focused summaries
- **Timeout Considerations**: For large audience rooms (50+ profiles), processing may take several minutes. The endpoint processes all profiles in parallel, but OpenAI API rate limits may affect total processing time
- **Idempotency**: Running this endpoint multiple times will regenerate summaries. The latest summary will overwrite previous ones
- **Cost**: Each profile requires one OpenAI API call. Monitor your OpenAI usage for large audience rooms

### Workflow Integration

This endpoint is typically used after:
1. Creating an audience room: `POST /api/v1/audience-rooms`
2. Scraping posts: `POST /api/v1/scrape` (with `audience_room_id`)
3. Waiting for scraping to complete: `GET /api/v1/scrape/status/{job_id}`

Then:
4. Generate summaries: `POST /api/v1/audience-rooms/{audience_room_id}/generate-summaries`
5. Fetch updated profile descriptions: `GET /api/v1/audience-rooms/{audience_room_id}/profiles/{profile_id}/description`

---

## General Notes

### Error Handling

All endpoints may return standard HTTP error codes:
- **400 Bad Request**: Invalid request payload
- **404 Not Found**: Resource not found
- **429 Too Many Requests**: Rate limit exceeded
- **500 Internal Server Error**: Server error
- **503 Service Unavailable**: Service/database not available

### Authentication

Currently, the API does not require authentication. In production, you may want to add API keys or OAuth tokens.

### CORS

The API has CORS enabled for all origins. In production, you should restrict this to your frontend domain.

### Rate Limiting

Be aware of rate limits on:
- People Data Labs (PDL) API
- Apify API
- OpenAI API (for summary generation)
- Your backend server

### Data Storage

- **Database**: Metadata (audience rooms, profiles, scrape jobs) is stored in PostgreSQL
- **S3**: Actual data (descriptions, posts) is stored in AWS S3
- **S3 URLs**: S3 URLs in responses are direct URLs. You may need to generate presigned URLs for private buckets (not currently implemented in these endpoints)

### UUIDs

All IDs (audience_room_id, profile_id, job_id) are UUIDs (v4) generated by the backend.

---

## Integration Workflow Example

Here's a typical workflow for integrating these endpoints:

1. **Search for profiles**: `POST /api/v1/search`
2. **Create audience room**: `POST /api/v1/audience-rooms` (with selected profiles)
3. **Trigger scraping**: `POST /api/v1/scrape` (with `audience_room_id`)
4. **Poll for status**: `GET /api/v1/scrape/status/{job_id}` (until COMPLETED)
5. **Generate profile summaries** (optional): `POST /api/v1/audience-rooms/{audience_room_id}/generate-summaries` - Generates AI summaries, highlights, and keywords from posts
6. **Fetch audience description**: `GET /api/v1/audience-rooms/{audience_room_id}/description`
7. **Fetch profile descriptions**: `GET /api/v1/audience-rooms/{audience_room_id}/profiles/{profile_id}/description` (includes generated summary, highlights, and keywords)
8. **Fetch profile posts**: `GET /api/v1/audience-rooms/{audience_room_id}/profiles/{profile_id}/posts`
9. **Classify posts** (optional): `POST /api/classifier/run` - See [CLASSIFIER_API_DOCUMENTATION.md](./CLASSIFIER_API_DOCUMENTATION.md) for details

---

## Support

For questions or issues, please contact the backend team or refer to the main API documentation.

