# Parallel Search Preview API Documentation

This document provides comprehensive documentation for the Parallel Search Preview API endpoint. This API is a simplified version of the main Parallel Search API, designed for quick previews and testing with fixed parameters. It uses Server-Sent Events (SSE) to stream LinkedIn profile search results in real-time.

---

## Table of Contents

1. [Overview](#overview)
2. [Endpoint Details](#endpoint-details)
3. [Request Format](#request-format)
4. [Response Format (SSE Events)](#response-format-sse-events)
5. [Frontend Integration Examples](#frontend-integration-examples)
6. [Error Handling](#error-handling)
7. [Best Practices](#best-practices)
8. [Troubleshooting](#troubleshooting)
9. [Comparison with Main Parallel Search API](#comparison-with-main-parallel-search-api)

---

## Overview

The Parallel Search Preview API allows you to quickly preview LinkedIn profile search results using natural language queries. This endpoint is optimized for testing and quick previews with fixed parameters to ensure consistent, fast results.

**Key Features:**
- Real-time streaming results via Server-Sent Events (SSE)
- Natural language search queries
- Automatic profile matching with reasoning
- No polling required - results stream as they're found
- **Fixed parameters for consistency**: Uses "core" model and returns up to 10 matches
- **Automatic profile enrichment**: Each LinkedIn URL is automatically scraped using Apify to fetch full profile information
- **Parallel processing**: Profile scraping happens in parallel for all URLs, providing real-time updates

**Base URL**: `https://audience-workflow.vercel.app`

**Use Cases:**
- Quick testing of search queries
- Previewing results before running full searches
- Demonstrating search functionality
- Rapid iteration on query refinement

---

## Endpoint Details

### Endpoint
```
POST /api/search/parallel/preview
```

### Request Headers
```
Content-Type: application/json
```

### Response Type
```
text/event-stream (Server-Sent Events)
```

### Fixed Parameters

This endpoint uses fixed parameters that cannot be customized:
- **Model**: `"core"` (fixed) - Uses the more accurate model for better results
- **Match Limit**: `10` (fixed) - Returns up to 10 profile matches

For customizable parameters, use the main `/api/search/parallel` endpoint instead.

---

## Request Format

### Request Body Schema

```typescript
interface ParallelSearchPreviewRequest {
  query: string;  // Required: Natural language search query
}
```

### Request Body Example

```json
{
  "query": "Software engineers with 5+ years of experience in Python and machine learning, located in San Francisco"
}
```

### Field Descriptions

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `query` | string | ✅ Yes | Natural language search query describing the profiles you want to find |

### Query Examples

**Simple Query:**
```json
{
  "query": "Product managers at tech startups"
}
```

**Detailed Query:**
```json
{
  "query": "Senior software engineers with experience in React and TypeScript, working at companies with 100-500 employees, located in New York or San Francisco"
}
```

**Role-Specific Query:**
```json
{
  "query": "Data scientists with PhD in machine learning, 3+ years experience, currently working at FAANG companies"
}
```

**Location-Based Query:**
```json
{
  "query": "Marketing directors in London with experience in B2B SaaS"
}
```

---

## Response Format (SSE Events)

The API returns Server-Sent Events (SSE) in real-time. Each event is a JSON object prefixed with `data: `.

### Event Types

The API sends four types of events:

1. **`profile`** - A profile match found (initial event with LinkedIn URL)
2. **`profile_update`** - Profile information fetched from Apify scraper (includes full profile data)
3. **`completed`** - Search completed successfully
4. **`error`** - An error occurred

### Profile Event

Sent when a matching profile is found.

**Event Format:**
```json
{
  "type": "profile",
  "status": "matched" | "unmatched",
  "data": {
    "url": "https://linkedin.com/in/profile-url",
    "summary": "Profile summary text...",
    "reasoning": "Why this profile matches your query...",
    "apify_data": null
  }
}
```

**Example:**
```json
{
  "type": "profile",
  "status": "matched",
  "data": {
    "url": "https://linkedin.com/in/john-doe",
    "summary": "Senior Software Engineer at Google with 8 years of experience in Python and machine learning...",
    "reasoning": "Matches because: 5+ years experience, Python skills, machine learning expertise, located in San Francisco",
    "apify_data": null
  }
}
```

**Field Descriptions:**

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | Always `"profile"` for profile events |
| `status` | string | `"matched"` if profile matches query, `"unmatched"` if it doesn't fully match |
| `data.url` | string | LinkedIn profile URL |
| `data.summary` | string | Brief summary of the profile |
| `data.reasoning` | string | Explanation of why this profile matches (or doesn't match) your query |
| `data.apify_data` | object \| null | Full profile information from Apify scraper (initially null, populated in `profile_update` event) |

### Profile Update Event

Sent when profile information is successfully fetched from the Apify LinkedIn profile scraper. This event is sent after the initial `profile` event, and includes the full profile data scraped from LinkedIn.

**Event Format:**
```json
{
  "type": "profile_update",
  "status": "matched" | "unmatched",
  "data": {
    "url": "https://linkedin.com/in/profile-url",
    "summary": "Profile summary text...",
    "reasoning": "Why this profile matches your query...",
    "apify_data": {
      // Full profile data from Apify scraper
      "fullName": "John Doe",
      "headline": "Senior Software Engineer at Google",
      "location": "San Francisco, CA",
      "summary": "Experienced software engineer...",
      "experience": [...],
      "education": [...],
      // ... other profile fields
    }
  }
}
```

**Example:**
```json
{
  "type": "profile_update",
  "status": "matched",
  "data": {
    "url": "https://linkedin.com/in/john-doe",
    "summary": "Senior Software Engineer at Google...",
    "reasoning": "Matches because: 5+ years experience...",
    "apify_data": {
      "fullName": "John Doe",
      "headline": "Senior Software Engineer at Google",
      "location": "San Francisco, CA",
      "summary": "Experienced software engineer specializing in Python and machine learning...",
      "experience": [
        {
          "title": "Senior Software Engineer",
          "company": "Google",
          "startDate": "2016-01",
          "endDate": null
        }
      ],
      "education": [
        {
          "school": "Stanford University",
          "degree": "BS Computer Science",
          "endDate": "2015"
        }
      ]
    }
  }
}
```

**Field Descriptions:**

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | Always `"profile_update"` for profile update events |
| `status` | string | `"matched"` or `"unmatched"` (same as original profile event) |
| `data.url` | string | LinkedIn profile URL |
| `data.summary` | string | Brief summary from Parallel API |
| `data.reasoning` | string | Match reasoning from Parallel API |
| `data.apify_data` | object | Full profile information scraped from LinkedIn via Apify |

**Note:** Profile updates are fetched in parallel for each LinkedIn URL as they arrive. The `profile_update` event may arrive shortly after the initial `profile` event, depending on Apify scraper response time.

### Completed Event

Sent when the search finishes successfully.

**Event Format:**
```json
{
  "type": "completed",
  "message": "Run completed successfully"
}
```

**Example:**
```json
{
  "type": "completed",
  "message": "Run completed successfully"
}
```

### Error Event

Sent when an error occurs.

**Event Format:**
```json
{
  "type": "error",
  "message": "Error description"
}
```

**Example:**
```json
{
  "type": "error",
  "message": "Failed to start run: Invalid API key"
}
```

---

## Frontend Integration Examples

### JavaScript/TypeScript (EventSource)

```typescript
interface ProfileEvent {
  type: 'profile';
  status: 'matched' | 'unmatched';
  data: {
    url: string;
    summary: string;
    reasoning: string;
    apify_data: any | null;
  };
}

interface ProfileUpdateEvent {
  type: 'profile_update';
  status: 'matched' | 'unmatched';
  data: {
    url: string;
    summary: string;
    reasoning: string;
    apify_data: any;
  };
}

interface CompletedEvent {
  type: 'completed';
  message: string;
}

interface ErrorEvent {
  type: 'error';
  message: string;
}

type SearchEvent = ProfileEvent | ProfileUpdateEvent | CompletedEvent | ErrorEvent;

async function searchParallelPreview(
  query: string,
  onProfile: (profile: ProfileEvent['data']) => void,
  onProfileUpdate: (update: ProfileUpdateEvent['data']) => void,
  onComplete: () => void,
  onError: (error: string) => void
) {
  // Start the preview search
  const response = await fetch('https://audience-workflow.vercel.app/api/search/parallel/preview', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      query,
    }),
  });

  if (!response.ok) {
    const error = await response.json();
    onError(error.detail || 'Failed to start search');
    return;
  }

  // Check if response is SSE stream
  const contentType = response.headers.get('content-type');
  if (contentType?.includes('text/event-stream')) {
    // Read the stream
    const reader = response.body?.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    if (!reader) {
      onError('Failed to read response stream');
      return;
    }

    while (true) {
      const { done, value } = await reader.read();
      
      if (done) {
        break;
      }

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || ''; // Keep incomplete line in buffer

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const event: SearchEvent = JSON.parse(line.slice(6));
            
            if (event.type === 'profile') {
              onProfile(event.data);
            } else if (event.type === 'profile_update') {
              onProfileUpdate(event.data);
            } else if (event.type === 'completed') {
              onComplete();
              return;
            } else if (event.type === 'error') {
              onError(event.message);
              return;
            }
          } catch (e) {
            console.error('Failed to parse event:', e);
          }
        }
      }
    }
  } else {
    // Fallback: try to parse as JSON
    const data = await response.json();
    if (data.type === 'error') {
      onError(data.message);
    }
  }
}

// Usage example
const profiles: Map<string, ProfileEvent['data']> = new Map();

searchParallelPreview(
  'Software engineers with Python experience in San Francisco',
  (profile) => {
    // Handle initial profile event
    profiles.set(profile.url, profile);
    console.log('Found profile:', profile.url);
    updateUI(profile);
  },
  (update) => {
    // Handle profile update with full Apify data
    profiles.set(update.url, update);
    console.log('Profile updated with full data:', update.url);
    updateUIWithFullData(update);
  },
  () => {
    // Search completed
    console.log(`Preview search completed. Found ${profiles.size} profiles.`);
  },
  (error) => {
    // Handle error
    console.error('Search error:', error);
    showError(error);
  }
);
```

### React Hook Example

```typescript
import { useState, useEffect, useCallback } from 'react';

interface Profile {
  url: string;
  summary: string;
  reasoning: string;
  status: 'matched' | 'unmatched';
  apifyData?: any;
}

interface UseParallelPreviewSearchResult {
  profiles: Profile[];
  isLoading: boolean;
  isComplete: boolean;
  error: string | null;
  search: (query: string) => void;
  reset: () => void;
}

export function useParallelPreviewSearch(): UseParallelPreviewSearchResult {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isComplete, setIsComplete] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const search = useCallback(async (query: string) => {
    setProfiles([]);
    setIsLoading(true);
    setIsComplete(false);
    setError(null);

    try {
      const response = await fetch('https://audience-workflow.vercel.app/api/search/parallel/preview', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          query,
        }),
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to start search');
      }

      const reader = response.body?.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      if (!reader) {
        throw new Error('Failed to read response stream');
      }

      // Map to track profiles by URL
      const profileMap = new Map<string, Profile>();

      while (true) {
        const { done, value } = await reader.read();

        if (done) {
          setIsComplete(true);
          setIsLoading(false);
          break;
        }

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const event = JSON.parse(line.slice(6));

              if (event.type === 'profile') {
                const profile: Profile = {
                  url: event.data.url,
                  summary: event.data.summary,
                  reasoning: event.data.reasoning,
                  status: event.status,
                  apifyData: event.data.apify_data,
                };
                profileMap.set(profile.url, profile);
                setProfiles(Array.from(profileMap.values()));
              } else if (event.type === 'profile_update') {
                // Update existing profile with full Apify data
                const existingProfile = profileMap.get(event.data.url);
                if (existingProfile) {
                  existingProfile.apifyData = event.data.apify_data;
                  setProfiles(Array.from(profileMap.values()));
                }
              } else if (event.type === 'completed') {
                setIsComplete(true);
                setIsLoading(false);
                return;
              } else if (event.type === 'error') {
                setError(event.message);
                setIsLoading(false);
                return;
              }
            } catch (e) {
              console.error('Failed to parse event:', e);
            }
          }
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
      setIsLoading(false);
    }
  }, []);

  const reset = useCallback(() => {
    setProfiles([]);
    setIsLoading(false);
    setIsComplete(false);
    setError(null);
  }, []);

  return {
    profiles,
    isLoading,
    isComplete,
    error,
    search,
    reset,
  };
}

// Usage in component
function PreviewSearchComponent() {
  const { profiles, isLoading, isComplete, error, search, reset } = useParallelPreviewSearch();
  const [query, setQuery] = useState('');

  const handleSearch = () => {
    if (query.trim()) {
      search(query);
    }
  };

  return (
    <div>
      <input
        type="text"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Enter search query..."
      />
      <button onClick={handleSearch} disabled={isLoading}>
        {isLoading ? 'Searching...' : 'Preview Search'}
      </button>
      <button onClick={reset}>Reset</button>

      {error && <div className="error">{error}</div>}

      {isLoading && <div>Loading profiles... (up to 10 results)</div>}

      <div>
        <h3>Found {profiles.length} profiles</h3>
        {profiles.map((profile, index) => (
          <div key={index}>
            <a href={profile.url} target="_blank" rel="noopener noreferrer">
              {profile.url}
            </a>
            <p>{profile.summary}</p>
            <small>{profile.reasoning}</small>
            {profile.apifyData && (
              <div>
                <strong>Full Profile Data:</strong>
                <pre>{JSON.stringify(profile.apifyData, null, 2)}</pre>
              </div>
            )}
          </div>
        ))}
      </div>

      {isComplete && <div>Preview search completed!</div>}
    </div>
  );
}
```

### Vue.js Example

```vue
<template>
  <div>
    <input v-model="searchQuery" placeholder="Enter search query" />
    <button @click="startSearch" :disabled="isLoading">
      {{ isLoading ? 'Searching...' : 'Preview Search' }}
    </button>
    <button @click="reset">Reset</button>

    <div v-if="error" class="error">{{ error }}</div>
    <div v-if="isLoading">Loading profiles... (up to 10 results)</div>
    <div v-if="isComplete">Preview search completed! Found {{ profiles.length }} profiles.</div>

    <div v-for="(profile, index) in profiles" :key="index">
      <a :href="profile.url" target="_blank">{{ profile.url }}</a>
      <p>{{ profile.summary }}</p>
      <small>{{ profile.reasoning }}</small>
      <div v-if="profile.apifyData">
        <strong>Full Profile Data:</strong>
        <pre>{{ JSON.stringify(profile.apifyData, null, 2) }}</pre>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue';

interface Profile {
  url: string;
  summary: string;
  reasoning: string;
  status: 'matched' | 'unmatched';
  apifyData?: any;
}

const searchQuery = ref('');
const profiles = ref<Profile[]>([]);
const isLoading = ref(false);
const isComplete = ref(false);
const error = ref<string | null>(null);
const profileMap = new Map<string, Profile>();

async function startSearch() {
  profiles.value = [];
  profileMap.clear();
  isLoading.value = true;
  isComplete.value = false;
  error.value = null;

  try {
    const response = await fetch('https://audience-workflow.vercel.app/api/search/parallel/preview', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        query: searchQuery.value,
      }),
    });

    if (!response.ok) {
      const errorData = await response.json();
      throw new Error(errorData.detail || 'Failed to start search');
    }

    const reader = response.body?.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    if (!reader) {
      throw new Error('Failed to read response stream');
    }

    while (true) {
      const { done, value } = await reader.read();

      if (done) {
        isComplete.value = true;
        isLoading.value = false;
        break;
      }

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const event = JSON.parse(line.slice(6));

            if (event.type === 'profile') {
              const profile: Profile = {
                url: event.data.url,
                summary: event.data.summary,
                reasoning: event.data.reasoning,
                status: event.status,
                apifyData: event.data.apify_data,
              };
              profileMap.set(profile.url, profile);
              profiles.value = Array.from(profileMap.values());
            } else if (event.type === 'profile_update') {
              const existingProfile = profileMap.get(event.data.url);
              if (existingProfile) {
                existingProfile.apifyData = event.data.apify_data;
                profiles.value = Array.from(profileMap.values());
              }
            } else if (event.type === 'completed') {
              isComplete.value = true;
              isLoading.value = false;
              return;
            } else if (event.type === 'error') {
              error.value = event.message;
              isLoading.value = false;
              return;
            }
          } catch (e) {
            console.error('Failed to parse event:', e);
          }
        }
      }
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Unknown error';
    isLoading.value = false;
  }
}

function reset() {
  profiles.value = [];
  profileMap.clear();
  isLoading.value = false;
  isComplete.value = false;
  error.value = null;
}
</script>
```

---

## Error Handling

### HTTP Status Codes

| Status Code | Description | Solution |
|-------------|-------------|----------|
| `200` | Success - Stream started | Continue reading SSE events |
| `400` | Bad Request - Invalid request body | Check request format and required fields |
| `503` | Service Unavailable - API key not configured | Contact backend team to configure `PARALLEL_API_KEY` |
| `500` | Internal Server Error | Check error message in response |

### Error Event Handling

Always listen for `error` events in the SSE stream:

```typescript
if (event.type === 'error') {
  // Handle error
  console.error('Search error:', event.message);
  // Show error to user, stop loading, etc.
}
```

### Common Errors

**"PARALLEL_API_KEY not configured"**
- The backend is missing the Parallel API key
- Contact backend team to set the `PARALLEL_API_KEY` environment variable

**"Failed to start run"**
- The Parallel API rejected the request
- Check that your query is valid and not too long
- Verify API key is valid

**"Request to Parallel API timed out"**
- Network issue or Parallel API is slow
- Retry the request

**"Failed to connect to SSE stream"**
- Connection issue with Parallel API
- Retry the request

---

## Best Practices

### 1. Query Writing

**✅ Good Queries:**
- Be specific: "Senior software engineers with 5+ years Python experience"
- Include location: "Product managers in San Francisco or New York"
- Specify company size: "Engineers at startups with 10-50 employees"
- Mention skills: "Data scientists with TensorFlow and PyTorch experience"

**❌ Avoid:**
- Too vague: "engineers"
- Too many constraints: "Engineers with 10+ years, Python, JavaScript, React, Vue, TypeScript, AWS, GCP, Azure, PhD, located in SF, NYC, Seattle, Austin, working at FAANG..."
- Ambiguous terms without context

### 2. Performance

- This endpoint uses fixed parameters (`model: "core"`, `match_limit: 10`) for consistent, fast previews
- Results are limited to 10 profiles, making it ideal for quick testing
- For larger searches with custom parameters, use `/api/search/parallel` instead
- Cancel previous searches when starting a new one

### 3. User Experience

- Show loading state while `isLoading` is true
- Display profiles as they arrive (real-time updates)
- Show progress: "Found X of 10 profiles..."
- Handle errors gracefully with user-friendly messages
- Allow users to cancel/stop the search
- Indicate that this is a preview (limited to 10 results)
- Show when profile data is being enriched (waiting for `profile_update` events)

### 4. State Management

- Reset state when starting a new search
- Track which profiles are matched vs unmatched
- Store profiles in your state management (Redux, Zustand, etc.)
- Update profiles when `profile_update` events arrive with full Apify data
- Use a Map or object keyed by URL to efficiently update profiles

### 5. Network Handling

- Implement retry logic for failed requests
- Handle network disconnections gracefully
- Show connection status to users
- Consider implementing request cancellation

---

## Troubleshooting

### Profiles not appearing

1. **Check browser console** for errors
2. **Verify SSE stream is working** - check Network tab for `/api/search/parallel/preview` request
3. **Check event parsing** - ensure you're correctly parsing `data: ` prefix
4. **Verify query format** - ensure query is a string, not empty

### Stream stops unexpectedly

1. **Check for error events** - the stream may have sent an error
2. **Check network connection** - SSE requires persistent connection
3. **Check browser compatibility** - ensure browser supports SSE
4. **Check timeout settings** - some proxies/timeouts may close long connections

### Profile updates not arriving

1. **Check Apify API status** - profile enrichment depends on Apify scraper
2. **Verify LinkedIn URLs** - ensure URLs are valid LinkedIn profile URLs
3. **Check for errors in console** - Apify scraping errors may be logged
4. **Wait a bit longer** - profile updates arrive asynchronously after initial profile events

### Slow performance

1. **This endpoint uses "core" model** - it's optimized for accuracy, may be slower than "base"
2. **Limited to 10 results** - should complete faster than larger searches
3. **Simplify query** - complex queries take longer
4. **Check network latency** - Parallel API response time varies

### CORS Issues

If you encounter CORS errors:
- Ensure backend has CORS configured for your frontend domain
- Check that `Access-Control-Allow-Origin` header is set correctly
- Verify preflight requests are handled

---

## Comparison with Main Parallel Search API

### When to Use Preview API

Use `/api/search/parallel/preview` when:
- ✅ You want to quickly test a search query
- ✅ You only need up to 10 results
- ✅ You want consistent, predictable behavior
- ✅ You're building a preview/quick search feature
- ✅ You want to demonstrate search functionality

### When to Use Main API

Use `/api/search/parallel` when:
- ✅ You need more than 10 results (up to 1000)
- ✅ You want to customize the model (`core` or `base`)
- ✅ You need to adjust the match limit
- ✅ You're running production searches

### Key Differences

| Feature | Preview API | Main API |
|---------|-------------|----------|
| **Endpoint** | `/api/search/parallel/preview` | `/api/search/parallel` |
| **Query** | Required | Required |
| **Model** | Fixed: `"core"` | Configurable: `"core"` or `"base"` |
| **Match Limit** | Fixed: `10` | Configurable: `1-1000` (default: `100`) |
| **Use Case** | Quick previews, testing | Production searches |
| **Response Format** | Same SSE format | Same SSE format |
| **Profile Enrichment** | Yes (Apify) | Yes (Apify) |

---

## Additional Resources

- [Server-Sent Events MDN Documentation](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events)
- [Fetch API Streams](https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API/Using_Fetch#processing_a_text_file_line_by_line)
- [Parallel AI Documentation](https://docs.parallel.ai/) (for backend reference)
- [Main Parallel Search API Documentation](./PARALLEL_SEARCH_API_DOCUMENTATION.md) - For full-featured searches

---

## Support

For issues or questions:
1. Check this documentation first
2. Review error messages in browser console
3. Contact the backend team with:
   - Your query
   - Error message (if any)
   - Browser/network details
   - Request/response details from Network tab

---

**Last Updated**: 2025-01-XX
**API Version**: v1
**Endpoint**: `/api/search/parallel/preview`
**Fixed Parameters**: `model: "core"`, `match_limit: 10`

