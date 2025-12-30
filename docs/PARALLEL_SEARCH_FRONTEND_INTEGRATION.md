# Parallel Search API - Frontend Integration Guide

## Overview

The Parallel Search API provides real-time LinkedIn profile discovery and enrichment using Parallel AI's FindAll service combined with Apify profile scraping. Results are streamed via Server-Sent Events (SSE) for immediate UI updates.

---

## API Endpoint

```
POST /api/search/parallel
```

**Base URL:** `https://audience-workflow.vercel.app`

---

## Request Format

### HTTP Headers
```
Content-Type: application/json
Accept: text/event-stream
```

### Request Body

```typescript
{
  query: string;           // Natural language search query
  model?: string;          // "core" or "base" (default: "core")
  match_limit?: number;    // Max results (1-1000, default: 100)
}
```

### Example Request

```json
{
  "query": "Find all software engineers working at Amazon with at least 5 years of experience",
  "model": "core",
  "match_limit": 10
}
```

---

## Response Format (Server-Sent Events)

The API streams events in real-time using SSE. Each event starts with `data: ` followed by JSON.

### Event Types

1. **`profile`** - Initial profile found by Parallel AI
2. **`profile_update`** - Profile enriched with Apify data
3. **`completed`** - Search completed successfully
4. **`error`** - Error occurred

---

## Event Schemas

### 1. Profile Event (Initial)

Sent immediately when Parallel AI finds a LinkedIn profile.

```typescript
{
  type: "profile";
  status: "matched" | "unmatched";
  data: {
    url: string;              // LinkedIn profile URL
    summary: string;          // AI-generated summary
    reasoning: string | Reasoning[];  // Why matched/unmatched
    apify_data: null;         // Will be populated in update event
  }
}

interface Reasoning {
  field: string;
  citations: Citation[];
  reasoning: string;
  confidence: "low" | "medium" | "high";
}

interface Citation {
  title?: string;
  url: string;
  excerpts: string[];
}
```

**Example:**
```json
{
  "type": "profile",
  "status": "matched",
  "data": {
    "url": "https://www.linkedin.com/in/john-doe",
    "summary": "John Doe is a Senior Software Engineer at Amazon with 8 years of experience.",
    "reasoning": [
      {
        "field": "query_match",
        "citations": [],
        "reasoning": "Profile shows current employer as Amazon and job title as Senior Software Engineer with 8+ years experience.",
        "confidence": "high"
      }
    ],
    "apify_data": null
  }
}
```

---

### 2. Profile Update Event (Enriched)

Sent after Apify scrapes the profile (usually 2-10 seconds after initial profile event).

```typescript
{
  type: "profile_update";
  status: "matched" | "unmatched";
  data: {
    url: string;
    summary: string;
    reasoning: string | Reasoning[];
    apify_data: ApifyData | null;  // Enriched profile data
  }
}

interface ApifyData {
  fullName: string;
  jobTitle: string;
  currentCompany: string;
  companyIndustry: string;
  currentLocation: string;
  totalYearsExperience: number;
  education: Education[];
  about: string;
  headline: string;
}

interface Education {
  institution: string;
  degree: string;
  period: {
    startedOn: { year: number };
    endedOn: { year: number };
  };
}
```

**Example:**
```json
{
  "type": "profile_update",
  "status": "matched",
  "data": {
    "url": "https://www.linkedin.com/in/john-doe",
    "summary": "John Doe is a Senior Software Engineer at Amazon with 8 years of experience.",
    "reasoning": [...],
    "apify_data": {
      "fullName": "John Doe",
      "jobTitle": "Senior Software Engineer",
      "currentCompany": "Amazon",
      "companyIndustry": "Software Development",
      "currentLocation": "Seattle, WA, United States",
      "totalYearsExperience": 8.5,
      "education": [
        {
          "institution": "MIT",
          "degree": "BS Computer Science",
          "period": {
            "startedOn": { "year": 2012 },
            "endedOn": { "year": 2016 }
          }
        }
      ],
      "about": "Passionate software engineer...",
      "headline": "Senior Software Engineer at Amazon"
    }
  }
}
```

---

### 3. Completed Event

Sent when the search finishes.

```typescript
{
  type: "completed";
  message: string;
}
```

**Example:**
```json
{
  "type": "completed",
  "message": "Stream ended"
}
```

---

### 4. Error Event

Sent if an error occurs.

```typescript
{
  type: "error";
  message: string;
}
```

**Example:**
```json
{
  "type": "error",
  "message": "Invalid API key"
}
```

---

## Frontend Integration Examples

### Vanilla JavaScript / Fetch API

