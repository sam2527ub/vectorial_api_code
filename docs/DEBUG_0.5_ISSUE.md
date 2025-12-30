# Debug Guide: Why You're Getting 0.5 Default Values

## Quick Test

Use this endpoint to test a single post and see what Groq returns:

```bash
POST /api/classifier/test-single

{
  "classifier_id": "0e6a4fec-838d-4ae2-8c42-f3b96c1ec063",
  "post_text": "This is a test post about machine learning and AI"
}
```

This will return:
- Full Groq response
- What was parsed
- What scores were extracted
- Any errors

## Check Your Logs

After running the classifier, check your logs for these messages:

### 1. Look for "GROQ RESPONSE RECEIVED"
```
📥 GROQ RESPONSE RECEIVED
Raw Groq response (FULL): {...}
```

**What to check:**
- Is it valid JSON?
- Does it have `"scores"` field?
- Are the scores non-zero?

### 2. Look for "Parsed result"
```
✅ Parsed result: {...}
```

**What to check:**
- Does it have `label`, `score`, and `scores`?
- What are the actual values?

### 3. Look for "Extracted - scores"
```
Extracted - scores: {...}
```

**What to check:**
- Are all scores 0?
- Is the scores dict empty?

### 4. Look for Error Messages
```
❌ JSON DECODE ERROR - RETURNING DEFAULTS
```
or
```
⚠️ All scores are 0 or invalid
```
or
```
⚠️ No scores dict provided or empty
```

## Common Issues

### Issue 1: Groq Returns Text Instead of JSON
**Symptom:** Log shows "JSON DECODE ERROR"
**Solution:** Check the raw response - Groq might not be following the format

### Issue 2: Groq Returns JSON But No "scores" Field
**Symptom:** Log shows "No scores dict provided or empty"
**Solution:** Groq isn't including the scores object. Check the prompt.

### Issue 3: All Scores Are 0
**Symptom:** Log shows "All scores are 0 or invalid"
**Solution:** Groq returned scores but they're all zeros. The code will create a distribution.

## Next Steps

1. **Run the test endpoint** with a single post
2. **Check the response** - it will show you exactly what Groq returned
3. **Share the response** if you still see 0.5 defaults

The test endpoint will show you the full debug info without needing to check logs!


