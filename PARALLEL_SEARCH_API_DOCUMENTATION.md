# Parallel Search API Documentation

This document provides comprehensive documentation for integrating the Parallel Search API in your frontend application. This API uses Server-Sent Events (SSE) to stream LinkedIn profile search results in real-time.

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

---

## Overview

The Parallel Search API allows you to search for LinkedIn profiles using natural language queries. The API uses Parallel AI's FindAll service to intelligently match profiles based on your search criteria.

**Key Features:**
- Real-time streaming results via Server-Sent Events (SSE)
- Natural language search queries
- Automatic profile matching with reasoning
- No polling required - results stream as they're found
- Supports up to 1000 matches per search
- **Automatic profile enrichment**: Each LinkedIn URL is automatically scraped using Apify to fetch full profile information
- **Parallel processing**: Profile scraping happens in parallel for all URLs, providing real-time updates

**Base URL**: `https://audience-workflow.vercel.app`

---

## Endpoint Details

### Endpoint
```
POST /api/search/parallel
```

### Request Headers
```
Content-Type: application/json
```

### Response Type
```
text/event-stream (Server-Sent Events)
```

---

## Request Format

### Request Body Schema

```typescript
interface ParallelSearchRequest {
  query: string;           // Required: Natural language search query
  model?: string;          // Optional: "core" (default) or "base"
  match_limit?: number;    // Optional: Maximum matches (1-1000, default: 100)
}
```

### Request Body Example

```json
{
  "query": "Software engineers with 5+ years of experience in Python and machine learning, located in San Francisco",
  "model": "core",
  "match_limit": 100
}
```

