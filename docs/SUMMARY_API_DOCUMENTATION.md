# Summary API Documentation

This document provides comprehensive documentation for the Profile Summary Generation API endpoint.

**Base URL**: Your backend URL (e.g., `https://your-api.vercel.app` or `http://localhost:8000`)

**Content-Type**: All requests should use `application/json`

---

## Table of Contents

1. [Overview](#overview)
2. [POST /api/v1/audience-rooms/{audience_room_id}/generate-summaries - Generate Profile Summaries](#post-apiv1audience-roomsaudience_room_idgenerate-summaries---generate-profile-summaries)
3. [Data Structures](#data-structures)
4. [What Gets Generated](#what-gets-generated)
5. [Workflow](#workflow)
6. [Error Handling](#error-handling)
7. [Examples](#examples)
8. [Prerequisites](#prerequisites)
9. [Best Practices](#best-practices)

---

## Overview

The Summary API allows you to automatically generate AI-powered summaries, keywords, and highlights for all profiles in an audience room based on their LinkedIn posts. The system uses OpenAI's GPT-4o-mini to analyze post content and create comprehensive professional summaries.

### Key Features

- **Batch Processing**: Generates summaries for all profiles in an audience room
- **Parallel Processing**: Processes multiple profiles concurrently for faster results
- **AI-Powered Analysis**: Uses OpenAI GPT-4o-mini to analyze post content
- **Comprehensive Output**: Generates summaries, highlights, and keywords
- **Automatic Updates**: Updates profile descriptions in S3 with generated data
- **Error Handling**: Gracefully handles missing data and errors

### Use Cases

- Generate professional summaries for audience analysis
- Extract key highlights and badges for profile cards
- Identify important keywords for search and filtering
- Create engaging profile descriptions for marketing materials
- Analyze posting patterns and expertise areas

---

## POST /api/v1/audience-rooms/{audience_room_id}/generate-summaries - Generate Profile Summaries

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

### Processing Flow

1. **Fetch Audience Room**: Retrieves the audience room and all associated profiles from the database
2. **Process Profiles in Parallel**: For each profile:
   - Fetches profile description JSON from S3
   - Fetches posts JSON from S3
   - Extracts post text content
   - Generates summary, highlights, and keywords using OpenAI
   - Updates profile description JSON in S3
3. **Return Results**: Returns summary of processing results for all profiles

---

## Data Structures

### Response Schema

| Field | Type | Description |
|-------|------|-------------|
| `audience_room_id` | `string` | UUID of the audience room |
| `total_profiles` | `integer` | Total number of profiles in the audience room |
| `success_count` | `integer` | Number of profiles successfully processed |
| `skipped_count` | `integer` | Number of profiles skipped (no posts, no description URL, etc.) |
| `error_count` | `integer` | Number of profiles that encountered errors |
| `profiles` | `array` | Array of profile processing results |

### Profile Result Object Schema

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

### Status Values

#### Success Status

- **`status: "success"`**: Profile was successfully processed. Summary, highlights, and keywords were generated and saved to S3.

**Fields present:**
- `summary`: Truncated summary (first 100 characters)
- `highlights_count`: Number of highlights generated
- `keywords_count`: Number of keywords generated
- `error`: `null`

#### Skipped Status

- **`status: "skipped"`**: Profile was skipped due to missing data.

**Common reasons:**
- `no_posts`: No posts found in S3
- `no_posts_url`: Profile doesn't have a posts S3 URL
- `no_description_url`: Profile doesn't have a description S3 URL

**Fields present:**
- `reason`: Reason for skip
- `error`: `null`

#### Error Status

- **`status: "error"`**: An error occurred during processing.

**Common reasons:**
- `invalid_posts_url`: Invalid S3 URL format for posts
- `invalid_description_url`: Invalid S3 URL format for description
- `exception`: An exception occurred during processing (see `error` field for details)

**Fields present:**
- `reason`: Reason for error
- `error`: Detailed error message

---

## What Gets Generated

For each successfully processed profile, the following data is generated using OpenAI (GPT-4o-mini) and saved to the profile's description JSON in S3:

### 1. Summary

A comprehensive 5-8 sentence summary covering:

- **Current Role and Company Context**: Current position, company name, and company stage if evident (Series A/B, startup, growth stage, etc.)
- **Main Topics and Themes**: Subjects they frequently post about
- **Posting Style and Tone**: Technical depth, thought leadership, personal reflections, etc.
- **Key Insights and Expertise**: Opinions, expertise areas, or perspectives they share
- **Content Patterns**: Technical depth, problem-solving focus, industry commentary, etc.
- **Engagement Patterns**: Community involvement if evident
- **Unique Value Propositions**: Differentiators in their content

**Format**: Starts with "{profile_name} is currently..." or "{profile_name} has..." and written in a natural, engaging way.

### 2. Highlights

4-6 key highlights/badges extracted based on:

- **Company Stage**: Series A, B, growth stage, etc.
- **Technical Skills**: Expertise demonstrated in posts
- **Content Themes**: Thought leadership, technical depth, etc.
- **Career Patterns**: Notable experiences mentioned
- **Industry Recognition**: Patterns in their posts

**Examples**: "Early + Growth", "Fullstack", "Thought Leader", "Technical Expert", "Series B", "Problem Solver", "Startup Experience"

### 3. Keywords

10-15 important keywords/phrases for highlighting, including:

- **Technical Skills**: Tools, frameworks, technologies
- **Programming Languages**: Languages, platforms, methodologies
- **Key Themes**: Topics, subject areas
- **Company Names**: Companies, industries, domains
- **Concepts**: Practices, philosophies discussed

---

## Workflow

### Typical Integration Workflow

1. **Search for profiles**: `POST /api/v1/search`
2. **Create audience room**: `POST /api/v1/audience-rooms` (with selected profiles)
3. **Trigger scraping**: `POST /api/v1/scrape` (with `audience_room_id`)
4. **Poll for status**: `GET /api/v1/scrape/status/{job_id}` (until COMPLETED)
5. **Generate summaries**: `POST /api/v1/audience-rooms/{audience_room_id}/generate-summaries` ← **This endpoint**
6. **Fetch updated profile descriptions**: `GET /api/v1/audience-rooms/{audience_room_id}/profiles/{profile_id}/description` (includes generated summary, highlights, and keywords)

### When to Use

- **After Post Scraping**: Use this endpoint after posts have been successfully scraped and stored in S3
- **Before Displaying Profiles**: Generate summaries before displaying profiles to users
- **For Audience Analysis**: Use summaries to understand audience characteristics
- **For Marketing Materials**: Use generated content for marketing and outreach

---

## Error Handling

### Error Responses

#### 404 Not Found

**Audience room not found:**
```json
{
  "detail": "Audience room 550e8400-e29b-41d4-a716-446655440000 not found"
}
```

**No profiles found:**
```json
{
  "detail": "No profiles found in audience room 550e8400-e29b-41d4-a716-446655440000"
}
```

#### 503 Service Unavailable

**Audience database not configured:**
```json
{
  "detail": "Audience database connection not available. Please set AUDIENCE_DATABASE_URL."
}
```

**OpenAI client not initialized:**
```json
{
  "detail": "OpenAI client not initialized. Please set OPENAI_API_KEY."
}
```

**S3 not configured:**
```json
{
  "detail": "S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME."
}
```

#### 500 Internal Server Error

**Failed to generate summaries:**
```json
{
  "detail": "Failed to generate summaries"
}
```

### Handling Partial Success

The endpoint processes all profiles in parallel and returns results for each profile. Even if some profiles fail, others may succeed. Check the `status` field for each profile to determine success:

```javascript
const data = await response.json();

// Check overall results
console.log(`Success: ${data.success_count}`);
console.log(`Skipped: ${data.skipped_count}`);
console.log(`Errors: ${data.error_count}`);

// Handle each profile result
data.profiles.forEach(profile => {
  if (profile.status === 'success') {
    // Profile successfully processed
  } else if (profile.status === 'skipped') {
    // Profile skipped - may need to scrape posts first
  } else {
    // Profile error - check error field for details
  }
});
```

---

## Examples

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

if (!response.ok) {
  throw new Error(`HTTP error! status: ${response.status}`);
}

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

### Example Request (Python)

```python
import requests

audience_room_id = "550e8400-e29b-41d4-a716-446655440000"
url = f"https://your-api.vercel.app/api/v1/audience-rooms/{audience_room_id}/generate-summaries"

response = requests.post(url, headers={"Content-Type": "application/json"})
response.raise_for_status()

data = response.json()
print(f"Successfully processed: {data['success_count']} profiles")
print(f"Skipped: {data['skipped_count']} profiles")
print(f"Errors: {data['error_count']} profiles")

# Process results
for profile in data['profiles']:
    if profile['status'] == 'success':
        print(f"{profile['profile_name']}: Generated summary with {profile['highlights_count']} highlights")
    elif profile['status'] == 'skipped':
        print(f"{profile['profile_name']}: Skipped - {profile['reason']}")
    else:
        print(f"{profile['profile_name']}: Error - {profile['error']}")
```

### Example: Complete Workflow

```javascript
async function generateSummariesForAudience(audienceRoomId) {
  try {
    // Step 1: Generate summaries
    const response = await fetch(
      `https://your-api.vercel.app/api/v1/audience-rooms/${audienceRoomId}/generate-summaries`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      }
    );

    if (!response.ok) {
      throw new Error(`Failed to generate summaries: ${response.statusText}`);
    }

    const result = await response.json();
    
    // Step 2: Fetch updated profile descriptions
    const updatedProfiles = [];
    for (const profile of result.profiles) {
      if (profile.status === 'success') {
        const descResponse = await fetch(
          `https://your-api.vercel.app/api/v1/audience-rooms/${audienceRoomId}/profiles/${profile.profile_id}/description`
        );
        const profileData = await descResponse.json();
        updatedProfiles.push({
          ...profile,
          fullSummary: profileData.summary,
          highlights: profileData.highlights,
          keywords: profileData.keywords
        });
      }
    }

    return {
      summary: result,
      profiles: updatedProfiles
    };
  } catch (error) {
    console.error('Error generating summaries:', error);
    throw error;
  }
}

// Usage
const result = await generateSummariesForAudience('550e8400-e29b-41d4-a716-446655440000');
console.log('Generated summaries for', result.profiles.length, 'profiles');
```

---

## Prerequisites

### Required Services

1. **Audience Database**: PostgreSQL database with audience tables configured
   - Environment variable: `AUDIENCE_DATABASE_URL`
   - Must contain `AudienceRoom` and `AudienceProfile` tables

2. **OpenAI API**: OpenAI API key for AI-powered summary generation
   - Environment variable: `OPENAI_API_KEY`
   - Uses GPT-4o-mini model

3. **AWS S3**: S3 bucket for storing profile descriptions and posts
   - Environment variable: `AUDIENCE_BUCKET_NAME` or `VECTOR_BUCKET_NAME`
   - Must have read/write permissions

### Required Data

1. **Audience Room**: Must exist in the database
2. **Profiles**: Audience room must contain at least one profile
3. **Profile Descriptions**: Profiles must have description JSON stored in S3
4. **Posts**: Profiles must have posts JSON stored in S3 (created via scraping)

### Data Flow

```
Database (AudienceRoom) 
  → Fetch profiles
  → For each profile:
    → S3 (Profile Description JSON)
    → S3 (Posts JSON)
    → OpenAI API (Generate summary)
    → S3 (Update Profile Description JSON)
```

---

## Best Practices

### Performance

- **Parallel Processing**: The endpoint processes all profiles in parallel automatically
- **Large Audience Rooms**: For rooms with 50+ profiles, processing may take several minutes
- **Rate Limits**: Be aware of OpenAI API rate limits for large batches
- **Timeout Considerations**: Consider implementing client-side timeout handling for very large rooms

### Error Handling

- **Check Status**: Always check the `status` field for each profile in the response
- **Handle Skipped Profiles**: Profiles may be skipped if posts haven't been scraped yet
- **Retry Logic**: Consider implementing retry logic for transient errors
- **Logging**: Log errors for debugging and monitoring

### Cost Management

- **OpenAI Usage**: Each profile requires one OpenAI API call
- **Monitor Usage**: Track OpenAI API usage for large audience rooms
- **Batch Size**: Consider processing smaller batches if cost is a concern
- **Caching**: Summaries are regenerated each time - consider caching if needed

### Data Quality

- **Post Quality**: Better summaries are generated from more posts
- **Post Recency**: More recent posts provide better insights
- **Minimum Posts**: At least 5-10 posts per profile recommended for quality summaries

### Idempotency

- **Regeneration**: Running this endpoint multiple times will regenerate summaries
- **Latest Wins**: The latest summary will overwrite previous ones
- **No Versioning**: Previous summaries are not preserved

### Integration Tips

1. **Wait for Scraping**: Ensure posts are scraped before generating summaries
2. **Poll for Completion**: For large rooms, consider polling or using webhooks
3. **Update UI**: Refresh profile descriptions after summary generation
4. **User Feedback**: Show progress indicators for long-running operations

---

## Support

For questions or issues, please contact the backend team or refer to the main API documentation.

---

## Related Documentation

- [API_ENDPOINTS_DOCUMENTATION.md](./API_ENDPOINTS_DOCUMENTATION.md) - Main API documentation
- [CLASSIFIER_API_DOCUMENTATION.md](./CLASSIFIER_API_DOCUMENTATION.md) - Post classification API


