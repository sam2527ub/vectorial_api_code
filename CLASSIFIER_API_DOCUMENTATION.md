# Classifier API Documentation

This document provides comprehensive documentation for the Post Classifier API endpoint.

**Base URL**: Your backend URL (e.g., `https://your-api.vercel.app` or `http://localhost:8000`)

**Content-Type**: All requests should use `application/json`

---

## Table of Contents

1. [Overview](#overview)
2. [POST /api/classifier/run - Run Classifier](#post-apiclassifierrun---run-classifier)
3. [Data Structures](#data-structures)
4. [Workflow](#workflow)
5. [Error Handling](#error-handling)
6. [Examples](#examples)
7. [Prerequisites](#prerequisites)

---

## Overview

The Classifier API allows you to automatically classify LinkedIn posts using AI-powered classification. The system uses Groq LLM to analyze posts and assign labels with confidence scores based on predefined classifiers.

### Key Features

- **Batch Processing**: Classifies all posts for all profiles in an audience room
- **Parallel Processing**: Processes multiple posts concurrently for faster results
- **Few-Shot Learning**: Uses example posts to improve classification accuracy
- **Confidence Scores**: Returns confidence scores for all available labels
- **Automatic Updates**: Updates posts in S3 with classification results

---

## POST /api/classifier/run - Run Classifier

Runs a classifier on all posts in an audience room. The system will:
1. Fetch classifier configuration from the database
2. Retrieve all profiles in the specified audience room
3. Download posts from S3 for each profile
4. Classify each post using Groq LLM
5. Add classification labels to posts
6. Upload updated posts back to S3

### Endpoint

```
POST /api/classifier/run
```

### Request Headers

```
Content-Type: application/json
```

### Request Body

```json
{
  "audienceRoomId": "38e61624-fd86-410b-a012-a7496b27e43c",
  "classifierId": "9e906c02-a578-4760-adf0-793efde18f2c"
}
```

### Request Body Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `audienceRoomId` | `string` (UUID) | Yes | The ID of the audience room containing profiles to classify |
| `classifierId` | `string` (UUID) | Yes | The ID of the classifier to use for classification |

### Response (200 OK)

```json
{
  "classifier_id": "9e906c02-a578-4760-adf0-793efde18f2c",
  "classifier_name": "Post Usefulness",
  "audience_room_id": "38e61624-fd86-410b-a012-a7496b27e43c",
  "total_profiles_processed": 5,
  "total_posts_classified": 55,
  "profiles": [
    {
      "profile_id": "1f2d7407-8065-4ab2-8ab0-3e9f61047edf",
      "profile_name": "fábio costa",
      "status": "success",
      "posts_classified": 12,
      "updated_posts_url": "https://audience-room-uploads.s3.us-west-2.amazonaws.com/audiences/38e61624-fd86-410b-a012-a7496b27e43c/profiles/1f2d7407-8065-4ab2-8ab0-3e9f61047edf/posts.json"
    },
    {
      "profile_id": "a3088386-f41a-4ae8-b767-e4b5fbc9d1df",
      "profile_name": "mohit seth",
      "status": "success",
      "posts_classified": 29,
      "updated_posts_url": "https://audience-room-uploads.s3.us-west-2.amazonaws.com/audiences/38e61624-fd86-410b-a012-a7496b27e43c/profiles/a3088386-f41a-4ae8-b767-e4b5fbc9d1df/posts.json"
    },
    {
      "profile_id": "85b4974d-f57d-4c43-930f-76f1ad9d987a",
      "profile_name": "christopher yip",
      "status": "skipped",
      "reason": "no_posts_url",
      "posts_classified": 0
    }
  ]
}
```

### Response Schema

| Field | Type | Description |
|-------|------|-------------|
| `classifier_id` | `string` | The ID of the classifier used |
| `classifier_name` | `string` | The name of the classifier |
| `audience_room_id` | `string` | The ID of the audience room processed |
| `total_profiles_processed` | `integer` | Total number of profiles in the room |
| `total_posts_classified` | `integer` | Total number of posts classified across all profiles |
| `profiles` | `array` | Array of profile processing results |

#### Profile Result Schema

| Field | Type | Description |
|-------|------|-------------|
| `profile_id` | `string` | The ID of the profile |
| `profile_name` | `string` | The name of the profile |
| `status` | `string` | Status: `"success"`, `"skipped"`, or `"error"` |
| `posts_classified` | `integer` | Number of posts classified for this profile |
| `updated_posts_url` | `string` | (If successful) S3 URL of updated posts JSON |
| `reason` | `string` | (If skipped/error) Reason for skipping or error message |

---

## Data Structures

### Post Labels Structure

After classification, each post will have a `labels` field added with the following structure:

```json
{
  "labels": {
    "useful": 0.85,
    "not-useful": 0.15,
    "promotional": 0.05,
    "low-content": 0.02,
    "off-domain": 0.01,
    "personal": 0.03,
    "generic-advice": 0.08,
    "gen-quote": 0.04,
    "repost": 0.02,
    "trend": 0.01,
    "classifierId": "9e906c02-a578-4760-adf0-793efde18f2c"
  }
}
```

The `labels` object contains:
- **Label scores**: Each key (except `classifierId`) is a label name with a confidence score (0.0 to 1.0)
- **classifierId**: The ID of the classifier used for this classification

### Classifier Configuration

A classifier in the database contains:

| Field | Type | Description |
|-------|------|-------------|
| `id` | `string` (UUID) | Unique identifier |
| `name` | `string` | Classifier name (e.g., "Post Usefulness") |
| `prompt` | `string` | System prompt for the LLM |
| `description` | `string` | Rules/description for classification |
| `labels` | `array<string>` | Available labels (e.g., `["useful", "not-useful", "promotional"]`) |
| `examples` | `object` | Few-shot learning examples (optional) |

---

## Workflow

### Step-by-Step Process

1. **Request Received**: API receives `audienceRoomId` and `classifierId`

2. **Fetch Classifier**: Retrieves classifier configuration from the audience database
   - Validates classifier exists
   - Extracts labels, prompt, description, and examples

3. **Fetch Audience Room**: Retrieves the audience room and all associated profiles
   - Validates room exists
   - Gets all profiles in the room

4. **Process Each Profile**:
   - Downloads posts JSON from S3 (using `postsS3Url` from profile)
   - Extracts posts array from the JSON structure
   - If no posts URL exists, profile is skipped

5. **Classify Posts**:
   - Processes posts in batches of 10 (parallel processing)
   - For each post:
     - Extracts post text content
     - Constructs prompt with classifier rules, labels, and examples
     - Calls Groq LLM API
     - Parses response to get scores for all labels
     - Normalizes scores (0.0 to 1.0)

6. **Update Posts**:
   - Adds `labels` object to each post
   - Uploads updated posts JSON back to S3
   - Updates profile record with new posts URL

7. **Return Results**: Returns summary of processing results

### Processing Flow Diagram

```
Request
  ↓
Fetch Classifier Config
  ↓
Fetch Audience Room & Profiles
  ↓
For each Profile:
  ├─ Download Posts from S3
  ├─ Extract Posts Array
  ├─ Classify Posts (Batch of 10)
  │   ├─ For each Post:
  │   │   ├─ Extract Text
  │   │   ├─ Call Groq LLM
  │   │   └─ Parse Scores
  ├─ Add Labels to Posts
  ├─ Upload to S3
  └─ Update Profile Record
  ↓
Return Results
```

---

## Error Handling

### HTTP Status Codes

| Status Code | Description |
|-------------|-------------|
| `200` | Success - Classification completed |
| `400` | Bad Request - Invalid input or classifier has no labels |
| `404` | Not Found - Classifier or audience room not found |
| `500` | Internal Server Error - Processing error |
| `503` | Service Unavailable - Missing dependencies (database, S3, Groq) |

### Error Response Format

```json
{
  "detail": "Error message describing what went wrong"
}
```

### Common Error Scenarios

#### 1. Classifier Not Found (404)

```json
{
  "detail": "Classifier 9e906c02-a578-4760-adf0-793efde18f2c not found"
}
```

**Solution**: Verify the `classifierId` exists in the database.

#### 2. Audience Room Not Found (404)

```json
{
  "detail": "Audience room 38e61624-fd86-410b-a012-a7496b27e43c not found"
}
```

**Solution**: Verify the `audienceRoomId` exists in the database.

#### 3. No Labels Defined (400)

```json
{
  "detail": "Classifier has no labels defined"
}
```

**Solution**: Ensure the classifier has a `labels` array with at least one label.

#### 4. Service Unavailable (503)

```json
{
  "detail": "Groq client not initialized. Please set GROQ_API_KEY."
}
```

**Solution**: Ensure `GROQ_API_KEY` environment variable is set.

#### 5. Profile Skipped

Profiles may be skipped for the following reasons:
- `"no_posts_url"`: Profile has no `postsS3Url` set
- `"no_posts"`: Posts JSON exists but contains no posts
- `"invalid_s3_url"`: S3 URL format is invalid

These are not errors - the profile is simply skipped and reported in the response.

---

## Examples

### Example 1: Basic Classification Request

**Request:**

```bash
curl -X POST 'http://localhost:8000/api/classifier/run' \
  -H 'Content-Type: application/json' \
  -d '{
    "audienceRoomId": "38e61624-fd86-410b-a012-a7496b27e43c",
    "classifierId": "9e906c02-a578-4760-adf0-793efde18f2c"
  }'
```

**Response:**

```json
{
  "classifier_id": "9e906c02-a578-4760-adf0-793efde18f2c",
  "classifier_name": "Post Usefulness",
  "audience_room_id": "38e61624-fd86-410b-a012-a7496b27e43c",
  "total_profiles_processed": 3,
  "total_posts_classified": 45,
  "profiles": [
    {
      "profile_id": "1f2d7407-8065-4ab2-8ab0-3e9f61047edf",
      "profile_name": "John Doe",
      "status": "success",
      "posts_classified": 15,
      "updated_posts_url": "https://bucket.s3.region.amazonaws.com/audiences/.../posts.json"
    },
    {
      "profile_id": "a3088386-f41a-4ae8-b767-e4b5fbc9d1df",
      "profile_name": "Jane Smith",
      "status": "success",
      "posts_classified": 20,
      "updated_posts_url": "https://bucket.s3.region.amazonaws.com/audiences/.../posts.json"
    },
    {
      "profile_id": "a5d1453a-035b-4fd0-998e-7b9dc7aa1ffd",
      "profile_name": "Bob Johnson",
      "status": "success",
      "posts_classified": 10,
      "updated_posts_url": "https://bucket.s3.region.amazonaws.com/audiences/.../posts.json"
    }
  ]
}
```

### Example 2: Post Structure After Classification

**Before Classification:**

```json
{
  "type": "article",
  "text": "Excited to share our latest product launch!",
  "url": "https://www.linkedin.com/posts/...",
  "timeSincePosted": "2d"
}
```

**After Classification:**

```json
{
  "type": "article",
  "text": "Excited to share our latest product launch!",
  "url": "https://www.linkedin.com/posts/...",
  "timeSincePosted": "2d",
  "labels": {
    "useful": 0.15,
    "not-useful": 0.10,
    "promotional": 0.85,
    "low-content": 0.05,
    "off-domain": 0.02,
    "personal": 0.03,
    "generic-advice": 0.05,
    "gen-quote": 0.02,
    "repost": 0.01,
    "trend": 0.02,
    "classifierId": "9e906c02-a578-4760-adf0-793efde18f2c"
  }
}
```

### Example 3: JavaScript/TypeScript Usage

```typescript
async function runClassifier(audienceRoomId: string, classifierId: string) {
  const response = await fetch('http://localhost:8000/api/classifier/run', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      audienceRoomId,
      classifierId,
    }),
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail);
  }

  const result = await response.json();
  console.log(`Classified ${result.total_posts_classified} posts`);
  console.log(`Processed ${result.total_profiles_processed} profiles`);
  
  return result;
}

// Usage
runClassifier(
  '38e61624-fd86-410b-a012-a7496b27e43c',
  '9e906c02-a578-4760-adf0-793efde18f2c'
).then(result => {
  result.profiles.forEach(profile => {
    if (profile.status === 'success') {
      console.log(`${profile.profile_name}: ${profile.posts_classified} posts classified`);
    } else {
      console.log(`${profile.profile_name}: ${profile.reason}`);
    }
  });
});
```

### Example 4: Python Usage

```python
import requests

def run_classifier(audience_room_id: str, classifier_id: str):
    url = "http://localhost:8000/api/classifier/run"
    payload = {
        "audienceRoomId": audience_room_id,
        "classifierId": classifier_id
    }
    
    response = requests.post(url, json=payload)
    response.raise_for_status()
    
    result = response.json()
    print(f"Classified {result['total_posts_classified']} posts")
    print(f"Processed {result['total_profiles_processed']} profiles")
    
    return result

# Usage
result = run_classifier(
    "38e61624-fd86-410b-a012-a7496b27e43c",
    "9e906c02-a578-4760-adf0-793efde18f2c"
)

for profile in result['profiles']:
    if profile['status'] == 'success':
        print(f"{profile['profile_name']}: {profile['posts_classified']} posts classified")
    else:
        print(f"{profile['profile_name']}: {profile.get('reason', 'Unknown error')}")
```

---

## Prerequisites

### Environment Variables

The following environment variables must be set:

| Variable | Description | Required |
|----------|-------------|----------|
| `GROQ_API_KEY` | Groq API key for LLM inference | Yes |
| `AUDIENCE_DATABASE_URL` | PostgreSQL connection string for audience database | Yes |
| `AUDIENCE_BUCKET_NAME` or `VECTOR_BUCKET_NAME` | S3 bucket name for storing posts | Yes |
| `AWS_REGION` | AWS region for S3 (default: `us-west-2`) | No |

### Database Requirements

1. **PostClassifier Table**: Must exist in the audience database with:
   - `id` (UUID, primary key)
   - `name` (string)
   - `prompt` (string, nullable)
   - `description` (string, nullable)
   - `labels` (JSON array of strings)
   - `examples` (JSON, nullable)

2. **AudienceRoom Table**: Must exist with profiles

3. **AudienceProfile Table**: Must have `postsS3Url` field populated for profiles with posts

### S3 Requirements

- Posts must be stored in S3 as JSON files
- S3 bucket must be accessible with AWS credentials
- Posts JSON structure should have a `posts` array or be a direct array

### Classifier Configuration

A classifier should be configured with:

1. **Labels**: Array of label names (e.g., `["useful", "not-useful", "promotional"]`)
2. **Description**: Clear rules for classification
3. **Examples** (optional): Few-shot learning examples to improve accuracy

---

## Performance Considerations

### Processing Time

- **Batch Size**: Posts are processed in batches of 10 concurrently
- **Estimated Time**: ~1-2 seconds per post (depending on Groq API response time)
- **Example**: 100 posts ≈ 10-20 seconds total processing time

### Rate Limits

- Groq API has rate limits based on your plan
- The system processes posts in batches to avoid overwhelming the API
- If rate limits are hit, individual post classifications may fail (logged as errors)

### Best Practices

1. **Monitor Progress**: Check the response to see which profiles succeeded/failed
2. **Handle Errors**: Some profiles may be skipped - this is normal if they have no posts
3. **Retry Logic**: Implement retry logic for transient failures
4. **Large Batches**: For very large audience rooms, consider processing in smaller chunks

---

## Troubleshooting

### Issue: No posts are being classified

**Possible Causes:**
- Profiles don't have `postsS3Url` set
- Posts JSON files are empty
- S3 URLs are invalid

**Solution**: Check the response - profiles with issues will have `status: "skipped"` with a `reason` field.

### Issue: Classification scores seem incorrect

**Possible Causes:**
- Classifier description/rules are unclear
- Few-shot examples are not representative
- Post text extraction is failing

**Solution**: 
- Review classifier configuration
- Add better few-shot examples
- Check that posts have `text` field populated

### Issue: API returns 503 Service Unavailable

**Possible Causes:**
- `GROQ_API_KEY` not set
- `AUDIENCE_DATABASE_URL` not set
- S3 bucket not configured

**Solution**: Verify all required environment variables are set.

---

## Additional Notes

- **Idempotency**: Running the classifier multiple times will overwrite previous labels
- **Post Text Extraction**: The system looks for `text`, `content`, or `description` fields in posts
- **Label Normalization**: All scores are normalized to 0.0-1.0 range
- **Case Sensitivity**: Label matching is case-insensitive, but stored labels use original case
- **Concurrent Processing**: Up to 10 posts are processed in parallel per batch

---

## Support

For issues or questions:
1. Check the error response for detailed error messages
2. Review logs for processing details
3. Verify all prerequisites are met
4. Check classifier configuration in the database

