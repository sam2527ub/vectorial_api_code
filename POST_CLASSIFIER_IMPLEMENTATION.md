# Post Classifier API Implementation Guide

This document explains how the Post Classifier API works, how it uses data from the `PostClassifier` table, and analyzes the labeling implementation.

## Table of Contents

1. [Overview](#overview)
2. [PostClassifier Table Schema](#postclassifier-table-schema)
3. [How the API Uses PostClassifier Data](#how-the-api-uses-postclassifier-data)
4. [Prompt Construction](#prompt-construction)
5. [Labeling Process](#labeling-process)
6. [Labeling Analysis](#labeling-analysis)
7. [API Endpoints](#api-endpoints)
8. [Example Flow](#example-flow)

---

## Overview

The Post Classifier API uses Groq LLM (Large Language Model) to classify LinkedIn posts based on predefined classifiers stored in the `PostClassifier` database table. The system performs few-shot learning using examples from the database and applies custom rules and labels.

**Main Components:**
- **PostClassifier Table**: Stores classifier configuration (name, prompt, description, labels, examples)
- **Groq LLM**: Performs the actual classification using the configured prompts
- **Batch Processing**: Classifies multiple posts in parallel for efficiency

---

## PostClassifier Table Schema

The `PostClassifier` table (defined in `audience.schema.prisma`) has the following structure:

```prisma
model PostClassifier {
  id          String   @id @default(uuid())
  name        String                    // Name of the classifier (e.g., "Post Usefulness")
  prompt      String?                   // Custom system prompt for the LLM
  description String?                   // Rules/description for classification
  labels      Json                      // Array of strings like ["USEFUL", "GEN-QUOTE", "PERSONAL", ...]
  examples    Json?                     // JSON field containing few-shot examples
  createdAt   DateTime @default(now())
  updatedAt   DateTime @updatedAt
}
```

### Key Fields Explained

1. **`labels`** (JSON): Array of classification labels. Example: `["USEFUL", "GEN-QUOTE", "PERSONAL", "PROMO", "TREND", "REPOST", "GENERIC-ADVICE", "OFF-DOMAIN", "LOW-CONTENT"]`

2. **`prompt`** (String, optional): **Custom classification rules and instructions that serve as the system prompt**. This is the base prompt sent to the LLM, containing all classification logic, definitions, and rules. If not provided, a simple fallback prompt is used.

3. **`description`** (String, optional): Additional context or description that gets added to the system prompt under "Additional Context" section.

4. **`examples`** (JSON, optional): Few-shot learning examples. **ALL examples are included** (no limit). Can be:
   - A list: `[{"text": "...", "labels": ["useful"]}, {"text": "...", "labels": ["not useful", "personal"]}, ...]`
   - A dict: `{"example1": {"text": "...", "labels": ["gen-quote"]}, ...}`
   - Supports both `"text"` and `"post"` fields for post content
   - Supports both `"label"` (single string) and `"labels"` (array of strings) for classification

---

## How the API Uses PostClassifier Data

### 1. Fetching Classifier Data

When `/api/classifier/run` is called, the API:
1. Queries the `PostClassifier` table using the provided `classifierId`
2. Extracts and parses all fields:
   - `name`: Used for logging and response
   - `prompt`: Used as the **system prompt** (base classification instructions)
   - `description`: Added to system prompt as "Additional Context"
   - `labels`: Parsed and validated (supports list, dict, or string format)
   - `examples`: Parsed and formatted for few-shot learning (ALL examples are used)

### 2. Label Parsing

The API handles multiple formats for the `labels` field:
- **List format** (preferred): `["USEFUL", "GEN-QUOTE", "PERSONAL"]`
- **String format**: Single label string (converted to list)
- **Dict format**: Keys or values extracted as labels
- **JSON string**: Parsed from JSON string representation

**Code location**: `main.py` lines 2371-2410

### 3. Examples Processing

Examples are processed and formatted for inclusion in the system prompt:
- **ALL examples are used** (no limit on quantity)
- **List format**: All examples are processed
  ```json
  [
    {"text": "I just shipped a new feature...", "labels": ["useful"], "score": 0.9},
    {"text": "Happy birthday!", "labels": ["not useful", "personal"], "score": 0.95},
    {"text": "Monday motivation! 💪", "labels": ["not useful", "gen-quote"]}
  ]
  ```
- **Dict format**: All key-value pairs are processed
  ```json
  {
    "example1": {"text": "...", "labels": ["useful"]},
    "example2": {"text": "...", "labels": ["not useful", "gen-quote"]}
  }
  ```
- **Example format support**:
  - Supports both `"text"` and `"post"` fields for post content
  - Supports both `"label"` (single) and `"labels"` (array) for labels
  - Multiple labels per example are displayed as comma-separated: `"Label(s): not useful, personal"`

**Code location**: `main.py` lines 1729-1770

---

## Prompt Construction

The API constructs **two prompts** that are sent to Groq LLM:

### 1. System Prompt (Fully Dynamic)

The system prompt is **completely dynamic** and built from the `PostClassifier` table data:

#### Base Prompt (PostClassifier.prompt)
- The `PostClassifier.prompt` field is used as the **base system prompt**
- This contains all classification rules, definitions, and instructions
- If no prompt is provided, a fallback is used: `"You are a {classifier_name} classifier. Classify posts according to the available labels."`

#### Dynamic Components Added
1. **Available Labels**: Automatically appended from `PostClassifier.labels`
   ```
   Available Labels: not useful, personal, useful, gen-quote, promo, ...
   ```

2. **Additional Context** (if `PostClassifier.description` exists):
   ```
   Additional Context: {classifier_description}
   ```

3. **Few-Shot Examples** (if `PostClassifier.examples` exists):
   ```
   Below are example posts with their correct classifications. Use them as ground-truth demonstrations for how to classify future posts:
   
   Example 1:
   Post: {example_text}
   Label(s): {example_labels}
   
   Example 2:
   Post: {example_text}
   Label(s): {example_labels}
   
   ... (ALL examples included)
   ```

#### Final System Prompt Structure
```
{PostClassifier.prompt}

Available Labels: {labels_list}

Additional Context: {PostClassifier.description}  [if provided]

Below are example posts with their correct classifications...:
{all_formatted_examples}  [if examples exist]
```

**Code location**: `main.py` lines 1772-1802

**Key Features:**
- ✅ No hardcoded classification logic
- ✅ Fully customizable per classifier
- ✅ All examples are included (no limit)
- ✅ Supports any classification schema (not limited to "USEFUL"/"NOT USEFUL")

### 2. User Prompt (Simplified, Dynamic)

The user prompt is now simplified since classification rules are in the system prompt:

#### Part 1: Post Content
```
## Post to Classify

Post Content:
{post_text}
```
**Source**: The actual post being classified

#### Part 2: Required Output Format
```
## Required Output Format

You MUST respond with a valid JSON object with EXACTLY this structure:
{
  "label": "<one of the available labels>",
  "score": <number between 0.0 and 1.0>,
  "scores": {
    <scores for ALL labels>
  }
}

REQUIREMENTS:
1. "label" must be one of these exact labels: {labels_list_str}
2. "score" must be a number between 0.0 and 1.0 representing confidence in the primary label
3. "scores" MUST be an object with ALL {len(classifier_labels)} labels as keys
4. Each score in "scores" must be a number between 0.0 and 1.0
5. The scores MUST sum to exactly 1.0 (probability distribution)
6. The score for the primary "label" should be the highest

Example response format:
{
  "label": "{classifier_labels[0]}",
  "score": 0.85,
  "scores": {
    "USEFUL": 0.85,
    "GEN-QUOTE": 0.05,
    ...
  }
}

Respond ONLY with valid JSON. No markdown, no code blocks, no explanation, just the JSON object.
```
**Source**: Hardcoded format specification (ensures consistent JSON response structure)

**Code location**: `main.py` lines 1804-1857

**Note**: Classification rules are no longer duplicated in the user prompt since they're already in the system prompt. This reduces token usage and avoids confusion.

---

## Labeling Process

### Step-by-Step Flow

1. **Fetch Classifier** → Query `PostClassifier` table by ID
2. **Parse Configuration** → Extract labels, prompt, description, examples
3. **Fetch Posts** → Download posts from S3 for each profile in the audience room
4. **Build Prompts** → Construct system and user prompts for each post
5. **Call Groq LLM** → Send prompts to Groq API
6. **Parse Response** → Extract JSON from Groq response (with fallback parsing)
7. **Validate & Normalize** → Ensure labels match, scores sum to 1.0
8. **Store Results** → Add labels to post objects and upload back to S3

### Response Processing

The API performs extensive response parsing and validation:

#### JSON Extraction (Multiple Attempts)
1. **Direct parse**: Try `json.loads(content)`
2. **Markdown extraction**: Extract from ` ```json ... ``` ` blocks
3. **Code block extraction**: Extract from ` ``` ... ``` ` blocks
4. **Balanced brace extraction**: Find JSON by matching `{` and `}`

#### Validation
- **Label validation**: Checks if returned label exists in `classifier_labels` (case-insensitive matching)
- **Score normalization**: Ensures scores are between 0.0 and 1.0
- **Probability distribution**: Normalizes scores to sum to exactly 1.0

#### Fallback Behavior
If parsing fails or scores are invalid:
- Creates a default distribution with primary label getting 0.8 and others sharing 0.2
- Logs warnings for debugging

**Code location**: `main.py` lines 1942-2170

### Storing Results

After classification, each post gets a `labels` object added:
```json
{
  "text": "Post content...",
  "labels": {
    "classifierId": "uuid-of-classifier",
    "USEFUL": 0.85,
    "GEN-QUOTE": 0.05,
    "PERSONAL": 0.03,
    "PROMO": 0.02,
    ...
  }
}
```

---

## Labeling Analysis

### ✅ What's Working Well

1. **Comprehensive Prompt Structure**
   - Clear definitions of "USEFUL" vs "NOT USEFUL"
   - Specific examples for each label category
   - Well-defined rules for classification

2. **Flexible Configuration**
   - Custom prompts via `PostClassifier.prompt` allow domain-specific rules
   - Few-shot examples improve accuracy
   - Labels are configurable per classifier

3. **Robust Parsing**
   - Multiple fallback strategies for JSON extraction
   - Handles various Groq response formats (markdown, plain JSON, etc.)
   - Case-insensitive label matching

4. **Probability Distribution**
   - Enforces scores summing to 1.0
   - Provides scores for all labels, not just the primary one
   - Normalization logic handles edge cases

5. **Error Handling**
   - Graceful fallbacks on parsing failures
   - Detailed logging for debugging
   - Batch processing continues even if individual posts fail

### ⚠️ Potential Issues & Concerns

1. **Score Distribution Logic**
   - **Issue**: When scores don't sum to 1.0, the normalization logic redistributes remaining probability equally among "missing" labels
   - **Impact**: If a label truly has 0% probability, it might get assigned a small non-zero score
   - **Note**: This is actually reasonable for probability distributions, but worth understanding

2. **Temperature Setting**
   - **Issue**: Fixed temperature of 0.3 is used
   - **Impact**: Lower temperature = more deterministic, which is good for classification but might reduce nuanced understanding
   - **Note**: This is likely intentional, but could be made configurable

3. **Rate Limiting**
   - **Issue**: Groq API has rate limits that can be hit when processing large batches
   - **Impact**: "Too many requests" errors when classifying many posts
   - **Current Mitigation**: 
     - Reduced batch size from 10 to 5 concurrent requests
     - Added 1 second delay between batches
     - Exponential backoff retry logic (2s, 4s, 8s, 16s, 32s delays)
     - Up to 5 retry attempts with automatic rate limit detection
   - **Recommendation**: Consider configurable batch sizes and delays based on API tier

### 🔧 Recent Improvements (Already Implemented)

1. ✅ **Dynamic System Prompt** - System prompt is now fully built from `PostClassifier.prompt`
2. ✅ **All Examples Used** - No longer limited to 3 examples; all examples from database are included
3. ✅ **Flexible Classification Schema** - No hardcoded assumptions; works with any label set
4. ✅ **Rate Limit Handling** - Automatic retry with exponential backoff for 429 errors

### 🔧 Future Recommendations

1. **Label Descriptions in Schema**
   ```prisma
   model PostClassifier {
     // ... existing fields
     labelDescriptions Json?  // Map of label -> description
   }
   ```

2. **Configurable Batch Size**
   ```python
   batch_size = classifier.get("batchSize", 5)  # Allow per-classifier configuration
   ```

---

## API Endpoints

### 1. POST `/api/classifier/run`

Runs a classifier on all posts in an audience room.

**Request:**
```json
{
  "audienceRoomId": "uuid",
  "classifierId": "uuid"
}
```

**Response:**
```json
{
  "classifier_id": "uuid",
  "classifier_name": "Post Usefulness",
  "audience_room_id": "uuid",
  "total_profiles_processed": 5,
  "total_posts_classified": 55,
  "profiles": [...]
}
```

### 2. POST `/api/classifier/test-single`

Tests classification on a single post with full debug output.

**Request:**
```json
{
  "classifier_id": "uuid",
  "post_text": "Post content to classify"
}
```

**Response:**
```json
{
  "classifier_id": "uuid",
  "classifier_name": "...",
  "post_text": "...",
  "labels": [...],
  "system_prompt": "...",
  "user_prompt": "...",
  "groq_response": "...",
  "parsed_result": {...},
  "classification_result": {...}
}
```

---

## Example Flow

### Example 1: Classifying Posts

1. **Classifier Configuration** (in database):
   ```json
   {
     "id": "abc-123",
     "name": "Post Usefulness",
     "prompt": "Focus on technical content. Prioritize posts about engineering practices.",
     "description": "This classifier helps identify posts useful for persona analysis.",
     "labels": ["USEFUL", "GEN-QUOTE", "PERSONAL", "PROMO"],
     "examples": [
       {
         "post": "Just deployed our new microservices architecture...",
         "label": "USEFUL",
         "score": 0.95
       },
       {
         "post": "Monday motivation! 💪",
         "label": "GEN-QUOTE",
         "score": 0.90
       }
     ]
   }
   ```

2. **Post to Classify**:
   ```json
   {
     "text": "I've been working on implementing a new caching strategy using Redis. Here's what I learned..."
   }
   ```

3. **System Prompt Sent to Groq** (built dynamically):
   ```
   Focus on technical content. Prioritize posts about engineering practices.
   
   Available Labels: USEFUL, GEN-QUOTE, PERSONAL, PROMO
   
   Additional Context: This classifier helps identify posts useful for persona analysis.
   
   Below are example posts with their correct classifications. Use them as ground-truth demonstrations for how to classify future posts:
   
   Example 1:
   Post: Just deployed our new microservices architecture...
   Label(s): USEFUL
   
   Example 2:
   Post: Monday motivation! 💪
   Label(s): GEN-QUOTE
   
   ... (all other examples from database)
   ```

4. **User Prompt Sent to Groq** (simplified):
   ```
   ## Post to Classify
   
   Post Content:
   I've been working on implementing a new caching strategy using Redis. Here's what I learned...
   
   ## Required Output Format
   
   You MUST respond with a valid JSON object with EXACTLY this structure:
   {
     "label": "<one of the available labels>",
     "score": <number between 0.0 and 1.0>,
     "scores": {
       <scores for ALL labels>
     }
   }
   ...
   ```

5. **Groq Response**:
   ```json
   {
     "label": "USEFUL",
     "score": 0.92,
     "scores": {
       "USEFUL": 0.92,
       "GEN-QUOTE": 0.03,
       "PERSONAL": 0.03,
       "PROMO": 0.02
     }
   }
   ```

6. **Final Post with Labels**:
   ```json
   {
     "text": "I've been working on implementing a new caching strategy using Redis. Here's what I learned...",
     "labels": {
       "classifierId": "abc-123",
       "USEFUL": 0.92,
       "GEN-QUOTE": 0.03,
       "PERSONAL": 0.03,
       "PROMO": 0.02
     }
   }
   ```

---

## Summary

The Post Classifier API is **fully dynamic and well-implemented** with:
- ✅ **Fully Dynamic Prompts**: System prompt built entirely from `PostClassifier.prompt` - no hardcoded assumptions
- ✅ **All Examples Used**: Every example from the database is included in the prompt (no arbitrary limits)
- ✅ **Flexible Schema**: Works with any classification schema - not limited to specific label sets
- ✅ **Robust Error Handling**: Rate limit detection with exponential backoff retry logic
- ✅ **Comprehensive Parsing**: Multiple fallback strategies for JSON extraction
- ✅ **Probability Distribution**: Enforces scores summing to 1.0 with normalization
- ✅ **Batch Processing**: Processes posts in parallel with rate limit protection (5 concurrent, 1s delays)

### Key Features

1. **Complete Customization**: Each classifier ID can have completely different:
   - Classification rules and instructions (via `prompt`)
   - Label sets (via `labels`)
   - Example sets (via `examples`)
   - Descriptions (via `description`)

2. **Rate Limit Protection**: 
   - Automatic detection of 429 errors
   - Exponential backoff (2s → 4s → 8s → 16s → 32s)
   - Reduced batch sizes and inter-batch delays

3. **Example Format Flexibility**:
   - Supports `"text"` or `"post"` fields
   - Supports single `"label"` or array `"labels"`
   - All examples from database are included

The labeling is **functionally correct and fully flexible** - it adapts to any classification schema defined in the `PostClassifier` table without requiring code changes.


