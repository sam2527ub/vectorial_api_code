# Frontend Update Required: Real-Time Profile Streaming Fix

## Overview

This document explains a critical update needed in the frontend code to properly handle real-time profile streaming from the Parallel Search API endpoints.

---

## The Problem

The backend API has been optimized to **validate profiles through Apify before sending them to the frontend**. This means:

- ✅ **Profiles are validated**: Only real, existing profiles with valid data are sent
- ✅ **Real-time streaming**: Profiles stream as soon as they're validated
- ⚠️ **Event structure changed**: The API now primarily sends `profile_update` events (not initial `profile` events)

### Current Frontend Issue

The frontend code currently expects this flow:
1. `profile` event arrives → Create new profile entry
2. `profile_update` event arrives → Update existing profile entry

But the backend now sends:
1. `profile_update` event only (after validation)

**Result**: When a `profile_update` event arrives for a profile that doesn't exist yet, the frontend code ignores it (because it only updates existing profiles). The profile never appears in the UI.

---

## The Solution

Update the frontend event handlers to treat `profile_update` events as **both creating and updating** profiles. 

**Key principle**: If a profile doesn't exist when a `profile_update` event arrives, **create it**. If it exists, **update it**.

---

## Required Code Changes

### Change Summary

**Current Logic:**
- `profile` event → Create profile
- `profile_update` event → Update profile (only if exists) ❌

**Required Logic:**
- `profile` event → Create profile (if backend sends it)
- `profile_update` event → **Create OR update profile** ✅

---

## Implementation Examples

### React Hook Pattern

**Current Code (Problematic):**
```typescript
if (event.type === 'profile') {
  // Create new profile
  profileMap.set(url, profile);
} else if (event.type === 'profile_update') {
  // ❌ Only updates existing profiles
  const existing = profileMap.get(url);
  if (existing) {
    existing.apifyData = event.data.apify_data;
  }
  // Profile is ignored if it doesn't exist!
}
```

**Fixed Code:**
```typescript
if (event.type === 'profile' || event.type === 'profile_update') {
  const url = event.data.url;
  const existing = profileMap.get(url);
  
  if (existing) {
    // Update existing profile
    existing.apifyData = event.data.apify_data || existing.apifyData;
    existing.summary = event.data.summary || existing.summary;
    existing.reasoning = event.data.reasoning || existing.reasoning;
    existing.status = event.status || existing.status;
  } else {
    // ✅ CREATE new profile (this is the fix!)
    const profile: Profile = {
      url: url,
      summary: event.data.summary || '',
      reasoning: event.data.reasoning || '',
      status: event.status || 'matched',
      apifyData: event.data.apify_data || null,
    };
    profileMap.set(url, profile);
  }
  
  setProfiles(Array.from(profileMap.values()));
}
```

### Vanilla JavaScript Pattern

**Fixed Code:**
```javascript
function handleProfileEvent(event) {
  const url = event.data.url;
  
  if (event.type === 'profile' || event.type === 'profile_update') {
    if (!profiles.has(url)) {
      // ✅ Create new profile
      profiles.set(url, {
        url: url,
        summary: event.data.summary || '',
        reasoning: event.data.reasoning || '',
        status: event.status || 'matched',
        apifyData: event.data.apify_data || null,
      });
    } else {
      // Update existing profile
      const existing = profiles.get(url);
      existing.apifyData = event.data.apify_data || existing.apifyData;
      existing.summary = event.data.summary || existing.summary;
      existing.reasoning = event.data.reasoning || existing.reasoning;
      existing.status = event.status || existing.status;
    }
    updateUI();
  }
}
```

---

## Complete Working Example

Here's a complete React hook implementation that handles the new event structure correctly:

