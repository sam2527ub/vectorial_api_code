# Quick Answers: Post Classifier Questions

## 🔍 What is Groq?

**Groq** is a fast AI inference API that provides access to LLMs (like Llama 3.1).

**In your code:**
- Used to classify LinkedIn posts
- Model: `llama-3.1-70b-versatile` (70B parameters)
- Fast and cost-effective for batch processing
- Returns JSON with classification labels and scores

**What it does:**
1. Receives a prompt with post content + classification rules
2. Analyzes the post
3. Returns JSON: `{label, score, scores}`

---

## 📝 What Prompts Are We Using?

### 1. **System Prompt** (from `PostClassifier.prompt` field)
```
Default: "You are a helpful classification assistant."
```
- Can be customized in your database
- Sets the AI's role/behavior

### 2. **User Prompt** (built dynamically from PostClassifier table)

Includes:
- ✅ **Classifier name** (from `PostClassifier.name`)
- ✅ **Rules/Description** (from `PostClassifier.description`) 
- ✅ **Available labels** (from `PostClassifier.labels`)
- ✅ **Few-shot examples** (from `PostClassifier.examples` - if provided)
- ✅ **Post content** (from the actual post being classified)
- ✅ **JSON format requirements** (hardcoded in the code)

---

## 🎯 How Are We Classifying Posts?

### Step-by-Step:

1. **Fetch Classifier Config** from `PostClassifier` table
   - Gets: name, prompt, description, labels, examples

2. **For Each Post:**
   - Extract post text (`text`, `content`, or `description` field)
   - Build prompt with:
     - Classifier rules (from `description`)
     - Available labels (from `labels`)
     - Examples (from `examples` - if exists)
     - Post content
   - Send to Groq API
   - Parse JSON response
   - Normalize scores
   - Add labels to post

3. **Upload Updated Posts** back to S3

---

## ✅ Are We Using Examples from PostClassifier Table?

**YES!** Examples from `PostClassifier.examples` are used if they exist.

**How they're used:**
- Added to the prompt as "Few-Shot Examples"
- Helps the model understand classification better
- Format: `Example Post: ... Label: ...`

**Where in code:**
```python
# Lines 1613-1633 in main.py
if classifier_examples:
    # Build examples_text from PostClassifier.examples
    # Add to prompt as "Few-Shot Examples: ..."
```

---

## 📊 How to Check Logs

### Local Development:
```bash
# Run your server and watch terminal output
python main.py
# or
uvicorn main:app --reload

# Logs appear in terminal with:
# INFO: ...
# WARNING: ...
# ERROR: ...
```

### Vercel (Production):
1. Go to Vercel Dashboard → Your Project → Deployments
2. Click latest deployment → Functions tab
3. View "Runtime Logs"

Or CLI:
```bash
vercel logs
```

### What to Look For:

**New logging added - you'll see:**
```
INFO: ================================================================================
INFO: CLASSIFICATION PROMPT BEING SENT TO GROQ:
INFO: System Prompt: ...
INFO: User Prompt (first 1000 chars): ...
INFO: Labels: ['useful', 'not-useful', ...]
INFO: Examples Used: True/False
INFO: ================================================================================
INFO: Raw Groq response (first 500 chars): ...
INFO: Parsed result: {...}
INFO: Extracted - label: ..., score: ..., scores keys: [...]
INFO: Final normalized scores: {...}
```

---

## 🐛 Why 0.5 Default Value?

The code returns **0.5 for first label, 0 for others** when:

1. **JSON parsing fails** - Groq response isn't valid JSON
2. **Missing "scores" field** - Response doesn't have scores object
3. **All scores are 0** - Groq returned but all zeros
4. **Any exception** - Error during classification

**Check logs for:**
- `"Failed to parse Groq JSON response"` → JSON issue
- `"All scores are 0 or invalid"` → Scores problem
- `"No scores dict provided"` → Missing scores field

---

## 🛠️ Debug Tools Added

### 1. Enhanced Logging
- Full prompt logged (first 1000 chars)
- Raw Groq response logged
- Parsed result logged
- Final scores logged

### 2. Preview Endpoint (NEW!)
```
GET /api/classifier/preview-prompt?classifier_id=xxx&sample_post_text=...
```

**Use this to:**
- See exact prompt without calling Groq
- Test different post texts
- Debug prompt issues

**Example:**
```bash
curl "http://localhost:8000/api/classifier/preview-prompt?classifier_id=abc123&sample_post_text=My%20post%20text"
```

Returns:
```json
{
  "classifier_name": "...",
  "system_prompt": "...",
  "user_prompt": "...",
  "labels": [...],
  "examples_used": true/false
}
```

---

## 📋 Quick Checklist

When debugging the 0.5 issue:

- [ ] Check logs for "Raw Groq response"
- [ ] Check logs for "Parsed result"
- [ ] Check logs for "Extracted - label, score, scores"
- [ ] Use preview endpoint to see the prompt
- [ ] Verify PostClassifier table has:
  - [ ] `description` field populated
  - [ ] `labels` as proper array
  - [ ] `examples` in correct format (if using)
- [ ] Verify GROQ_API_KEY is set

---

## 🚀 Next Steps

1. **Run classifier** and check terminal/logs
2. **Look for the new log lines** (they're very detailed now)
3. **Use preview endpoint** to see the prompt
4. **Share logs** if still getting 0.5 defaults

The enhanced logging should show you exactly what's happening! 🎯


