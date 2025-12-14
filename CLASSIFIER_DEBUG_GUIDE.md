# Post Classifier Debug Guide

## 📋 Table of Contents
1. [What is Groq?](#what-is-groq)
2. [How Classification Works](#how-classification-works)
3. [What Prompts Are Used](#what-prompts-are-used)
4. [How to Check Logs](#how-to-check-logs)
5. [Troubleshooting the 0.5 Default Issue](#troubleshooting-the-05-default-issue)

---

## What is Groq?

**Groq** is a fast AI inference platform that provides access to Large Language Models (LLMs) like Llama 3.1. It's being used in your code to classify LinkedIn posts.

### What Groq Does in Your Code:
1. **Receives a prompt** with:
   - The post content to classify
   - Classification rules/description
   - Available labels
   - Example posts (if provided)
   
2. **Returns a JSON response** with:
   - `label`: The primary classification label
   - `score`: Confidence score for the primary label (0.0 to 1.0)
   - `scores`: Object with scores for ALL labels

3. **Model Used**: `llama-3.1-70b-versatile` - a 70 billion parameter model optimized for speed

### Why Groq?
- **Fast**: Much faster than OpenAI's API
- **Cost-effective**: Cheaper for batch processing
- **Good for classification**: Works well for structured tasks like classification

---

## How Classification Works

### Step-by-Step Flow:

```
1. API Request (/api/classifier/run)
   ↓
2. Fetch Classifier from Database (PostClassifier table)
   - Gets: name, prompt, description, labels, examples
   ↓
3. Fetch Audience Room & Profiles
   ↓
4. For each Profile:
   a. Download posts from S3
   b. For each post:
      - Extract post text
      - Build classification prompt
      - Send to Groq API
      - Parse response
      - Add labels to post
   c. Upload updated posts back to S3
   ↓
5. Return summary of results
```

### Key Function: `classify_post_with_groq()`

This function:
1. Takes a post and classifier configuration
2. Builds a detailed prompt
3. Sends it to Groq
4. Parses the JSON response
5. Normalizes scores
6. Returns classification results

---

## What Prompts Are Used

### 1. **System Prompt** (from `PostClassifier.prompt` field)
- Default: `"You are a helpful classification assistant."`
- This is the system message that sets the AI's role
- Can be customized in your PostClassifier table

### 2. **User Prompt** (constructed dynamically)

The user prompt includes:

```
You are a classification assistant. Your task is to classify LinkedIn posts based on the following rules and labels.

Classifier: {classifier_name}

Rules/Description:
{classifier_description}  ← FROM PostClassifier.description

Available Labels: {labels_str}  ← FROM PostClassifier.labels

Few-Shot Examples:  ← FROM PostClassifier.examples (if provided)
Example Post: ...
Label: ...

Now classify the following post:

Post Content:
{post_text}

CRITICAL: You MUST respond with a valid JSON object...
[Detailed JSON format requirements]
```

### 3. **What Data Comes From PostClassifier Table?**

| Field | Used For | Where |
|-------|----------|-------|
| `name` | Classifier name in prompt | Shown in prompt |
| `prompt` | System prompt | Sent as system message to Groq |
| `description` | Classification rules | Main content of user prompt |
| `labels` | Available labels | Used in prompt and validation |
| `examples` | Few-shot learning | Added to prompt if provided |

**YES, examples from PostClassifier table ARE used!** They're added to the prompt as "Few-Shot Examples" to help the model understand the classification task better.

---

## How to Check Logs

### Option 1: Local Development (Terminal)

If running locally with `uvicorn` or `python main.py`:

```bash
# Logs will appear in your terminal
# Look for lines starting with:
# INFO:     ...
# WARNING:  ...
# ERROR:    ...
```

### Option 2: Vercel Logs

If deployed on Vercel:

1. Go to your Vercel dashboard
2. Select your project
3. Click on "Deployments"
4. Click on the latest deployment
5. Click on "Functions" tab
6. Click on your function
7. View "Runtime Logs"

Or use Vercel CLI:
```bash
vercel logs
```

### Option 3: Add Logging to Response

You can temporarily add logging to see what's happening:

The code now logs:
- ✅ Full prompt being sent (first 1000 chars)
- ✅ Raw Groq response (first 500 chars)
- ✅ Parsed result
- ✅ Extracted label and scores
- ✅ Final normalized scores

### What to Look For in Logs:

```
INFO: ================================================================================
INFO: CLASSIFICATION PROMPT BEING SENT TO GROQ:
INFO: ================================================================================
INFO: System Prompt: ...
INFO: User Prompt (first 1000 chars): ...
INFO: Labels: ['useful', 'not-useful', ...]
INFO: Examples Used: True/False
INFO: ================================================================================
INFO: Raw Groq response (first 500 chars): ...
INFO: Parsed result: {'label': '...', 'score': ..., 'scores': {...}}
INFO: Extracted - label: ..., score: ..., scores keys: [...]
INFO: Final normalized scores: {...}
```

---

## Troubleshooting the 0.5 Default Issue

### Why You're Getting 0.5 and 0s:

The code returns default values (0.5 for first label, 0 for others) when:

1. **JSON parsing fails** - Groq response isn't valid JSON
2. **Missing "scores" field** - Response doesn't have the scores object
3. **All scores are 0** - Groq returned scores but they're all zero
4. **Exception occurs** - Any error during classification

### Debugging Steps:

#### Step 1: Check the Raw Groq Response

Look for this log line:
```
INFO: Raw Groq response (first 500 chars): ...
```

**What to check:**
- Is it valid JSON?
- Does it have `"scores"` field?
- Are the scores non-zero?

#### Step 2: Check Parsed Result

Look for:
```
INFO: Parsed result: {...}
```

**What to check:**
- Does it have `label`, `score`, and `scores`?
- Are the scores in `scores` object non-zero?

#### Step 3: Check What Was Extracted

Look for:
```
INFO: Extracted - label: ..., score: ..., scores keys: [...]
```

**What to check:**
- Is `scores` a dict?
- Does it have all your labels as keys?
- Are the values numbers (not strings)?

#### Step 4: Check Final Scores

Look for:
```
INFO: Final normalized scores: {...}
```

**What to check:**
- Are all scores 0 except one?
- Does the sum make sense?

### Common Issues:

#### Issue 1: Groq Returns Invalid JSON
**Symptom**: Log shows "Failed to parse Groq JSON response"
**Solution**: The code tries to extract JSON, but if it fails, check the raw response format

#### Issue 2: Missing "scores" Field
**Symptom**: Log shows "scores keys: []" or "not a dict"
**Solution**: Groq isn't following the format. Check the prompt - it should be very explicit.

#### Issue 3: All Scores Are 0
**Symptom**: Log shows "All scores are 0 or invalid"
**Solution**: Groq returned scores but they're all 0. The code will create a distribution from the primary label score.

#### Issue 4: Wrong Label Names
**Symptom**: Log shows "Label 'X' not in available labels"
**Solution**: Groq returned a label that doesn't match. Check case sensitivity.

### Quick Fix Test:

Add this temporary endpoint to see what's happening:

```python
@app.get("/api/classifier/test-prompt")
async def test_classifier_prompt(classifier_id: str):
    # Fetch classifier and show what prompt would be sent
    # This helps debug without actually calling Groq
```

---

## Example: What a Good Response Looks Like

### Good Groq Response:
```json
{
  "label": "useful",
  "score": 0.85,
  "scores": {
    "useful": 0.85,
    "not-useful": 0.10,
    "promotional": 0.03,
    "low-content": 0.02
  }
}
```

### Bad Groq Response (causes defaults):
```json
{
  "label": "useful"
  // Missing "score" and "scores" fields
}
```

Or:
```json
{
  "label": "useful",
  "score": 0.85
  // Missing "scores" object
}
```

---

## Next Steps

1. **Run the classifier** and check your logs
2. **Look for the log lines** mentioned above
3. **Share the logs** if you still see 0.5 defaults
4. **Check your PostClassifier table** to ensure:
   - `description` field has clear rules
   - `labels` is a proper array
   - `examples` (if provided) are in the right format

---

## Need More Help?

If you're still getting 0.5 defaults after checking logs:

1. Copy the log output (especially "Raw Groq response" and "Parsed result")
2. Check your PostClassifier table data
3. Verify GROQ_API_KEY is set correctly
4. Try with a simpler classifier (fewer labels) to test

The enhanced logging should now show you exactly what's happening at each step!