```javascript
async function searchProfiles(query, onProfile, onUpdate, onComplete, onError) {
  const response = await fetch('http://your-backend-url:8000/api/search/parallel', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      query: query,
      model: 'core',
      match_limit: 10
    })
  });

  const reader = response.body.getReader();
  const decoder = new TextDecoder();

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    const chunk = decoder.decode(value);
    const lines = chunk.split('\n');

    for (const line of lines) {
      if (line.startsWith('data: ')) {
        const data = JSON.parse(line.slice(6));

        switch (data.type) {
          case 'profile':
            onProfile(data);
            break;
          case 'profile_update':
            onUpdate(data);
            break;
          case 'completed':
            onComplete(data);
            break;
          case 'error':
            onError(data);
            break;
        }
      }
    }
  }
}

// Usage
searchProfiles(
  'Find software engineers at Google',
  (profile) => console.log('New profile:', profile),
  (update) => console.log('Profile updated:', update),
  () => console.log('Search completed'),
  (error) => console.error('Error:', error)
);
```

---

### React Hook Implementation

```typescript
// useParallelSearch.ts
import { useState, useCallback, useRef } from 'react';

interface Profile {
  url: string;
  status: 'matched' | 'unmatched';
  summary: string;
  reasoning: any;
  apify_data: ApifyData | null;
}

interface ApifyData {
  fullName: string;
  jobTitle: string;
  currentCompany: string;
  companyIndustry: string;
  currentLocation: string;
  totalYearsExperience: number;
  education: any[];
  about: string;
  headline: string;
}

export function useParallelSearch(baseUrl: string) {
  const [profiles, setProfiles] = useState<Map<string, Profile>>(new Map());
  const [isSearching, setIsSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  const startSearch = useCallback(async (
    query: string,
    matchLimit: number = 10
  ) => {
    setIsSearching(true);
    setError(null);
    setProfiles(new Map());

    // Create abort controller for cancellation
    abortControllerRef.current = new AbortController();

    try {
      const response = await fetch(`${baseUrl}/api/search/parallel`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          query,
          model: 'core',
          match_limit: matchLimit
        }),
        signal: abortControllerRef.current.signal
      });

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const reader = response.body?.getReader();
      const decoder = new TextDecoder();

      if (!reader) {
        throw new Error('Failed to get reader from response');
      }

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value);
        const lines = chunk.split('\n');

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const eventData = JSON.parse(line.slice(6));

            if (eventData.type === 'profile' || eventData.type === 'profile_update') {
              setProfiles(prev => {
                const newMap = new Map(prev);
                const url = eventData.data.url;
                
                // Merge with existing profile data if it exists
                const existing = newMap.get(url);
                newMap.set(url, {
                  url,
                  status: eventData.status,
                  summary: eventData.data.summary,
                  reasoning: eventData.data.reasoning,
                  apify_data: eventData.data.apify_data || existing?.apify_data || null
                });
                
                return newMap;
              });
            } else if (eventData.type === 'completed') {
              setIsSearching(false);
            } else if (eventData.type === 'error') {
              setError(eventData.message);
              setIsSearching(false);
            }
          }
        }
      }
    } catch (err: any) {
      if (err.name !== 'AbortError') {
        setError(err.message);
        setIsSearching(false);
      }
    }
  }, [baseUrl]);

  const cancelSearch = useCallback(() => {
    abortControllerRef.current?.abort();
    setIsSearching(false);
  }, []);

  return {
    profiles: Array.from(profiles.values()),
    isSearching,
    error,
    startSearch,
    cancelSearch
  };
}
```

---

### React Component Example

```typescript
// ParallelSearchComponent.tsx
import React, { useState } from 'react';
import { useParallelSearch } from './useParallelSearch';

export function ParallelSearchComponent() {
  const [query, setQuery] = useState('');
  const { profiles, isSearching, error, startSearch, cancelSearch } = 
    useParallelSearch('http://localhost:8000');

  const handleSearch = () => {
    if (query.trim()) {
      startSearch(query, 10);
    }
  };

  return (
    <div className="parallel-search">
      <div className="search-bar">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Find software engineers at Amazon..."
          disabled={isSearching}
        />
        <button 
          onClick={handleSearch} 
          disabled={isSearching || !query.trim()}
        >
          {isSearching ? 'Searching...' : 'Search'}
        </button>
        {isSearching && (
          <button onClick={cancelSearch}>Cancel</button>
        )}
      </div>

      {error && (
        <div className="error">
          Error: {error}
        </div>
      )}

      <div className="results">
        <h3>Found {profiles.length} profiles</h3>
        {profiles.map((profile) => (
          <div 
            key={profile.url} 
            className={`profile-card ${profile.status}`}
          >
            <div className="profile-header">
              <h4>
                <a href={profile.url} target="_blank" rel="noopener noreferrer">
                  {profile.apify_data?.fullName || 'Loading...'}
                </a>
              </h4>
              <span className={`badge ${profile.status}`}>
                {profile.status}
              </span>
            </div>

            {profile.apify_data ? (
              <div className="profile-details">
                <p className="job-title">{profile.apify_data.jobTitle}</p>
                <p className="company">{profile.apify_data.currentCompany}</p>
                <p className="location">{profile.apify_data.currentLocation}</p>
                <p className="experience">
                  {profile.apify_data.totalYearsExperience} years experience
                </p>
                
                {profile.apify_data.education.length > 0 && (
                  <div className="education">
                    <strong>Education:</strong>
                    {profile.apify_data.education.map((edu, idx) => (
                      <div key={idx}>
                        {edu.degree} - {edu.institution}
                      </div>
                    ))}
                  </div>
                )}
                
                {profile.apify_data.about && (
                  <p className="about">{profile.apify_data.about}</p>
                )}
              </div>
            ) : (
              <div className="loading">
                <span>Loading profile details...</span>
              </div>
            )}

            <div className="summary">
              <strong>AI Summary:</strong>
              <p>{profile.summary}</p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
```

