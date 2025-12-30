"""Classifier service for post classification."""
import json
import os
import logging
import asyncio
from typing import List, Dict, Any, Optional
from fastapi import HTTPException
from app.config import groq_client, logger


def match_classifications_to_posts(classifications: List[Dict], expected_count: int, classifier_labels: List[str]) -> List[Dict]:
    """
    Intelligently match classifications to posts when count doesn't match.
    Uses post_id field if available, otherwise truncates/pads.
    """
    # Try to use post_id for matching if available
    has_post_ids = all(isinstance(c.get("post_id"), int) for c in classifications if isinstance(c, dict))
    
    if has_post_ids:
        # Create a mapping by post_id
        id_to_classification = {}
        for c in classifications:
            if isinstance(c, dict):
                post_id = c.get("post_id")
                if isinstance(post_id, int) and 1 <= post_id <= expected_count:
                    id_to_classification[post_id] = c
        
        # Build result array using post_ids
        matched_results = []
        for i in range(1, expected_count + 1):
            if i in id_to_classification:
                matched_results.append(id_to_classification[i])
            else:
                # Fill with default for missing post_id
                default_label = classifier_labels[0] if classifier_labels else "Unknown"
                default_scores = {label: 0.0 for label in classifier_labels}
                if default_label in default_scores:
                    default_scores[default_label] = 0.5
                matched_results.append({
                    "label": default_label,
                    "score": 0.5,
                    "scores": default_scores
                })
        
        logger.info(f"✅ Matched {len(id_to_classification)} classifications by post_id, filled {expected_count - len(id_to_classification)} with defaults")
        return matched_results
    
    # Fallback: truncate or pad
    if len(classifications) > expected_count:
        logger.info(f"✅ Truncating {len(classifications)} classifications to {expected_count}")
        return classifications[:expected_count]
    else:
        # Pad with defaults
        result = list(classifications)
        default_label = classifier_labels[0] if classifier_labels else "Unknown"
        while len(result) < expected_count:
            default_scores = {label: 0.0 for label in classifier_labels}
            if default_label in default_scores:
                default_scores[default_label] = 0.5
            result.append({
                "label": default_label,
                "score": 0.5,
                "scores": default_scores
            })
        logger.info(f"✅ Padded {len(classifications)} classifications to {expected_count} with defaults")
        return result


