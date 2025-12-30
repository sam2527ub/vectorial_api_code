# Classifier Prompt Safeguards

This document explains the safeguards in place to prevent timeout and rate limit errors when using the Post Classifier API.

## Overview

The Post Classifier API uses **all examples** from the `PostClassifier` table to provide comprehensive few-shot learning. To prevent issues with very long prompts, several safeguards are implemented.

## Safeguards Implemented

### 1. Example Length Limits

**Maximum Examples Length**: 80,000 characters (~100k tokens conservative limit)
- **Environment Variable**: `MAX_EXAMPLES_LENGTH` (default: 80000)
- **Behavior**: If adding all examples would exceed this limit, examples are included sequentially until the limit is reached
- **Logging**: Warns when examples are skipped due to length limit

**Maximum Individual Post Length**: 2,000 characters per example post
- **Environment Variable**: `MAX_EXAMPLE_POST_LENGTH` (default: 2000)
- **Behavior**: Very long example posts are truncated with `"... [truncated]"` suffix
- **Logging**: Tracks how many posts were truncated

### 2. System Prompt Length Check

**Maximum System Prompt Length**: 100,000 characters (~125k tokens)
- **Environment Variable**: `MAX_SYSTEM_PROMPT_LENGTH` (default: 100000)
- **Behavior**: Warns if the total system prompt (including base prompt, labels, description, and examples) exceeds this limit
- **Action**: Logs warning but does not truncate (allows operation to proceed with warning)

### 3. Rate Limit Protection

**Batch Processing**:
- Reduced batch size: 5 concurrent requests (down from 10)
- Inter-batch delay: 1 second between batches

**Automatic Retry with Exponential Backoff**:
- Detects 429 rate limit errors
- Retries with delays: 2s → 4s → 8s → 16s → 32s
- Maximum 5 retry attempts

### 4. Example Prioritization

Examples are included in **order from the database** (first to last):
- If examples must be limited, earlier examples are prioritized
- This ensures consistency (same examples always included for the same classifier)

## Configuration

All limits can be configured via environment variables:

```bash
# Maximum length for all examples combined (characters)
MAX_EXAMPLES_LENGTH=80000

# Maximum length for individual example posts (characters)
MAX_EXAMPLE_POST_LENGTH=2000

# Maximum total system prompt length (characters) - warning threshold
MAX_SYSTEM_PROMPT_LENGTH=100000

# Groq API timeout (seconds)
GROQ_TIMEOUT_SECONDS=60
```

## Example Behavior

### Scenario 1: Normal Usage (100 examples, each 200 chars)
- ✅ All 100 examples included (~20,000 chars total)
- ✅ No truncation needed
- ✅ No warnings logged

### Scenario 2: Many Examples (200 examples, each 500 chars)
- ✅ First ~160 examples included (until 80k limit reached)
- ⚠️ Last 40 examples skipped (warning logged)
- ✅ No individual post truncation needed

### Scenario 3: Very Long Posts (50 examples, each 3000 chars)
- ✅ All 50 examples included
- ⚠️ Each post truncated to 2000 chars (50 truncations logged)
- ✅ Total examples length: ~100,000 chars (within limit)

### Scenario 4: Extremely Large (500 examples, each 5000 chars)
- ✅ First ~80 examples included (truncated to 2000 chars each = 160k chars, but stops at 80k limit)
- ⚠️ ~420 examples skipped
- ⚠️ ~80 posts truncated

## Logging

The API logs important information:

```
📚 Included 150 examples in prompt (50 skipped due to length limit), 20 posts truncated
⚠️ System prompt is very long (95000 chars). This may cause timeout issues.
```

## Recommendations

1. **For Best Results**: 
   - Keep individual example posts under 2,000 characters
   - Aim for 50-100 high-quality examples rather than 500+ examples
   - Prioritize diverse, representative examples

2. **If You Need More Examples**:
   - Increase `MAX_EXAMPLES_LENGTH` (but monitor for timeout issues)
   - Consider using shorter example posts
   - Monitor system prompt length warnings

3. **Monitoring**:
   - Check logs for truncation/skipping warnings
   - Monitor API response times
   - Adjust limits based on your specific use case

## Token Estimation

Rough token estimation (1 token ≈ 4 characters):
- 80,000 characters ≈ ~20,000 tokens
- 100,000 characters ≈ ~25,000 tokens
- Groq models typically support 32k-128k tokens
- Our limits are conservative to ensure reliability

## Rate Limiting

Even with prompt length safeguards, rate limits can still occur:
- ✅ Automatic retry handles most rate limit issues
- ✅ Batch size and delays reduce rate limit frequency
- ✅ If issues persist, consider:
  - Reducing batch size further (change `batch_size=5` to `batch_size=3`)
  - Increasing inter-batch delay (change `delay_between_batches = 1.0` to `2.0`)