---

## TypeScript Type Definitions

```typescript
// types/parallel-search.ts

export interface ParallelSearchRequest {
  query: string;
  model?: 'core' | 'base';
  match_limit?: number;
}

export interface SSEEvent {
  type: 'profile' | 'profile_update' | 'completed' | 'error';
  status?: 'matched' | 'unmatched';
  data?: ProfileData;
  message?: string;
}

export interface ProfileData {
  url: string;
  summary: string;
  reasoning: string | Reasoning[];
  apify_data: ApifyData | null;
}

export interface Reasoning {
  field: string;
  citations: Citation[];
  reasoning: string;
  confidence: 'low' | 'medium' | 'high';
}

export interface Citation {
  title?: string;
  url: string;
  excerpts: string[];
}

export interface ApifyData {
  fullName: string;
  jobTitle: string;
  currentCompany: string;
  companyIndustry: string;
  currentLocation: string;
  totalYearsExperience: number;
  education: Education[];
  about: string;
  headline: string;
}

export interface Education {
  institution: string;
  degree: string;
  period: {
    startedOn: { year: number };
    endedOn: { year: number };
  };
}
```

---

## Important Notes

### 1. Profile Updates
- Each profile appears **twice**: once as `profile` (initial), then as `profile_update` (enriched)
- Store profiles in a Map keyed by URL to avoid duplicates
- Merge `apify_data` when the update arrives

### 2. Timing
- Initial `profile` events: Immediate (as Parallel AI finds them)
- `profile_update` events: 2-10 seconds later (after Apify scrapes)
- Total search time: 1-5 minutes depending on `match_limit`

### 3. Empty Apify Data
Some profiles may have empty `apify_data` due to:
- LinkedIn privacy settings (profile is private)
- Rate limiting / anti-scraping blocks
- Profile genuinely empty

This is expected behavior - handle gracefully in UI.

### 4. Stream Cancellation
Always implement cancellation:
```javascript
const abortController = new AbortController();
fetch(url, { signal: abortController.signal });
// Later: abortController.abort();
```

### 5. Error Handling
- Network errors
- API authentication errors
- Rate limiting
- Invalid queries

Always show user-friendly error messages.

---

## Testing

### Test Request
```bash
curl -X POST http://localhost:8000/api/search/parallel \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Find software engineers at Amazon",
    "model": "core",
    "match_limit": 5
  }'
```

### Expected Flow
1. **T+0s**: Connection established, streaming starts
2. **T+2s**: First `profile` event (initial match)
3. **T+5s**: First `profile_update` event (enriched with Apify)
4. **T+30s**: More profiles streaming in...
5. **T+2m**: `completed` event, stream closes

---

## Performance Considerations

- **Memory**: Store only displayed profiles, paginate if needed
- **UI Updates**: Debounce rapid updates to prevent jank
- **Loading States**: Show skeleton loaders for pending Apify data
- **Cancellation**: Implement search cancellation for UX

---

## Support

For issues or questions:
- Check backend logs for errors
- Verify API endpoint is reachable
- Ensure request format matches schema
- Check browser console for client-side errors

---

## Example UI States

```typescript
// State machine for profile cards
type ProfileState = 
  | 'initial'       // Just found by Parallel AI
  | 'enriching'     // Waiting for Apify data
  | 'complete'      // Full data available
  | 'failed';       // Apify scraping failed

// UI can show different states:
// initial: Show URL + summary + loading spinner
// enriching: Animate "Fetching details..."
// complete: Show full profile card
// failed: Show "Limited data available" message
```