async def classify_multiple_posts_single_call(
    posts_texts: List[str],
    classifier_name: str,
    classifier_prompt: str,
    classifier_description: str,
    classifier_labels: List[str],
    classifier_examples: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Classify multiple posts in a SINGLE API call to Groq.
    More efficient than separate calls - sends all posts together.
    
    Args:
        posts_texts: List of post text strings to classify (extracted text only)
        classifier_name: Name of the classifier
        classifier_prompt: System prompt for the classifier
        classifier_description: Rules/description for classification
        classifier_labels: Available labels
        classifier_examples: Few-shot examples
    
    Returns:
        List of classification results (dicts with 'label', 'score', and 'allScores')
    """
    if not groq_client:
        raise HTTPException(status_code=503, detail="Groq client not initialized. Please set GROQ_API_KEY.")
    
    if not posts_texts:
        return []
    
    # Build few-shot examples string
    MAX_EXAMPLES_LENGTH = int(os.getenv("MAX_EXAMPLES_LENGTH", "80000"))
    MAX_EXAMPLE_POST_LENGTH = int(os.getenv("MAX_EXAMPLE_POST_LENGTH", "2000"))
    
    examples_text = ""
    examples_count = 0
    examples_skipped = 0
    examples_truncated = 0
    
    if classifier_examples:
        if isinstance(classifier_examples, list):
            for idx, example in enumerate(classifier_examples):
                if isinstance(example, dict):
                    example_post = example.get("post") or example.get("text", "")
                    example_labels = example.get("labels", [])
                    example_label = example.get("label", "")
                    example_score = example.get("score", "")
                    
                    if len(example_post) > MAX_EXAMPLE_POST_LENGTH:
                        example_post = example_post[:MAX_EXAMPLE_POST_LENGTH] + "... [truncated]"
                        examples_truncated += 1
                    
                    if isinstance(example_labels, list) and len(example_labels) > 0:
                        label_display = ", ".join(example_labels)
                    elif example_label:
                        label_display = example_label
                    else:
                        label_display = ""
                    
                    if example_post and label_display:
                        example_formatted = f"\n\nExample {idx + 1}:\nPost: {example_post}\nLabel(s): {label_display}"
                        if example_score:
                            example_formatted += f" (Score: {example_score})"
                        
                        if len(examples_text) + len(example_formatted) > MAX_EXAMPLES_LENGTH:
                            examples_skipped = len(classifier_examples) - idx
                            logger.warning(f"⚠️ Examples limit reached ({MAX_EXAMPLES_LENGTH} chars). Skipping {examples_skipped} remaining examples.")
                            break
                        
                        examples_text += example_formatted
                        examples_count += 1
        elif isinstance(classifier_examples, dict):
            for idx, (key, value) in enumerate(classifier_examples.items()):
                if isinstance(value, dict):
                    example_post = value.get("post") or value.get("text", "")
                    example_labels = value.get("labels", [])
                    example_label = value.get("label", key)
                    
                    if len(example_post) > MAX_EXAMPLE_POST_LENGTH:
                        example_post = example_post[:MAX_EXAMPLE_POST_LENGTH] + "... [truncated]"
                        examples_truncated += 1
                    
                    if isinstance(example_labels, list) and len(example_labels) > 0:
                        label_display = ", ".join(example_labels)
                    elif example_label:
                        label_display = example_label
                    else:
                        label_display = key
                    
                    if example_post and label_display:
                        example_formatted = f"\n\nExample {idx + 1}:\nPost: {example_post}\nLabel(s): {label_display}"
                        
                        if len(examples_text) + len(example_formatted) > MAX_EXAMPLES_LENGTH:
                            remaining = len(classifier_examples) - idx
                            examples_skipped = remaining
                            logger.warning(f"⚠️ Examples limit reached ({MAX_EXAMPLES_LENGTH} chars). Skipping {remaining} remaining examples.")
                            break
                        
                        examples_text += example_formatted
                        examples_count += 1
    
    # Build system prompt
    system_prompt_parts = []
    
    if classifier_prompt:
        system_prompt_parts.append(classifier_prompt)
    else:
        system_prompt_parts.append(f"You are a {classifier_name} classifier. Classify posts according to the available labels.")
    
    if classifier_labels:
        labels_str = ", ".join(classifier_labels)
        system_prompt_parts.append(f"\n\nAvailable Labels: {labels_str}")
    
    if classifier_description:
        system_prompt_parts.append(f"\n\nAdditional Context: {classifier_description}")
    
    if examples_text:
        system_prompt_parts.append(f"\n\nBelow are example posts with their correct classifications. Use them as ground-truth demonstrations for how to classify future posts:{examples_text}")
    
    system_prompt = "\n".join(system_prompt_parts)
    
    # Build user prompt with ALL posts
    labels_list_str = ", ".join([f'"{label}"' for label in classifier_labels])
    num_posts = len(posts_texts)
    
    # Format all posts for classification with UNIQUE delimiters
    posts_section = ""
    for idx, post_text in enumerate(posts_texts, 1):
        posts_section += f"\n\n<<<POST_ID_{idx}_START>>>\n{post_text}\n<<<POST_ID_{idx}_END>>>"
    
    user_prompt = f"""
        Classify exactly {num_posts} posts. Each <<<POST_ID_X_START>>>...<<<POST_ID_X_END>>> block = 1 post.

        {posts_section}

        Return JSON with exactly {num_posts} classifications in this structure:

        {{
        "classifications": [
            {{"post_id": 1, "label": "winning_label", "score": 0.85}},
            {{"post_id": 2, "label": "winning_label", "score": 0.92}},
            ...
            {{"post_id": {num_posts}, "label": "winning_label", "score": 0.95}}
        ]
        }}

        Rules:
        1.⁠ ⁠Each post must have *exactly one label*:
        - ⁠"useful"⁠ if the post is USEFUL
        - One of the NOT USEFUL reason labels if the post is NOT USEFUL
        2.⁠ ⁠⁠"score"⁠ must be a number between 0.0 and 1.0 representing your confidence in the chosen label
        3.⁠ ⁠Array length must equal {num_posts}, and the order must match post_id 1 through {num_posts}
        4.⁠ ⁠Respond *only with valid JSON*, no explanations, no markdown, no extra text
    """
            
    # Get model name
    model_name = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    
    # Retry logic with exponential backoff for rate limits AND count mismatches
    max_retries = 5
    max_count_mismatch_retries = 2  # Additional retries specifically for count mismatch
    base_delay = 2
    
    classifications = None
    last_count_received = 0
    
    for attempt in range(max_retries):
        count_mismatch_retry = 0
        
        while count_mismatch_retry <= max_count_mismatch_retries:
            try:
                try:
                    # Adjust temperature slightly on count mismatch retries to get different results
                    temp = 0.1 if count_mismatch_retry == 0 else 0.1 + (count_mismatch_retry * 0.1)
                    
                    response = groq_client.chat.completions.create(
                        model=model_name,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        temperature=temp,
                        response_format={"type": "json_object"}
                    )
                    
                    # Parse response immediately to check count
                    content = response.choices[0].message.content
                    
                    # Try to extract JSON
                    result = None
                    try:
                        result = json.loads(content)
                    except json.JSONDecodeError:
                        # Try extracting from markdown code blocks
                        if "```json" in content:
                            json_start = content.find("```json") + 7
                            json_end = content.find("```", json_start)
                            if json_end != -1:
                                result = json.loads(content[json_start:json_end].strip())
                        elif "```" in content:
                            json_start = content.find("```") + 3
                            json_end = content.find("```", json_start)
                            if json_end != -1:
                                result = json.loads(content[json_start:json_end].strip())
                        else:
                            # Try balanced braces
                            start_idx = content.find('{')
                            if start_idx != -1:
                                brace_count = 0
                                end_idx = start_idx
                                for i in range(start_idx, len(content)):
                                    if content[i] == '{':
                                        brace_count += 1
                                    elif content[i] == '}':
                                        brace_count -= 1
                                        if brace_count == 0:
                                            end_idx = i + 1
                                            break
                                if brace_count == 0:
                                    result = json.loads(content[start_idx:end_idx])
                    
                    if not result:
                        raise json.JSONDecodeError("Could not parse JSON from response", content, 0)
                    
                    # Extract classifications array
                    classifications = result.get("classifications", [])
                    
                    if not isinstance(classifications, list):
                        raise ValueError(f"Expected classifications array, got: {type(classifications)}")
                    
                    last_count_received = len(classifications)
                    
                    # Check count - if mismatch, retry with count mismatch logic
                    if len(classifications) != num_posts:
                        if count_mismatch_retry < max_count_mismatch_retries:
                            logger.warning(f"⚠️ Count mismatch: expected {num_posts}, got {len(classifications)}. Retrying ({count_mismatch_retry + 1}/{max_count_mismatch_retries})...")
                            count_mismatch_retry += 1
                            await asyncio.sleep(0.5)  # Brief pause before retry
                            continue
                        else:
                            # Exhausted count mismatch retries - use intelligent matching
                            logger.warning(f"⚠️ Count mismatch persists after {max_count_mismatch_retries} retries. Expected {num_posts}, got {len(classifications)}. Using intelligent matching...")
                            classifications = match_classifications_to_posts(classifications, num_posts, classifier_labels)
                    
                    # Success - we have the right count or have handled the mismatch
                    break
                    
                except json.JSONDecodeError as json_err:
                    logger.error(f"JSON parse error: {json_err}")
                    if count_mismatch_retry < max_count_mismatch_retries:
                        count_mismatch_retry += 1
                        await asyncio.sleep(0.5)
                        continue
                    raise
                    
                except Exception as groq_error:
                    error_str = str(groq_error).lower()
                    error_repr = repr(groq_error).lower()
                    full_error_str = str(groq_error)
                    
                    # Check for token quota limit (daily limit) - cannot retry
                    is_token_quota_limit = (
                        "tokens per day" in error_str or "tpd" in error_str or
                        "token quota" in error_str or "type': 'tokens'" in error_str or
                        "'code': 'rate_limit_exceeded'" in error_str
                    )
                    
                    if is_token_quota_limit:
                        logger.error(f"❌ Daily token quota limit exceeded. Cannot retry - quota resets daily.")
                        raise HTTPException(
                            status_code=429,
                            detail={
                                "error": "token_quota_exceeded",
                                "message": "Daily token quota limit reached for Groq API. This is a quota limit, not a rate limit.",
                                "type": "token_quota",
                                "suggestion": "Please wait for the daily quota to reset, or upgrade your Groq plan to increase token limits.",
                                "groq_error": full_error_str
                            }
                        )
                    
                    # Check for request rate limit - can retry
                    is_request_rate_limit = (
                        ("429" in error_str or "429" in error_repr or
                        hasattr(groq_error, 'status_code') and groq_error.status_code == 429) and
                        not is_token_quota_limit
                    )
                    
                    if is_request_rate_limit:
                        raise  # Let outer loop handle rate limit retry
                    
                    # For other errors, raise to outer loop
                    logger.error(f"Groq API call failed: {groq_error}")
                    raise
            
            except HTTPException:
                raise
            except Exception as inner_error:
                if count_mismatch_retry < max_count_mismatch_retries:
                    count_mismatch_retry += 1
                    await asyncio.sleep(0.5)
                    continue
                raise
        
        # If we got here with valid classifications, break outer loop
        if classifications is not None and len(classifications) > 0:
            break
            
        # Handle outer retry logic for rate limits
        if attempt < max_retries - 1:
            delay = base_delay * (2 ** attempt)
            logger.warning(f"⚠️ Retry attempt {attempt + 1}/{max_retries}. Waiting {delay} seconds...")
            await asyncio.sleep(delay)
            continue
        else:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to classify posts after {max_retries} attempts"
            )
    
    # Final validation - ensure we have classifications
    if classifications is None:
        raise HTTPException(status_code=500, detail="Failed to get classifications from API")
    
    # Normalize each classification
    normalized_results = []
    for idx, classification in enumerate(classifications):
        label = classification.get("label", "")
        score = classification.get("score", 0.0)
        all_scores = classification.get("scores", {})
        
        # Validate and normalize label
        if label not in classifier_labels:
            label_lower = label.lower()
            matched_label = None
            for available_label in classifier_labels:
                if available_label.lower() == label_lower:
                    matched_label = available_label
                    break
            label = matched_label if matched_label else (classifier_labels[0] if classifier_labels else "Unknown")
        
        # Normalize score
        try:
            score = float(score)
            if score > 1.0:
                score = score / 100.0
            score = max(0.0, min(1.0, score))
        except (ValueError, TypeError):
            score = 0.5
        
        # Normalize all scores
        normalized_scores = {}
        if isinstance(all_scores, dict) and len(all_scores) > 0:
            total_score = 0.0
            for available_label in classifier_labels:
                if available_label in all_scores:
                    try:
                        label_score = float(all_scores[available_label])
                        if label_score > 1.0:
                            label_score = label_score / 100.0
                        normalized_scores[available_label] = round(max(0.0, min(1.0, label_score)), 2)
                        total_score += normalized_scores[available_label]
                    except (ValueError, TypeError):
                        normalized_scores[available_label] = 0.0
                else:
                    normalized_scores[available_label] = 0.0
            
            if total_score > 1.0:
                for available_label in classifier_labels:
                    normalized_scores[available_label] = round(normalized_scores[available_label] / total_score, 2)
            elif total_score < 1.0 and total_score > 0:
                remaining = 1.0 - total_score
                missing_labels = [l for l in classifier_labels if normalized_scores[l] == 0.0]
                if missing_labels:
                    per_missing = remaining / len(missing_labels)
                    for missing_label in missing_labels:
                        normalized_scores[missing_label] = round(per_missing, 2)
                else:
                    for available_label in classifier_labels:
                        normalized_scores[available_label] = round(normalized_scores[available_label] / total_score, 2)
            elif total_score == 0:
                remaining = max(0.0, 1.0 - score)
                remaining_per_label = remaining / max(1, len(classifier_labels) - 1) if len(classifier_labels) > 1 else 0.0
                for available_label in classifier_labels:
                    if available_label == label:
                        normalized_scores[available_label] = round(score, 2)
                    else:
                        normalized_scores[available_label] = round(remaining_per_label, 2)
        else:
            remaining = max(0.0, 1.0 - score)
            remaining_per_label = remaining / max(1, len(classifier_labels) - 1) if len(classifier_labels) > 1 else 0.0
            for available_label in classifier_labels:
                if available_label == label:
                    normalized_scores[available_label] = round(score, 2)
                else:
                    normalized_scores[available_label] = round(remaining_per_label, 2)
        
        normalized_results.append({
            "label": label,
            "score": round(score, 2),
            "allScores": normalized_scores
        })
    
    # Ensure we have the same number of results as posts
    while len(normalized_results) < num_posts:
        default_label = classifier_labels[0] if classifier_labels else "Unknown"
        default_scores = {label: 0.0 for label in classifier_labels}
        if default_label in default_scores:
            default_scores[default_label] = 0.5
        normalized_results.append({
            "label": default_label,
            "score": 0.5,
            "allScores": default_scores
        })
    
    return normalized_results[:num_posts]  # Return only what we need


async def classify_posts_batch(
    posts: List[Dict[str, Any]],
    classifier_name: str,
    classifier_prompt: str,
    classifier_description: str,
    classifier_labels: List[str],
    classifier_examples: Optional[Dict[str, Any]] = None,
    batch_size: int = 20,  # Process 20 posts together in one API call
) -> List[Dict[str, Any]]:
    """
    Classify multiple posts in batches using Groq.
    Each batch sends multiple posts in a SINGLE API call (much more efficient).
    
    Args:
        posts: List of post objects to classify
        classifier_name: Name of the classifier
        classifier_prompt: System prompt for the classifier
        classifier_description: Rules/description for classification
        classifier_labels: Available labels
        classifier_examples: Few-shot examples
        batch_size: Number of posts to send together in one API call (default: 20)
    
    Returns:
        List of classification results (dicts with 'label', 'score', and 'allScores')
    """
    results = []
    
    # Extract text from all posts (only send text field, not full objects)
    posts_texts = []
    for post in posts:
        post_text = post.get("text", "")
        if not post_text:
            # Try alternative fields
            post_text = post.get("content", "") or post.get("description", "") or ""
        posts_texts.append(post_text)
    
    # Process in batches - each batch sends multiple posts in ONE API call
    for i in range(0, len(posts_texts), batch_size):
        batch_texts = posts_texts[i:i + batch_size]
        batch_num = (i // batch_size) + 1
        total_batches = (len(posts_texts) + batch_size - 1) // batch_size
        
        logger.info(f"Processing batch {batch_num}/{total_batches} ({len(batch_texts)} posts in single API call)")
        
        try:
            # Classify all posts in this batch with ONE API call
            batch_results = await classify_multiple_posts_single_call(
                posts_texts=batch_texts,
                classifier_name=classifier_name,
                classifier_prompt=classifier_prompt,
                classifier_description=classifier_description,
                classifier_labels=classifier_labels,
                classifier_examples=classifier_examples,
            )
            
            # Add results
            results.extend(batch_results)
            
        except Exception as e:
            logger.error(f"Error classifying batch {batch_num}: {e}")
            # Use default classification for all posts in this batch
            default_label = classifier_labels[0] if classifier_labels else "Unknown"
            for _ in batch_texts:
                default_scores = {label: 0.0 for label in classifier_labels}
                if default_label in default_scores:
                    default_scores[default_label] = 0.5
                results.append({
                    "label": default_label,
                    "score": 0.5,
                    "allScores": default_scores
                })
        
        # Add delay between batches to avoid rate limits (except after the last batch)
        if i + batch_size < len(posts_texts):
            delay_between_batches = 1.0  # 1 second delay between batches
            logger.info(f"Waiting {delay_between_batches} seconds before next batch to avoid rate limits...")
            await asyncio.sleep(delay_between_batches)
    
    return results