### Field Descriptions

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `query` | string | ✅ Yes | - | Natural language search query describing the profiles you want to find |
| `model` | string | ❌ No | `"core"` | Model to use: `"core"` (more accurate, slower) or `"base"` (faster, less accurate) |
| `match_limit` | number | ❌ No | `100` | Maximum number of profiles to return (must be between 1 and 1000) |

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
    "reasoning": "Why this profile matches your query..."
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
    "reasoning": "Matches because: 5+ years experience, Python skills, machine learning expertise, located in San Francisco"
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
| `data.profile_info` | object \| null | Full profile information from Apify scraper (initially null, populated in `profile_update` event) |

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
    "profile_info": {
      // Full profile data from Apify scraper
      // Structure depends on the Apify actor output
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
    "profile_info": {
      "fullName": "John Doe",
      "headline": "Senior Software Engineer at Google",
      "location": "San Francisco, CA",
      "experience": [...],
      "education": [...],
      // ... other profile fields from Apify
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
| `data.profile_info` | object | Full profile information scraped from LinkedIn via Apify |

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

type SearchEvent = ProfileEvent | CompletedEvent | ErrorEvent;

async function searchParallelProfiles(
  query: string,
  model: string = 'core',
  matchLimit: number = 100,
  onProfile: (profile: ProfileEvent['data']) => void,
  onComplete: () => void,
  onError: (error: string) => void
) {
  // First, start the search by making a POST request
  const response = await fetch('https://audience-workflow.vercel.app/api/search/parallel', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      query,
      model,
      match_limit: matchLimit,
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
const profiles: ProfileEvent['data'][] = [];

searchParallelProfiles(
  'Software engineers with Python experience in San Francisco',
  'core',
  100,
  (profile) => {
    // Handle each profile as it arrives
    profiles.push(profile);
    console.log('Found profile:', profile.url);
    updateUI(profile);
  },
  () => {
    // Search completed
    console.log(`Search completed. Found ${profiles.length} profiles.`);
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
}

interface UseParallelSearchResult {
  profiles: Profile[];
  isLoading: boolean;
  isComplete: boolean;
  error: string | null;
  search: (query: string, model?: string, matchLimit?: number) => void;
  reset: () => void;
}

export function useParallelSearch(): UseParallelSearchResult {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isComplete, setIsComplete] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const search = useCallback(
    async (query: string, model: string = 'core', matchLimit: number = 100) => {
      setProfiles([]);
      setIsLoading(true);
      setIsComplete(false);
      setError(null);

      try {
        const response = await fetch('https://audience-workflow.vercel.app/api/search/parallel', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            query,
            model,
            match_limit: matchLimit,
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
                  setProfiles((prev) => [
                    ...prev,
                    {
                      url: event.data.url,
                      summary: event.data.summary,
                      reasoning: event.data.reasoning,
                      status: event.status,
                    },
                  ]);
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
    },
    []
  );

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
function SearchComponent() {
  const { profiles, isLoading, isComplete, error, search, reset } = useParallelSearch();

  const handleSearch = () => {
    search('Software engineers with Python experience');
  };

  return (
    <div>
      <button onClick={handleSearch} disabled={isLoading}>
        {isLoading ? 'Searching...' : 'Search'}
      </button>
      <button onClick={reset}>Reset</button>

      {error && <div className="error">{error}</div>}

      {isLoading && <div>Loading profiles...</div>}

      <div>
        <h3>Found {profiles.length} profiles</h3>
        {profiles.map((profile, index) => (
          <div key={index}>
            <a href={profile.url} target="_blank" rel="noopener noreferrer">
              {profile.url}
            </a>
            <p>{profile.summary}</p>
            <small>{profile.reasoning}</small>
          </div>
        ))}
      </div>

      {isComplete && <div>Search completed!</div>}
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
      {{ isLoading ? 'Searching...' : 'Search' }}
    </button>
    <button @click="reset">Reset</button>

    <div v-if="error" class="error">{{ error }}</div>
    <div v-if="isLoading">Loading profiles...</div>
    <div v-if="isComplete">Search completed! Found {{ profiles.length }} profiles.</div>

    <div v-for="(profile, index) in profiles" :key="index">
      <a :href="profile.url" target="_blank">{{ profile.url }}</a>
      <p>{{ profile.summary }}</p>
      <small>{{ profile.reasoning }}</small>
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
}

const searchQuery = ref('');
const profiles = ref<Profile[]>([]);
const isLoading = ref(false);
const isComplete = ref(false);
const error = ref<string | null>(null);

async function startSearch() {
  profiles.value = [];
  isLoading.value = true;
  isComplete.value = false;
  error.value = null;

  try {
    const response = await fetch('https://audience-workflow.vercel.app/api/search/parallel', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        query: searchQuery.value,
        model: 'core',
        match_limit: 100,
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
              profiles.value.push({
                url: event.data.url,
                summary: event.data.summary,
                reasoning: event.data.reasoning,
                status: event.status,
              });
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

- Use `model: "base"` for faster results (less accurate)
- Use `model: "core"` for better accuracy (slower)
- Set appropriate `match_limit` - don't request 1000 if you only need 50
- Cancel previous searches when starting a new one

### 3. User Experience

- Show loading state while `isLoading` is true
- Display profiles as they arrive (real-time updates)
- Show progress: "Found X profiles so far..."
- Handle errors gracefully with user-friendly messages
- Allow users to cancel/stop the search
- Debounce search input if allowing live search

### 4. State Management

- Reset state when starting a new search
- Track which profiles are matched vs unmatched
- Store profiles in your state management (Redux, Zustand, etc.)
- Consider pagination if you expect many results

### 5. Network Handling

- Implement retry logic for failed requests
- Handle network disconnections gracefully
- Show connection status to users
- Consider implementing request cancellation

---

## Troubleshooting

### Profiles not appearing

1. **Check browser console** for errors
2. **Verify SSE stream is working** - check Network tab for `/api/search/parallel` request
3. **Check event parsing** - ensure you're correctly parsing `data: ` prefix
4. **Verify query format** - ensure query is a string, not empty

### Stream stops unexpectedly

1. **Check for error events** - the stream may have sent an error
2. **Check network connection** - SSE requires persistent connection
3. **Check browser compatibility** - ensure browser supports SSE
4. **Check timeout settings** - some proxies/timeouts may close long connections

### Slow performance

1. **Use `model: "base"`** for faster results
2. **Reduce `match_limit`** if you don't need many results
3. **Simplify query** - complex queries take longer
4. **Check network latency** - Parallel API response time varies

### CORS Issues

If you encounter CORS errors:
- Ensure backend has CORS configured for your frontend domain
- Check that `Access-Control-Allow-Origin` header is set correctly
- Verify preflight requests are handled

---

## Additional Resources

- [Server-Sent Events MDN Documentation](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events)
- [Fetch API Streams](https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API/Using_Fetch#processing_a_text_file_line_by_line)
- [Parallel AI Documentation](https://docs.parallel.ai/) (for backend reference)

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
**Endpoint**: `/api/search/parallel`