```typescript
import { useState, useCallback } from 'react';

interface Profile {
  url: string;
  summary: string;
  reasoning: string;
  status: 'matched' | 'unmatched';
  apifyData: any | null;
}

export function useParallelPreviewSearch() {
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
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query }),
      });

      if (!response.ok) {
        throw new Error('Failed to start search');
      }

      const reader = response.body?.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      const profileMap = new Map<string, Profile>();

      if (!reader) throw new Error('Failed to read stream');

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const event = JSON.parse(line.slice(6));

              // ✅ Handle both profile and profile_update events
              if (event.type === 'profile' || event.type === 'profile_update') {
                const url = event.data.url;
                const existing = profileMap.get(url);

                if (existing) {
                  // Update existing
                  existing.apifyData = event.data.apify_data || existing.apifyData;
                  existing.summary = event.data.summary || existing.summary;
                  existing.reasoning = event.data.reasoning || existing.reasoning;
                  existing.status = event.status || existing.status;
                } else {
                  // ✅ Create new (critical for profile_update events!)
                  profileMap.set(url, {
                    url,
                    summary: event.data.summary || '',
                    reasoning: event.data.reasoning || '',
                    status: event.status || 'matched',
                    apifyData: event.data.apify_data || null,
                  });
                }

                setProfiles(Array.from(profileMap.values()));
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
              console.error('Parse error:', e);
            }
          }
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
      setIsLoading(false);
    }
  }, []);

  return { profiles, isLoading, isComplete, error, search };
}
```

---

## Event Structure Reference

### profile_update Event

```typescript
{
  type: "profile_update";
  status: "matched" | "unmatched";
  data: {
    url: string;                    // LinkedIn profile URL
    summary: string;                // AI-generated summary
    reasoning: string | array;      // Match reasoning
    apify_data: {                   // Full profile data (already validated)
      fullName: string;
      jobTitle: string;
      currentCompany: string;
      // ... other fields
    } | null;
  }
}
```

**Important Notes:**
- `apify_data` is **always present** (profile is already validated)
- This is typically the **first and only** event for each profile
- Treat this as creating a new profile if the URL doesn't exist in your state

---

## Testing Checklist

After implementing the fix, verify:

- [ ] Profiles appear in real-time (within 2-10 seconds of validation)
- [ ] All validated profiles appear in the UI (none are missing)
- [ ] Each profile has `apify_data` populated (no "Loading..." state needed)
- [ ] No duplicate profiles appear (using Map/Set keyed by URL)
- [ ] Search completion works correctly
- [ ] Error handling works correctly

### Test Query

```typescript
search('Software engineers with Python experience in San Francisco');
```

**Expected Behavior:**
- Profiles stream in one by one as they're validated
- Each profile appears with full data immediately
- Search completes after all profiles are found

---

## Why This Change Was Made

The backend was updated to:

1. **Improve user experience**: Only show real, validated profiles
2. **Real-time streaming**: Stream profiles as soon as they're validated (not waiting for Parallel API events)
3. **Data quality**: Ensure all profiles have meaningful data before showing them

The frontend update ensures compatibility with this improved backend behavior.

---

## Migration Guide

### Step 1: Identify Your Event Handlers

Find where you handle `profile` and `profile_update` events in your codebase.

### Step 2: Update the Logic

Change the `profile_update` handler to:
- Check if profile exists
- If it exists → Update it
- If it doesn't exist → **Create it** (this is the key change)

### Step 3: Test Thoroughly

Test with real searches to ensure all profiles appear correctly.

### Step 4: Optional Cleanup

You can optionally simplify by handling both `profile` and `profile_update` events the same way (unified handler).

---

## Common Pitfalls

1. **Assuming profile exists**: Don't assume a profile exists when handling `profile_update`
2. **Ignoring profile_update**: Don't ignore `profile_update` events for new profiles
3. **Duplicate handling**: Use Map/Set keyed by URL to prevent duplicates
4. **State management**: Ensure state updates trigger re-renders correctly

---

## Support

If you encounter issues:

1. Check browser console for errors
2. Verify event structure matches expected format
3. Ensure Map/Set is used for profile tracking (by URL)
4. Check that state updates are working correctly

---

## Summary

**The Fix in One Sentence:**

Make sure `profile_update` events **create new profiles** if they don't exist, not just update existing ones.

**The Change:**

```typescript
// Before: Only updates
if (existing) { update(); }

// After: Creates or updates
if (existing) { update(); } else { create(); }
```

That's it! This simple change ensures all validated profiles appear in the UI in real-time.

