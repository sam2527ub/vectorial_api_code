# Parallel Search API - Quick Start Guide

## TL;DR

Stream LinkedIn profiles in real-time with AI matching + profile enrichment.

---

## Quick Integration (Copy-Paste Ready)

### 1. Basic Fetch

```javascript
const response = await fetch('https://audience-workflow.vercel.app/api/search/parallel', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    query: 'Find software engineers at Amazon',
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
      const event = JSON.parse(line.slice(6));
      console.log(event); // Handle event
    }
  }
}
```

---

### 2. React Hook (Production Ready)

```typescript
// useParallelSearch.ts
import { useState, useCallback } from 'react';

export function useParallelSearch(baseUrl: string) {
  const [profiles, setProfiles] = useState<Map<string, any>>(new Map());
  const [isSearching, setIsSearching] = useState(false);

  const startSearch = useCallback(async (query: string) => {
    setIsSearching(true);
    setProfiles(new Map());

    const response = await fetch(`${baseUrl}/api/search/parallel`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, match_limit: 10 })
    });

    const reader = response.body!.getReader();
    const decoder = new TextDecoder();

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const chunk = decoder.decode(value);
      const lines = chunk.split('\n');

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const event = JSON.parse(line.slice(6));

          if (event.type === 'profile' || event.type === 'profile_update') {
            setProfiles(prev => new Map(prev).set(
              event.data.url,
              event.data
            ));
          } else if (event.type === 'completed') {
            setIsSearching(false);
          }
        }
      }
    }
  }, [baseUrl]);

  return {
    profiles: Array.from(profiles.values()),
    isSearching,
    startSearch
  };
}
```

**Usage:**
```tsx
function MyComponent() {
  const { profiles, isSearching, startSearch } = useParallelSearch('http://localhost:8000');

  return (
    <>
      <button onClick={() => startSearch('Find engineers at Google')}>
        Search
      </button>
      {profiles.map(p => (
        <div key={p.url}>
          {p.apify_data?.fullName || 'Loading...'}
        </div>
      ))}
    </>
  );
}
```

---

## Event Types Reference

| Event Type | When | Contains |
|------------|------|----------|
| `profile` | Profile found by AI | URL, summary, reasoning |
| `profile_update` | Profile scraped by Apify | + full profile data |
| `completed` | Search finished | Done message |
| `error` | Something failed | Error message |

---

## Key Fields You'll Use

```typescript
// What you get from profile_update event
{
  type: "profile_update",
  status: "matched",  // or "unmatched"
  data: {
    url: "https://linkedin.com/in/...",
    summary: "AI summary...",
    apify_data: {
      fullName: "John Doe",
      jobTitle: "Senior Engineer",
      currentCompany: "Amazon",
      totalYearsExperience: 8,
      education: [...],
      about: "...",
      // ... more fields
    }
  }
}
```

---

## Request Options

```typescript
{
  query: string;           // Required: "Find X at Y"
  model?: "core" | "base"; // Optional: default "core"
  match_limit?: number;    // Optional: 1-1000, default 100
}
```

---

## Important Patterns

### 1. De-duplicate by URL
Profiles come twice (initial + update), use Map:
```javascript
const profilesMap = new Map();
profilesMap.set(event.data.url, event.data); // Auto-updates
```

### 2. Handle Loading States
```jsx
{profile.apify_data ? (
  <div>{profile.apify_data.fullName}</div>
) : (
  <div>Loading details...</div>
)}
```

### 3. Show Match Status
```jsx
<span className={profile.status}>
  {profile.status === 'matched' ? '✓ Match' : '○ Not Match'}
</span>
```

---

## Common Issues

| Problem | Solution |
|---------|----------|
| CORS error | Add CORS middleware on backend |
| Connection closes early | Check backend logs for errors |
| Empty apify_data | Normal - some profiles are private |
| Slow updates | Expected - Apify takes 2-10s per profile |

---

## Next Steps

1. Copy the React hook above
2. Install in your component
3. Call `startSearch(query)`
4. Render `profiles` array
5. Done! 🎉

**See full docs:** `PARALLEL_SEARCH_FRONTEND_INTEGRATION.md`

