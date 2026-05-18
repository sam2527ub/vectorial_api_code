#!/usr/bin/env python3
"""
Final Predictions with Memory
=============================

Generates final predictions for training data using static memory from SGO training.
- Uses the same pipeline as pre_sgo_predictions
- Loads memory from _memory folder for each tribe
- Adds memory context to prompts (similar to part_a prompt)
- Supports modes: final_train (with memory) and final_train_chars (with memory + refined characteristics)

Usage:
    python 07_sgo_training/scripts/final_predictions.py
    python 07_sgo_training/scripts/final_predictions.py --only-cluster cluster_0
    python 07_sgo_training/scripts/final_predictions.py --only-cluster cluster_4 --only-micro micro_15
    python 07_sgo_training/scripts/final_predictions.py --start-from-cluster cluster_1
"""

import sys
import logging
import os
import json
from pathlib import Path
import argparse
from threading import Lock
from openai import OpenAI

# Add project root FIRST (before any imports)
project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Load .env so OPENAI_API_KEY and OPENAI_BASE_URL (Bedrock) are set
try:
    from dotenv import load_dotenv
    load_dotenv(project_root / ".env", override=True)
except ImportError:
    pass

# Import from project root utils FIRST (before _package_setup which might modify paths)
from utils.openai_client import create_openai_client
from utils.wandb_utils import init_wandb_run, load_config, get_stage_config, get_openai_config, finish_run

# Set up package structure for sgo_training imports
scripts_dir = Path(__file__).parent
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))
import _package_setup

# Add prediction_lib path (from 07_pre_sgo_predictions/scripts)
prediction_lib_path = str(project_root / "07_pre_sgo_predictions" / "scripts")
if prediction_lib_path not in sys.path:
    sys.path.insert(0, prediction_lib_path)
from prediction_lib.data_loader import DataLoader
from prediction_lib.generate_model_predictions import PipelineOrchestrator
from prediction_lib.llm_client import RateLimiter
from prediction_lib.prompt_generation import load_prompt, create_enhanced_prompt
from prediction_lib.single_review_prediction import process_single_review

# Import sgo_training utilities for memory loading
from sgo_training.utils import io_utils

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def load_memory_file(memory_dir: str, cluster_id: str, micro_id: str) -> dict:
    """Load memory file (batch_analyses) for a specific cluster and micro cluster."""
    memory_file = os.path.join(memory_dir, cluster_id, f"{micro_id}_batch_analyses.json")
    if os.path.exists(memory_file):
        return io_utils.load_json_file(memory_file, {})
    return {}


def format_memory_for_prompt(memory_data: dict, persona_name: str, user_id: str) -> str:
    """
    Format memory data (batch_analyses) into a string for prompt inclusion.
    Only extracts analyses for the specific user_id.
    
    Args:
        memory_data: Dictionary with batch data (e.g., "iteration_1_batch_1": {"reviews": [...], "analyses": [...]})
        persona_name: Name of the persona (not used but kept for compatibility)
        user_id: User ID to filter analyses for (e.g., "AE32INSVRFOUVTTXAAY3E6VXFUQA")
    
    Returns:
        Formatted string with only this user's analyses, or empty string if none found
    """
    user_analyses = []
    
    # Extract analyses only for this specific user
    for batch_key, batch_data in memory_data.items():
        if not isinstance(batch_data, dict):
            continue
        
        reviews = batch_data.get('reviews', [])
        analyses = batch_data.get('analyses', [])
        
        if not isinstance(reviews, list) or not isinstance(analyses, list):
            continue
        
        # Find all review indices for this user
        # Review keys are in format: "{user_id}_review_{index}"
        for idx, review_key in enumerate(reviews):
            if isinstance(review_key, str) and review_key.startswith(f"{user_id}_review_"):
                # Found a review for this user - get corresponding analysis
                if idx < len(analyses):
                    analysis = analyses[idx]
                    if analysis:  # Only add non-empty analyses
                        user_analyses.append(str(analysis))
    
    if user_analyses:
        # Format as past learnings
        past_learnings = ' | '.join(user_analyses)
        memory_text = f"""
**Past Learnings (Memory from SGO Training):**
{past_learnings}
"""
        # Print memory for debugging
        logging.info(f"  📝 Memory for user {user_id}: {len(user_analyses)} analyses")
        logging.debug(f"  Memory content:\n{memory_text}")
        return memory_text
    logging.debug(f"  📝 No memory found for user {user_id}")
    return ""


def create_final_prompt_with_memory(
    persona_context: dict,
    user_char_summary: str,
    category_char_summary: str,
    product_description: str,
    category: str,
    category_themes: list,
    prompt_template: str,
    memory_text: str = "",
    scoring_mode: str = "confidence",
    mode: str = "final_train"
) -> str:
    """
    Create enhanced prompt with memory included.
    Similar to create_enhanced_prompt but adds memory section.
    
    Args:
        mode: "final_train" (no refined chars) or "final_train_chars" (with refined chars)
    """
    persona_name = persona_context.get('persona_name', 'Unknown Persona')
    qual_summary = persona_context.get('qualitative_summary', {})
    
    persona_summary = qual_summary.get('persona_summary', 'N/A')
    key_motivations = qual_summary.get('key_motivations', [])
    common_praises = qual_summary.get('common_praises', [])
    common_criticisms = qual_summary.get('common_criticisms', [])
    core_characteristics = qual_summary.get('core_characteristics', [])
    potential_goals = qual_summary.get('potential_goals', [])
    
    def format_list(items):
        if not items or not isinstance(items, list):
            return "N/A"
        return "\n- ".join([""] + items)
    
    motivations_text = format_list(key_motivations)
    praises_text = format_list(common_praises)
    criticisms_text = format_list(common_criticisms)
    characteristics_text = format_list(core_characteristics)
    goals_text = format_list(potential_goals)
    
    category_section = ""
    if category_char_summary and category_char_summary.strip():
        category_section = f"""
### 3. Your Category-Specific Behavior ({category})
**How You Specifically Behave When Reviewing {category} Products:**
{category_char_summary}
"""
    
    # Format memory section
    memory_section = memory_text if memory_text else "No past learnings available."
    
    # For final_train mode, remove the refined characteristics section from template
    # For final_train_chars mode, we'll provide refined_chars_section
    if mode == "final_train":
        # Remove the refined characteristics section from the template
        import re
        # Remove the entire "### Refined Characteristics" section including the content placeholder
        # Pattern matches from "### Refined Characteristics" up to (but not including) "## THE PRODUCT"
        refined_section_pattern = r'### Refined Characteristics.*?\n\{refined_chars_section\}\s*\n'
        prompt_template = re.sub(refined_section_pattern, '', prompt_template, flags=re.DOTALL)
        refined_chars_section = ""  # Not used, but needed for format() if template still has placeholder
    else:
        # final_train_chars mode - refined_chars_section will be provided separately
        refined_chars_section = "No refined characteristics available."
    
    # Format the prompt template
    if scoring_mode in ["logprobs", "logprobs-without-persona-context"]:
        prompt = prompt_template.format(
            persona_name=persona_name,
            persona_summary=persona_summary,
            key_motivations=motivations_text,
            common_praises=praises_text,
            common_criticisms=criticisms_text,
            core_characteristics=characteristics_text,
            potential_goals=goals_text,
            user_char_summary=user_char_summary or "N/A",
            category_section=category_section,
            category=category,
            product_description=product_description,
            memory_section=memory_section,
            refined_chars_section=refined_chars_section
        )
    else:
        # Confidence mode: include theme scoring instructions
        themes_list = "\n".join([f"{i+1}. {theme}" for i, theme in enumerate(category_themes)])
        
        # Generate themes JSON template
        themes_json_template = "\n".join([
            f'    "{theme}": <float 0.0-1.0>,' if i < len(category_themes) - 1 
            else f'    "{theme}": <float 0.0-1.0>'
            for i, theme in enumerate(category_themes)
        ])
        
        prompt = prompt_template.format(
            persona_name=persona_name,
            persona_summary=persona_summary,
            key_motivations=motivations_text,
            common_praises=praises_text,
            common_criticisms=criticisms_text,
            core_characteristics=characteristics_text,
            potential_goals=goals_text,
            user_char_summary=user_char_summary or "N/A",
            category_section=category_section,
            category=category,
            product_description=product_description,
            memory_section=memory_section,
            refined_chars_section=refined_chars_section,
            themes_list=themes_list,
            themes_json_template=themes_json_template
        )
    
    return prompt


def process_single_review_with_memory(args_tuple):
    """
    Process a single review prediction with memory.
    Modified version of process_single_review that uses memory in prompt.
    Also calculates deltas (text_delta, theme_delta/JSD, overall_delta).
    Thread-safe with lock for embeddings_cache access.
    """
    (actual_review, persona_context, user_char_summary, category_char_summary,
     main_category, category_themes, memory_text, client, rate_limiter, 
     prompt_template, model_name, max_tokens, temperature, max_retries, 
     scoring_mode, theme_prediction_model, review_key, embeddings_cache, embeddings_cache_lock, mode) = args_tuple
    
    # Create prompt with memory (mode determines if refined_chars_section is included)
    prompt = create_final_prompt_with_memory(
        persona_context=persona_context,
        user_char_summary=user_char_summary,
        category_char_summary=category_char_summary,
        product_description=actual_review.get('product_description', ''),
        category=main_category,
        category_themes=category_themes,
        prompt_template=prompt_template,
        memory_text=memory_text,
        scoring_mode=scoring_mode,
        mode=mode
    )
    
    # Use the same LLM prediction logic from pre_sgo_predictions
    from prediction_lib.llm_client import get_llm_prediction
    
    product_description = actual_review.get('product_description', '')
    # NOTE: The prompt includes memory_text (past learnings from SGO training)
    # - In "logprobs" mode: Memory is included in theme prediction (full context)
    # - In "logprobs-without-persona-context" mode: Memory is NOT included in theme prediction (only product_description + review_text)
    # - In "confidence" mode: Memory is included in the single prediction call
    prediction = get_llm_prediction(
        prompt, client, rate_limiter, category_themes, model_name, 
        max_tokens, temperature, max_retries, scoring_mode, 
        theme_prediction_model, product_description
    )
    
    # Check if LLM call failed
    if prediction is None:
        return {
            'status': 'failed',
            'error': 'LLM call failed after all retries',
            'product_description': actual_review.get('product_description', ''),
            'category': actual_review.get('category'),
            'asin': actual_review.get('asin'),
            'timestamp': actual_review.get('timestamp'),
            'actual': {
                'review_text': actual_review.get('review_text', ''),
                'rating': actual_review.get('rating'),
                'sentiment': actual_review.get('sentiment'),
                'topic_probabilities': actual_review.get('topic_probabilities', {})  # Use topic_probabilities as actual (ground truth), NOT predicted_themes
            }
        }
    
    # Calculate metrics
    from prediction_lib.metrics import calculate_review_metrics
    
    metrics = calculate_review_metrics(prediction, {
        'rating': actual_review.get('rating'),
        'sentiment': actual_review.get('sentiment'),
        'predicted_themes': actual_review.get('themes', actual_review.get('predicted_themes', []))
    })
    
    # Calculate deltas (text_delta, theme_delta/JSD, overall_delta)
    deltas = {}
    try:
        # Import delta calculation functions
        from calculate_behaviour_loss.weighted_topic_review_metric_calculation import (
            calculate_text_delta,
            calculate_theme_delta,
            calculate_theme_delta_logprobs
        )
        from sgo_training.utils import embeddings
        
        # Get scoring mode from params (passed through task tuple)
        scoring_mode = scoring_mode  # From args_tuple
        
        # Calculate text_delta (cosine similarity between embeddings)
        pred_text = prediction.get('review_text', '')
        act_text = actual_review.get('review_text', '')
        
        if pred_text and act_text:
            try:
                # Use get_or_compute_embedding like SGO training pipeline (with lock for thread-safety)
                pred_emb = embeddings.get_or_compute_embedding(
                    pred_text,
                    review_key,
                    'pred_final',
                    client,
                    embeddings_cache,
                    lock=embeddings_cache_lock  # Thread-safe cache access
                )
                act_emb = embeddings.get_or_compute_embedding(
                    act_text,
                    review_key,
                    'actual',
                    client,
                    embeddings_cache,
                    lock=embeddings_cache_lock  # Thread-safe cache access
                )
                if pred_emb is not None and act_emb is not None:
                    text_delta = calculate_text_delta(pred_emb, act_emb)
                    deltas['text_delta'] = text_delta
            except Exception as e:
                logging.warning(f"Failed to calculate text_delta: {e}")
        
        # Calculate theme_delta (JSD for logprobs mode, 1-recall for confidence mode)
        # IMPORTANT: Use ONLY topic_probabilities for JSD calculation (ground truth)
        # Do NOT use predicted_themes for JSD - it's only for recall calculation
        pred_themes = prediction.get('predicted_themes', {})
        
        # For actual themes: MUST use topic_probabilities (ground truth distribution) for JSD
        # Do NOT fallback to predicted_themes - that's only for recall, not JSD
        act_themes = actual_review.get('topic_probabilities', {})
        if not act_themes or (isinstance(act_themes, dict) and len(act_themes) == 0):
            # If topic_probabilities is missing, cannot calculate JSD properly
            asin = actual_review.get('asin', 'unknown')
            logging.warning(f"Review {asin} missing topic_probabilities - skipping theme delta calculation (ground truth required for JSD)")
            act_themes = {}  # Will skip delta calculation below
        
        # Handle list format for actual themes (convert to uniform distribution)
        if isinstance(act_themes, list):
            if not act_themes:
                act_themes = {}
            else:
                act_themes = {theme: 1.0 / len(act_themes) for theme in act_themes}
        
        # Handle list format for predicted themes (convert to uniform distribution)
        if isinstance(pred_themes, list):
            if not pred_themes:
                pred_themes = {}
            else:
                pred_themes = {theme: 1.0 / len(pred_themes) for theme in pred_themes}
        
        if pred_themes and act_themes:
            try:
                # Get all themes for JSD calculation (if logprobs mode)
                all_themes = None
                if scoring_mode in ["logprobs", "logprobs-without-persona-context"]:
                    # For JSD, we need all possible themes - use category_themes from task
                    all_themes = category_themes  # From args_tuple
                    theme_delta = calculate_theme_delta_logprobs(pred_themes, act_themes, all_themes)
                    deltas['theme_delta'] = theme_delta
                    deltas['theme_jsd'] = theme_delta  # Explicit JSD field
                else:
                    # Confidence mode: use 1-recall
                    theme_delta = calculate_theme_delta(pred_themes, act_themes)
                    deltas['theme_delta'] = theme_delta
            except Exception as e:
                logging.warning(f"Failed to calculate theme_delta: {e}")
        
        # Calculate overall_delta (weighted combination)
        if 'text_delta' in deltas and 'theme_delta' in deltas:
            # Use same weights as SGO training (from config or default)
            weight_text = 0.7  # Default, can be overridden from config
            weight_theme = 0.3  # Default, can be overridden from config
            overall_delta = (deltas['text_delta'] * weight_text) + (deltas['theme_delta'] * weight_theme)
            deltas['overall_delta'] = overall_delta
            deltas['delta_weights'] = {'text': weight_text, 'theme': weight_theme}
        elif 'text_delta' in deltas or 'theme_delta' in deltas:
            # Log partial delta calculation
            logging.warning(f"Partial delta calculation: text_delta={'text_delta' in deltas}, theme_delta={'theme_delta' in deltas}")
    except Exception as e:
        logging.warning(f"Failed to calculate deltas: {e}", exc_info=True)
    
    # Get topic_probabilities from input review (ground truth from training data)
    # This should be at the top level of the review dict from training data artifact
    # The review dict structure from cleaned_merged_reviews_by_user.json has topic_probabilities at top level
    topic_probabilities = actual_review.get('topic_probabilities', {})
    
    # Validate and extract topic_probabilities
    if not topic_probabilities:
        # Try alternative locations (shouldn't be needed, but just in case)
        topic_probabilities = actual_review.get('actual', {}).get('topic_probabilities', {})
    
    if not topic_probabilities or (isinstance(topic_probabilities, dict) and len(topic_probabilities) == 0):
        # If not found, log a warning - this is critical for delta calculation
        asin = actual_review.get('asin', 'unknown')
        logging.warning(f"Review {asin} missing topic_probabilities - theme delta cannot be calculated properly")
        # Set to empty dict to avoid KeyError, but delta calculation will fail for themes
        topic_probabilities = {}
    else:
        # Validate it's a dict with probabilities
        if not isinstance(topic_probabilities, dict):
            logging.warning(f"Review {actual_review.get('asin', 'unknown')} has topic_probabilities but it's not a dict: {type(topic_probabilities)}")
            topic_probabilities = {}
        else:
            # Log success for debugging (only first few to avoid spam)
            asin = actual_review.get('asin', 'unknown')
            if hasattr(process_single_review_with_memory, '_debug_count'):
                process_single_review_with_memory._debug_count = getattr(process_single_review_with_memory, '_debug_count', 0) + 1
            else:
                process_single_review_with_memory._debug_count = 1
            
            if process_single_review_with_memory._debug_count <= 3:
                logging.debug(f"Review {asin}: Found {len(topic_probabilities)} topic_probabilities for delta calculation")
    
    # Get predicted_themes from input if it exists (for recall calculation, not JSD)
    input_predicted_themes = actual_review.get('predicted_themes', [])
    if not input_predicted_themes:
        # Try themes as fallback
        input_predicted_themes = actual_review.get('themes', [])
    
    result = {
        'status': 'success',
        'product_description': actual_review.get('product_description', ''),
        'category': actual_review.get('category'),
        'asin': actual_review.get('asin'),
        'timestamp': actual_review.get('timestamp'),
        'prediction': prediction,
        'actual': {
            'review_text': actual_review.get('review_text', ''),
            'rating': actual_review.get('rating'),
            'sentiment': actual_review.get('sentiment'),
            'topic_probabilities': topic_probabilities,  # Use topic_probabilities for JSD calculation (ground truth)
            'predicted_themes': input_predicted_themes if input_predicted_themes else []  # Keep predicted_themes for recall calculation (not used for JSD)
        },
        # Store original topic_probabilities at top level for easy access during cleanup
        'topic_probabilities': topic_probabilities
    }
    
    if metrics:
        result['metrics'] = metrics
    else:
        # Default metrics
        result['metrics'] = {
            'rating_score': 0.0,
            'sentiment_score': 0.0,
            'recall@1': 0.0,
            'recall@3': 0.0,
            'recall@5': 0.0,
            'recall@k': 0.0,
            'recall@max(3,k)': 0.0,
            'recall@max(3,k)_threshold_0.8': 0.0,
            'num_themes_above_0.8': 0,
            'num_additional_themes_0.8': 0,
            'recall@max(3,k)_threshold_0.85': 0.0,
            'num_themes_above_0.85': 0,
            'num_additional_themes_0.85': 0,
            'overall_accuracy': 0.0,
            'weights_used': {'rating': 0.4, 'sentiment': 0.3, 'theme_recall': 0.3}
        }
    
    # Add deltas to result
    if deltas:
        result['deltas'] = deltas
    
    return result


class FinalPredictionsOrchestrator(PipelineOrchestrator):
    """
    Extended orchestrator that adds memory loading and uses memory in prompts.
    """
    def __init__(self, run, client, rate_limiter, prompt_template, config_params, 
                 category_mapping=None, memory_base_dir=None, mode="final_train"):
        super().__init__(run, client, rate_limiter, prompt_template, config_params, category_mapping)
        self.memory_base_dir = memory_base_dir
        self.mode = mode  # "final_train" or "final_train_chars"
    
    def _save_micro_cluster_data(self, base_dir, cluster_id, micro_id, tribe_id, predictions, persona_context, dataset_type):
        """Override to save deltas in summary file instead of recall metrics, and ensure topic_probabilities in actual section."""
        import numpy as np
        from collections import Counter, defaultdict
        
        cluster_dir = base_dir / cluster_id
        cluster_dir.mkdir(parents=True, exist_ok=True)
        
        # Clean up predictions: ensure topic_probabilities in actual, remove predicted_themes from actual
        # Collect deltas for aggregation
        all_deltas = defaultdict(list)  # metric_name -> [values]
        reviews_with_deltas = 0
        reviews_without_deltas = 0
        reviews_with_topic_probs = 0
        reviews_without_topic_probs = 0
        
        for user_id, user_reviews in predictions.items():
            if isinstance(user_reviews, list):
                for review in user_reviews:
                    # Clean up actual section: ensure topic_probabilities is present
                    # Keep predicted_themes if it exists (for recall calculation, not JSD)
                    if 'actual' in review:
                        # Ensure topic_probabilities is in actual section
                        # First check if it's already there and valid
                        has_valid_topic_probs = (
                            'topic_probabilities' in review['actual'] and 
                            isinstance(review['actual']['topic_probabilities'], dict) and 
                            len(review['actual']['topic_probabilities']) > 0
                        )
                        
                        if not has_valid_topic_probs:
                            # Try to get from the review data if available (might be at top level)
                            topic_probs = review.get('topic_probabilities', {})
                            if topic_probs and isinstance(topic_probs, dict) and len(topic_probs) > 0:
                                review['actual']['topic_probabilities'] = topic_probs
                                reviews_with_topic_probs += 1
                                logging.debug(f"Added topic_probabilities to actual section for review {review.get('asin', 'unknown')}")
                            else:
                                # If still not found, this is a problem - log warning
                                reviews_without_topic_probs += 1
                                logging.warning(f"  ⚠️  Review {review.get('asin', 'unknown')} missing topic_probabilities - cannot calculate theme delta properly")
                                # Set empty dict to avoid KeyError later
                                review['actual']['topic_probabilities'] = {}
                        else:
                            # topic_probabilities exists and is valid
                            reviews_with_topic_probs += 1
                        
                        # Keep predicted_themes in actual section if it exists (for recall calculation)
                        # JSD calculation will only use topic_probabilities, not predicted_themes
                    
                    # Collect deltas for aggregation
                    if 'deltas' in review and review['deltas']:
                        deltas = review['deltas']
                        # Collect all delta metrics
                        if 'text_delta' in deltas and deltas['text_delta'] is not None:
                            all_deltas['text_delta'].append(float(deltas['text_delta']))
                        if 'theme_delta' in deltas and deltas['theme_delta'] is not None:
                            all_deltas['theme_delta'].append(float(deltas['theme_delta']))
                        if 'theme_jsd' in deltas and deltas['theme_jsd'] is not None:
                            all_deltas['theme_jsd'].append(float(deltas['theme_jsd']))
                        if 'overall_delta' in deltas and deltas['overall_delta'] is not None:
                            all_deltas['overall_delta'].append(float(deltas['overall_delta']))
                        reviews_with_deltas += 1
                    else:
                        # If deltas are missing, log a warning
                        if review.get('status') == 'success':
                            reviews_without_deltas += 1
                            logging.warning(f"  ⚠️  Missing deltas for review in {user_id}")
        
        if reviews_with_deltas > 0:
            logging.info(f"  ✅ {reviews_with_deltas} reviews have deltas saved")
        if reviews_without_deltas > 0:
            logging.warning(f"  ⚠️  {reviews_without_deltas} reviews missing deltas")
        if reviews_with_topic_probs > 0:
            logging.info(f"  ✅ {reviews_with_topic_probs} reviews have topic_probabilities in actual section")
        if reviews_without_topic_probs > 0:
            logging.warning(f"  ⚠️  {reviews_without_topic_probs} reviews missing topic_probabilities in actual section")
        
        # Calculate aggregate metrics from deltas (not recall metrics)
        aggregate_scores_raw = {}
        final_metrics = {}
        
        for delta_name, values in all_deltas.items():
            if values:
                aggregate_scores_raw[delta_name] = values
                final_metrics[delta_name] = {
                    'mean': float(np.mean(values)),
                    'std': float(np.std(values)),
                    'count': len(values),
                    'min': float(np.min(values)),
                    'max': float(np.max(values)),
                    'median': float(np.median(values))
                }
        
        # Helper for quant summary (rating and sentiment)
        all_actuals = [{'rating': r['actual']['rating'], 'sentiment': r['actual']['sentiment']} 
                      for u_reviews in predictions.values() for r in u_reviews if 'actual' in r]
        
        avg_rating = "N/A"
        if all_actuals:
            ratings = [r['rating'] for r in all_actuals if r['rating'] is not None]
            if ratings: 
                avg_rating = f"{np.mean(ratings):.2f}"
        
        # Calculate sentiment distribution
        sentiment_counts = Counter([r.get('sentiment', 'Unknown') for r in all_actuals if r.get('sentiment')])
        sentiment_dist = {key: round((count / len(all_actuals)) * 100, 1) for key, count in sentiment_counts.items()} if all_actuals else {}
        
        # Build summary data with deltas instead of recall metrics
        summary_data = {
            "metadata": {
                "persona_name": persona_context.get('persona_name'),
                "micro_cluster_id": tribe_id,
                "total_users_in_cluster": len(predictions),
                "total_reviews_from_cluster": len(all_actuals),
                "quantitative_summary": {
                    "average_rating": avg_rating,
                    "sentiment_distribution_percent": sentiment_dist
                },
                "qualitative_summary": persona_context.get('qualitative_summary', {}),
                "dataset_type": dataset_type
            },
            "model_type_used": "enhanced_persona_micro_cluster",
            "user_predictions": predictions,
            "aggregate_scores": aggregate_scores_raw,  # Raw arrays of delta values
            "final_metrics": final_metrics  # Aggregated delta statistics (mean, std, count, min, max, median)
        }
        
        # Validate against schema before saving
        scoring_mode = self.params.get('scoring_mode', 'confidence')
        from prediction_lib.generate_model_predictions import InitialPredictionsArtifactLogprobs, InitialPredictionsArtifact
        schema_class = InitialPredictionsArtifactLogprobs if scoring_mode == "logprobs" else InitialPredictionsArtifact
        
        logging.info(f"  🔍 Validating schema for {cluster_id}/{micro_id} (mode: {scoring_mode})...")
        try:
            validated_artifact = schema_class.from_dict(summary_data)
            validated_data = validated_artifact.to_dict()
            schema_validated = True
            logging.info(f"  ✅ Schema validation passed for {cluster_id}/{micro_id}")
        except Exception as e:
            logging.error(f"  ❌ Schema validation failed for {cluster_id}/{micro_id}: {e}")
            logging.error("  Saving without schema validation (data may be invalid)")
            from prediction_lib import utils as pred_utils
            validated_data = pred_utils.convert_to_serializable(summary_data)
            schema_validated = False
        
        # CRITICAL: Ensure topic_probabilities is present in actual sections
        # Keep predicted_themes if it exists (for recall calculation, not JSD)
        # Schema validation might have removed topic_probabilities
        if 'user_predictions' in validated_data:
            topic_probs_added_count = 0
            for user_id, user_reviews in validated_data['user_predictions'].items():
                if isinstance(user_reviews, list):
                    for review in user_reviews:
                        if 'actual' in review:
                            # CRITICAL: Ensure topic_probabilities is in actual section
                            # Check if it's missing or empty
                            has_valid_topic_probs = (
                                'topic_probabilities' in review['actual'] and 
                                isinstance(review['actual']['topic_probabilities'], dict) and 
                                len(review['actual']['topic_probabilities']) > 0
                            )
                            
                            if not has_valid_topic_probs:
                                # Try to get from top level of review (stored during processing)
                                topic_probs = review.get('topic_probabilities', {})
                                if topic_probs and isinstance(topic_probs, dict) and len(topic_probs) > 0:
                                    review['actual']['topic_probabilities'] = topic_probs
                                    topic_probs_added_count += 1
                                    logging.debug(f"Added topic_probabilities to actual section for review {review.get('asin', 'unknown')} after schema validation")
                                else:
                                    # Try to get from original predictions dict (before validation)
                                    # The original review data should have topic_probabilities
                                    asin = review.get('asin', '')
                                    if asin:
                                        # Search in original predictions for this review
                                        found_topic_probs = False
                                        for orig_user_id, orig_reviews in predictions.items():
                                            if isinstance(orig_reviews, list):
                                                for orig_review in orig_reviews:
                                                    if orig_review.get('asin') == asin:
                                                        # Try top level first (we store it there)
                                                        orig_topic_probs = orig_review.get('topic_probabilities', {})
                                                        if not orig_topic_probs:
                                                            # Fallback to actual section
                                                            orig_topic_probs = orig_review.get('actual', {}).get('topic_probabilities', {})
                                                        if orig_topic_probs and isinstance(orig_topic_probs, dict) and len(orig_topic_probs) > 0:
                                                            review['actual']['topic_probabilities'] = orig_topic_probs
                                                            topic_probs_added_count += 1
                                                            logging.debug(f"Restored topic_probabilities from original data for review {asin}")
                                                            found_topic_probs = True
                                                            break
                                                if found_topic_probs:
                                                    break
                                    
                                    # If still not found, log warning
                                    if not review['actual'].get('topic_probabilities'):
                                        logging.warning(f"  ⚠️  Review {review.get('asin', 'unknown')} missing topic_probabilities after schema validation - cannot restore")
                                        review['actual']['topic_probabilities'] = {}  # Set empty to avoid KeyError
            
            if cleanup_count > 0:
                logging.warning(f"  ⚠️  Removed predicted_themes from {cleanup_count} reviews after schema validation")
            if topic_probs_added_count > 0:
                logging.info(f"  ✅ Restored topic_probabilities for {topic_probs_added_count} reviews after schema validation")
        
        # Naming Convention
        infix = "_test" if dataset_type == "test" else ""
        filename = f"{micro_id}{infix}_summary_enhanced_persona_micro_cluster_accuracy.json"
        
        with open(cluster_dir / filename, 'w', encoding='utf-8') as f:
            json.dump(validated_data, f, indent=2, ensure_ascii=False)
        
        if schema_validated:
            logging.info(f"  💾 Saved {cluster_id}/{filename}: {len(predictions)} users, {len(all_actuals)} reviews with delta metrics (schema validated)")
        else:
            logging.warning(f"  ⚠️  Saved {cluster_id}/{filename}: {len(predictions)} users, {len(all_actuals)} reviews with delta metrics (schema validation failed)")
        
        # Log delta summary
        if final_metrics:
            logging.info(f"  📊 Delta Summary:")
            for delta_name, stats in final_metrics.items():
                logging.info(f"     {delta_name}: mean={stats['mean']:.4f}, std={stats['std']:.4f}, count={stats['count']}")
    
    def _parse_tribe_id(self, tribe_id):
        """Parse tribe_id to get cluster and micro IDs."""
        import re
        if "_micro_" in tribe_id:
            parts = tribe_id.split("_micro_")
            raw_cluster = parts[0]
            micro_id = f"micro_{parts[1]}"
            
            # Normalize segment_0 -> cluster_0
            match = re.search(r'(\d+)', raw_cluster)
            cluster_id = f"cluster_{match.group(1)}" if match else raw_cluster
            return cluster_id, micro_id
        return tribe_id, "micro_0"
    
    def _execute_parallel(self, tasks, indices_map):
        """Runs single review predictions in parallel threads with memory.
        Thread-safe: Uses locks for results collection and embeddings cache access.
        """
        from collections import defaultdict
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from threading import Lock
        
        results = defaultdict(list)
        failed_results = defaultdict(list)
        results_lock = Lock()  # Lock for thread-safe results collection
        
        with ThreadPoolExecutor(max_workers=self.params['num_workers']) as executor:
            future_map = {}
            for uid, idxs in indices_map.items():
                for i in idxs:
                    future = executor.submit(process_single_review_with_memory, tasks[i])
                    future_map[future] = (uid, i)
            
            for future in as_completed(future_map):
                uid, task_idx = future_map[future]
                try:
                    res = future.result()
                    if res:
                        with results_lock:
                            if res.get('status') == 'failed':
                                failed_results[uid].append(res)
                            else:
                                results[uid].append(res)
                except Exception as e:
                    logging.error(f"Review processing exception for {uid}: {e}")
                    actual_review = tasks[task_idx][0]
                    with results_lock:
                        failed_results[uid].append({
                            'status': 'failed',
                            'error': f'Processing exception: {str(e)}',
                            'error_type': 'exception',
                            'product_description': actual_review.get('product_description', ''),
                            'category': actual_review.get('category'),
                            'asin': actual_review.get('asin'),
                            'timestamp': actual_review.get('timestamp'),
                            'actual': {
                                'review_text': actual_review.get('review_text', ''),
                                'rating': actual_review.get('rating'),
                                'sentiment': actual_review.get('sentiment'),
                                'topic_probabilities': actual_review.get('topic_probabilities', {})  # Use topic_probabilities as actual (ground truth)
                            }
                        })
        
        return results, failed_results
    
    def _filter_tribes(self, tribe_data, target_clusters):
        """Filter tribes by cluster names. Accepts both 'cluster_X' and 'segment_X' formats.
        Also supports 'cluster_X micro_Y' format for specific micro cluster filtering."""
        if not target_clusters:
            return tribe_data
        
        import re
        
        # Parse target clusters - can be "cluster_X" or "cluster_X micro_Y"
        target_clusters_raw = set()
        target_clusters_segment = set()  # For matching tribe_ids
        target_micro_map = {}  # Map cluster to micro: {"segment_0": "micro_15"}
        
        for cluster_spec in target_clusters:
            parts = cluster_spec.strip().split()
            if len(parts) == 2:
                # Format: "cluster_X micro_Y"
                cluster_name = parts[0]
                micro_name = parts[1]
                target_clusters_raw.add(cluster_name)
                # Convert to segment format
                if cluster_name.startswith("cluster_"):
                    segment_name = cluster_name.replace("cluster_", "segment_", 1)
                    target_clusters_segment.add(segment_name)
                    target_micro_map[segment_name] = micro_name
                elif cluster_name.startswith("segment_"):
                    target_clusters_segment.add(cluster_name)
                    target_micro_map[cluster_name] = micro_name
            else:
                # Format: "cluster_X" (all micro clusters in this cluster)
                cluster_name = parts[0]
                target_clusters_raw.add(cluster_name)
                if cluster_name.startswith("cluster_"):
                    segment_name = cluster_name.replace("cluster_", "segment_", 1)
                    target_clusters_segment.add(segment_name)
                elif cluster_name.startswith("segment_"):
                    target_clusters_segment.add(cluster_name)
                else:
                    # Try to extract number
                    match = re.search(r'(\d+)', cluster_name)
                    if match:
                        num = match.group(1)
                        target_clusters_segment.add(f"segment_{num}")
        
        # Filter tribes that belong to target clusters
        filtered_tribe_seed_data = {}
        for tribe_id, tribe_seed in tribe_data.items():
            # Extract cluster and micro from tribe_id (e.g., "segment_0_micro_15" -> "segment_0", "micro_15")
            if "_micro_" in tribe_id:
                cluster_id = tribe_id.split("_micro_")[0]
                micro_id = f"micro_{tribe_id.split('_micro_')[1]}"
            else:
                cluster_id = tribe_id
                micro_id = None
            
            # Check if cluster matches
            if cluster_id in target_clusters_segment:
                # If specific micro cluster is specified, check it matches
                if cluster_id in target_micro_map:
                    if micro_id == target_micro_map[cluster_id]:
                        filtered_tribe_seed_data[tribe_id] = tribe_seed
                else:
                    # No specific micro specified, include all micros in this cluster
                    filtered_tribe_seed_data[tribe_id] = tribe_seed
        
        if not filtered_tribe_seed_data:
            logging.error(f"No tribes found for specified clusters: {target_clusters}")
            available = set(tid.split('_micro_')[0] if '_micro_' in tid else tid for tid in tribe_data.keys())
            logging.error(f"Available clusters: {available}")
            return {}
        
        if target_micro_map:
            logging.info(f"Filtered to {len(filtered_tribe_seed_data)} tribe(s) matching: {target_clusters}")
        else:
            logging.info(f"Filtered to {len(filtered_tribe_seed_data)} tribes in clusters: {sorted(target_clusters_raw)}")
        return filtered_tribe_seed_data
    
    def run_pipeline(self, dataset_type, tribe_seed_data, user_backstories, review_data, 
                     topic_universe, user_to_tribe_map, output_artifact_name, target_clusters=None):
        """Main execution method with memory loading."""
        logging.info(f"Starting final predictions pipeline for {dataset_type.upper()} set with mode: {self.mode}")
        
        # 1. Setup Theme Map
        category_themes_map = self._build_theme_map(topic_universe)
        logging.info(f"Built theme map with {len(category_themes_map)} categories")
        
        # 2. Filter Tribes if target_clusters provided
        if target_clusters:
            tribe_seed_data = self._filter_tribes(tribe_seed_data, target_clusters)
        
        # 3. Prepare Output Directory
        output_stage = self.params.get("output_stage", "07_sgo_training")
        from utils.wandb_utils import get_artifact_dir
        artifact_dir = get_artifact_dir(output_stage, output_artifact_name)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        
        all_tribe_predictions = {}
        from collections import defaultdict
        clusters_dict = defaultdict(lambda: defaultdict(dict))
        
        # 4. Main Loop
        for tribe_id, tribe_seed in tribe_seed_data.items():
            logging.info(f"\n{'='*60}")
            logging.info(f"Processing Tribe: {tribe_id}")
            logging.info(f"{'='*60}")
            
            # Parse tribe_id to get cluster and micro IDs
            cluster_id, micro_id = self._parse_tribe_id(tribe_id)
            
            # Load memory data for this tribe (will be filtered per user later)
            memory_data = {}
            if self.memory_base_dir:
                memory_data = load_memory_file(self.memory_base_dir, cluster_id, micro_id)
                if memory_data:
                    logging.info(f"  ✅ Loaded memory data for {cluster_id}/{micro_id}")
                else:
                    logging.warning(f"  ⚠️  No memory data found for {cluster_id}/{micro_id}")
            
            # Identify Users
            tribe_users = [uid for uid, tid in user_to_tribe_map.items() if tid == tribe_id]
            if not tribe_users:
                logging.warning(f"  ⚠️  No users found for tribe {tribe_id}, skipping")
                continue
            logging.info(f"  Found {len(tribe_users)} users in tribe")
            
            # Prepare Data for Workers
            all_review_tasks = []
            user_review_indices = defaultdict(list)
            task_keys_seen = set()
            
            # Initialize embeddings cache for this tribe (shared across threads)
            embeddings_cache = {}
            embeddings_cache_lock = Lock()  # Lock for thread-safe cache access
            
            persona_context = {
                'persona_name': tribe_seed.persona_name,
                'qualitative_summary': tribe_seed.qualitative_summary.model_dump() if hasattr(tribe_seed.qualitative_summary, 'model_dump') else tribe_seed.qualitative_summary
            }
            
            # Build Tasks (same as parent class but with memory_text)
            users_with_reviews = 0
            reviews_skipped_no_data = 0
            reviews_skipped_no_themes = 0
            reviews_skipped_empty_themes = 0
            reviews_skipped_duplicate = 0
            
            for user_id in tribe_users:
                if user_id not in review_data:
                    continue
                
                user_story = user_backstories.get(user_id)
                user_summary = user_story.overall_characteristics.influencing_characteristics_summary if user_story else ""
                if not user_summary:
                    continue
                
                reviews = review_data[user_id].get('reviews', [])
                if not reviews:
                    continue
                
                users_with_reviews += 1
                
                # Format memory for this specific user (filter analyses by user_id)
                persona_name = tribe_seed.persona_name if hasattr(tribe_seed, 'persona_name') else f'persona_{tribe_id}'
                user_memory_text = format_memory_for_prompt(memory_data, persona_name, user_id) if memory_data else ""
                
                # Print memory for this user
                if user_memory_text:
                    logging.info(f"  📝 User {user_id} memory context:")
                    logging.info(f"     {user_memory_text.strip()}")
                else:
                    logging.info(f"  📝 User {user_id}: No memory found")
                
                for review_idx, review in enumerate(reviews):
                    cat = review.get('category')
                    if not cat:
                        reviews_skipped_no_data += 1
                        continue
                    
                    review_themes = review.get('themes', []) or review.get('predicted_themes', [])
                    if not review_themes or (isinstance(review_themes, list) and len(review_themes) == 0):
                        reviews_skipped_empty_themes += 1
                        continue
                    
                    asin = review.get('asin', '')
                    timestamp = review.get('timestamp', '')
                    review_text = review.get('review_text', '')
                    task_key = f"{user_id}_{asin}_{timestamp}_{hash(review_text[:50])}"
                    if task_key in task_keys_seen:
                        reviews_skipped_duplicate += 1
                        continue
                    task_keys_seen.add(task_key)
                    
                    # Create review_key for embedding cache (same format as SGO training)
                    review_key = f"{user_id}_review_{review_idx}"
                    
                    from prediction_lib import config as lib_config
                    main_cat = lib_config.map_category_to_main_category(cat, self.category_mapping) or cat
                    normalized_main = self._normalize_category(main_cat)
                    
                    themes = category_themes_map.get(normalized_main, [])
                    if not themes:
                        reviews_skipped_no_themes += 1
                        continue
                    
                    cat_summary = ""
                    if user_story and user_story.category_characteristics:
                        c_chars = user_story.category_characteristics.get(main_cat) or user_story.category_characteristics.get(cat)
                        if c_chars: 
                            cat_summary = c_chars.influencing_characteristics_summary
                    
                    # Add user_memory_text (filtered by user_id), review_key, embeddings_cache, lock, and mode to task tuple
                    task = (review, persona_context, user_summary, cat_summary, main_cat, themes,
                            user_memory_text, self.client, self.rate_limiter, self.prompt_template,
                            self.params.get('model'),
                            self.params.get('max_tokens'),
                            self.params.get('temperature'),
                            self.params.get('max_retries', 3),
                            self.params.get('scoring_mode', 'confidence'),
                            self.params.get('theme_prediction_model'),
                            review_key, embeddings_cache, embeddings_cache_lock, self.mode)
                    
                    user_review_indices[user_id].append(len(all_review_tasks))
                    all_review_tasks.append(task)
            
            if not all_review_tasks:
                logging.warning(f"  ⚠️  No review tasks created for tribe {tribe_id}")
                continue
            
            logging.info(f"  Created {len(all_review_tasks)} review tasks from {users_with_reviews} users")
            
            # Run Parallel Execution
            tribe_user_predictions, tribe_failed_predictions = self._execute_parallel(all_review_tasks, user_review_indices)
            
            # Store results
            all_tribe_predictions[tribe_id] = tribe_user_predictions
            clusters_dict[cluster_id][micro_id] = tribe_user_predictions
            
            # Collect and save deltas for all reviews
            all_reviews_deltas = []
            for user_id, user_results in tribe_user_predictions.items():
                for review_idx, result in enumerate(user_results):
                    if result.get('status') == 'success' and 'deltas' in result:
                        deltas = result.get('deltas', {})
                        # Extract review information
                        review_key = f"{user_id}_review_{review_idx}"
                        
                        # Build comprehensive delta info (matching SGO training format)
                        delta_info = {
                            'review_key': review_key,
                            'user_id': user_id,
                            'review_idx': review_idx,
                            'text_delta': deltas.get('text_delta'),
                            'theme_delta': deltas.get('theme_delta'),
                            'theme_jsd': deltas.get('theme_jsd'),  # Explicit JSD field for logprobs mode
                            'overall_delta': deltas.get('overall_delta'),
                            'delta_weights': deltas.get('delta_weights', {'text': 0.7, 'theme': 0.3}),
                            'prediction': {
                                'review_text': result.get('prediction', {}).get('review_text', ''),
                                'rating': result.get('prediction', {}).get('rating'),
                                'sentiment': result.get('prediction', {}).get('sentiment'),
                                'predicted_themes': result.get('prediction', {}).get('predicted_themes', {})
                            },
                            'actual': {
                                'review_text': result.get('actual', {}).get('review_text', ''),
                                'rating': result.get('actual', {}).get('rating'),
                                'sentiment': result.get('actual', {}).get('sentiment'),
                                'topic_probabilities': result.get('actual', {}).get('topic_probabilities', {})  # Use topic_probabilities as actual (ground truth)
                            },
                            'category': result.get('category'),
                            'product_description': result.get('product_description', '')
                        }
                        all_reviews_deltas.append(delta_info)
            
            # Save deltas to file (matching SGO training format)
            if all_reviews_deltas:
                import time
                delta_dir = artifact_dir / "deltas" / cluster_id
                delta_dir.mkdir(parents=True, exist_ok=True)
                delta_file = delta_dir / f"{micro_id}_all_reviews_deltas.json"
                
                all_deltas_data = {
                    'cluster_id': cluster_id,
                    'micro_cluster_id': micro_id,
                    'tribe_id': tribe_id,
                    'total_reviews': len(all_reviews_deltas),
                    'calculation_timestamp': time.time(),
                    'deltas': all_reviews_deltas
                }
                
                import json
                with open(delta_file, 'w') as f:
                    json.dump(all_deltas_data, f, indent=2)
                
                logging.info(f"  💾 Saved deltas for {len(all_reviews_deltas)} reviews to: {delta_file}")
                
                # Print delta statistics
                if all_reviews_deltas:
                    import numpy as np
                    theme_deltas = [d.get('theme_delta') for d in all_reviews_deltas if d.get('theme_delta') is not None]
                    text_deltas = [d.get('text_delta') for d in all_reviews_deltas if d.get('text_delta') is not None]
                    overall_deltas = [d.get('overall_delta') for d in all_reviews_deltas if d.get('overall_delta') is not None]
                    
                    if theme_deltas:
                        logging.info(f"  📊 Delta Statistics:")
                        logging.info(f"     Theme Delta:   min={min(theme_deltas):.4f}, max={max(theme_deltas):.4f}, mean={np.mean(theme_deltas):.4f}, median={np.median(theme_deltas):.4f}")
                    if text_deltas:
                        logging.info(f"     Text Delta:    min={min(text_deltas):.4f}, max={max(text_deltas):.4f}, mean={np.mean(text_deltas):.4f}, median={np.median(text_deltas):.4f}")
                    if overall_deltas:
                        logging.info(f"     Overall Delta: min={min(overall_deltas):.4f}, max={max(overall_deltas):.4f}, mean={np.mean(overall_deltas):.4f}, median={np.median(overall_deltas):.4f}")
            
            # Save failed predictions
            if tribe_failed_predictions:
                self._save_failed_predictions(artifact_dir, cluster_id, micro_id, tribe_id, tribe_failed_predictions, dataset_type)
            
            # Save Micro-Cluster Files
            self._save_micro_cluster_data(
                artifact_dir, cluster_id, micro_id, tribe_id,
                tribe_user_predictions, persona_context, dataset_type
            )
            
            # Update Cluster Grand Summary
            self._update_grand_summary(artifact_dir, cluster_id, micro_id, dataset_type)
        
        # Verify all files are saved
        self._verify_saved_files(artifact_dir, clusters_dict)
        
        # Final Logging & Artifacts
        self._log_final_artifacts(self.run, artifact_dir, output_artifact_name, all_tribe_predictions, dataset_type)


def main():
    parser = argparse.ArgumentParser(description='Generate final predictions with memory from SGO training')
    parser.add_argument('--only-cluster', type=str, help='Process only this specific cluster (e.g., cluster_0)')
    parser.add_argument('--start-from-cluster', type=str, help='Start processing from this cluster onwards')
    parser.add_argument('--only-micro', type=str, help='Process only this specific micro cluster (e.g., micro_15). Must be used with --only-cluster')
    parser.add_argument('--dataset-type', type=str, choices=['train', 'test'], default='train', help='Dataset type: train or test (default: train)')
    args = parser.parse_args()
    
    # Validate micro cluster argument
    if args.only_micro and not args.only_cluster:
        logging.error("❌ --only-micro requires --only-cluster to be specified")
        return
    
    # Config - use pre_sgo_predictions config structure
    config = load_config()
    stage_config = get_stage_config("07_pre_sgo_predictions")
    openai_config = get_openai_config()
    
    logging.info(f"Using config from stage: 07_pre_sgo_predictions")
    
    if not stage_config:
        logging.error("❌ Stage config not found")
        return
    
    # Get mode from sgo_training config
    sgo_config = get_stage_config("07_sgo_training")
    mode = "final_train"  # default
    if sgo_config:
        mode = sgo_config.get("final_predictions_mode", "final_train")
        if mode not in ["final_train", "final_train_chars"]:
            logging.warning(f"⚠️  Invalid final_predictions_mode in config: {mode}, using default: final_train")
            mode = "final_train"
    
    logging.info(f"Using mode from config: {mode}")
    
    # Memory base directory will be set after downloading sgo_training_results from config
    memory_base_dir = None
    
    # Use same config loading as pre_sgo_predictions
    input_artifacts = stage_config.get("input_artifacts", {})
    output_artifacts = stage_config.get("output_artifacts", {})
    hyperparams = stage_config.get("hyperparameters", {})
    file_patterns = stage_config.get("file_patterns", {})
    dataset_pattern_mapping = stage_config.get("dataset_pattern_mapping", {})
    prompt_config = stage_config.get("prompt", {})
    schema_config = stage_config.get("schema", {})
    
    # Get sgo_training_results artifact from config (for memory, user chars, category chars, tribe chars)
    sgo_training_results_artifact = input_artifacts.get("sgo_training_results")
    if not sgo_training_results_artifact:
        logging.error("❌ sgo_training_results not found in input_artifacts config (required for final predictions)")
        return
    
    # Get all required configs (same as pre_sgo_predictions)
    tribe_seed_artifact = input_artifacts.get("tribe_seed_characteristics")
    user_backstory_artifact = input_artifacts.get("user_backstories")
    training_data_artifact = input_artifacts.get("training_data_with_topics")
    topic_universe_artifact = input_artifacts.get("topic_universe")
    user_tribes_artifact = input_artifacts.get("user_tribes")
    category_mapping_artifact = input_artifacts.get("category_mapping")
    
    # Get scoring mode and model config
    scoring_mode = stage_config.get("scoring_mode", "confidence")
    model_name = hyperparams.get("model")
    theme_prediction_model = None
    if scoring_mode in ["logprobs", "logprobs-without-persona-context"]:
        review_prediction_model = stage_config.get("review_prediction_model")
        theme_prediction_model = stage_config.get("logprobs_model_id") or stage_config.get("theme_prediction_model")
        if review_prediction_model:
            model_name = review_prediction_model
    else:
        base_url = os.environ.get("OPENAI_BASE_URL", "").strip()
        if base_url and "bedrock-mantle" in base_url and stage_config.get("bedrock_model_id"):
            model_name = stage_config["bedrock_model_id"]
    
    # Select prompt filename based on scoring mode
    use_enhanced = stage_config.get("use_enhanced_prompt", False)
    if scoring_mode in ["logprobs", "logprobs-without-persona-context"]:
        prompt_filename = "final_prediction_prompt_logprobs.txt"
    else:
        prompt_filename = "final_prediction_prompt.txt"
    
    # Load prompt from sgo_training prompts directory
    stage_dir = Path(__file__).parent.parent
    prompt_dir = stage_dir / "prompts"
    prompt_path = prompt_dir / prompt_filename
    
    if not prompt_path.exists():
        logging.error(f"❌ Prompt file not found: {prompt_path}")
        return
    
    prompt_template = load_prompt(prompt_dir, prompt_filename)
    logging.info(f"✅ Loaded prompt template from: {prompt_path}")
    
    # Output artifact name - save to W&B with fixed name
    output_artifact = "sgo_train_final_predictions"
    logging.info(f"Output artifact name: {output_artifact}")
    
    # Initialize W&B run
    run = init_wandb_run(
        run_name=f"final_predictions_{mode}",
        stage="07_sgo_training",
        config={
            "description": f"Generate final predictions with memory (mode: {mode})",
            "mode": mode,
            "memory_base_dir": memory_base_dir,
            "output_artifact": output_artifact
        }
    )
    
    if run is None:
        logging.error("❌ W&B run initialization failed")
        return
    
    try:
        # Download sgo_training_results from wandb for memory, user chars, category chars, tribe chars
        logging.info(f"Downloading {sgo_training_results_artifact} from W&B for memory and characteristics...")
        from utils.wandb_utils import use_artifact
        sgo_results_path = use_artifact(run, sgo_training_results_artifact, artifact_type="dataset")
        if not sgo_results_path:
            logging.error(f"❌ Could not download {sgo_training_results_artifact} from W&B")
            finish_run(run)
            return
        
        sgo_results_path = Path(sgo_results_path)
        logging.info(f"✅ Downloaded {sgo_training_results_artifact} to: {sgo_results_path}")
        
        # Set memory_base_dir to _memory folder in sgo_training_results
        memory_base_dir = str(sgo_results_path / "_memory")
        if not Path(memory_base_dir).exists():
            logging.warning(f"⚠️  Memory directory not found in {sgo_training_results_artifact}: {memory_base_dir}")
            memory_base_dir = None
        else:
            logging.info(f"✅ Using memory directory: {memory_base_dir}")
        # Init Resources
        api_key = openai_config.get("api_key") or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            logging.error("❌ OPENAI_API_KEY not found")
            return

        if scoring_mode in ["logprobs", "logprobs-without-persona-context"]:
            client = OpenAI(api_key=api_key)
        else:
            client = create_openai_client(openai_config=openai_config, timeout=120.0)
        loader = DataLoader(run, file_patterns, dataset_pattern_mapping)
        limiter = RateLimiter(min_interval=hyperparams.get("min_request_interval"))
        
        # Load Data based on dataset_type (from args or config)
        sgo_config = get_stage_config("07_sgo_training")
        dataset_type = args.dataset_type or (sgo_config.get("dataset_type", "train") if sgo_config else "train")
        logging.info(f"Loading {dataset_type} data from W&B...")
        
        tribe_seed_data = loader.load_tribe_seeds(tribe_seed_artifact)
        user_backstories = loader.load_user_backstories(user_backstory_artifact)
        topic_universe = loader.load_topic_universe(topic_universe_artifact)
        category_mapping = loader.load_category_mapping(category_mapping_artifact)
        
        # Load review data based on dataset_type
        if dataset_type == "test":
            # For test mode: load from test_micro_cluster_details artifact
            test_data_artifact = input_artifacts.get("test_micro_cluster_details") or input_artifacts.get("ground_truth_test_micro_cluster_details")
            if not test_data_artifact:
                logging.error("❌ test_micro_cluster_details or ground_truth_test_micro_cluster_details not found in config (required for test mode)")
                finish_run(run)
                return
            
            logging.info(f"Downloading {test_data_artifact} from W&B for test data...")
            from utils.wandb_utils import use_artifact
            test_data_path = use_artifact(run, test_data_artifact, artifact_type="dataset")
            if not test_data_path:
                logging.error(f"❌ Could not download {test_data_artifact} from W&B")
                finish_run(run)
                return
            
            test_data_path = Path(test_data_path)
            logging.info(f"✅ Downloaded test data to: {test_data_path}")
            
            # Convert test_micro_cluster_details structure to review_data format
            # Structure: cluster_X/micro_Y_details.json with members_grouped_by_user
            review_data = {}
            user_to_tribe_map = {}
            
            # Process each cluster/micro file
            for cluster_dir in test_data_path.iterdir():
                if not cluster_dir.is_dir() or not cluster_dir.name.startswith('cluster_'):
                    continue
                
                cluster_id = cluster_dir.name
                for micro_file in cluster_dir.glob('micro_*_details.json'):
                    micro_id = micro_file.stem.replace('_details', '')
                    with open(micro_file, 'r', encoding='utf-8') as f:
                        micro_data = json.load(f)
                    
                    # Extract members_grouped_by_user
                    members = micro_data.get('members_grouped_by_user', {})
                    for user_id, reviews in members.items():
                        if isinstance(reviews, list):
                            review_data[user_id] = {'reviews': reviews}
                            # Map user to tribe (segment_X_micro_Y format)
                            tribe_id = f"segment_{cluster_id.split('_')[1]}_micro_{micro_id.split('_')[1]}"
                            user_to_tribe_map[user_id] = tribe_id
            
            logging.info(f"✅ Loaded test data: {len(review_data)} users from test_micro_cluster_details")
        else:
            # For train mode: load from sgo_training_results_V3 (contains corrected predictions)
            # This is more efficient - we already have the data structured by cluster/micro
            logging.info(f"Loading train data from {sgo_training_results_artifact}...")
            from utils.wandb_utils import use_artifact
            sgo_results_path = use_artifact(run, sgo_training_results_artifact, artifact_type="dataset")
            if not sgo_results_path:
                logging.error(f"❌ Could not download {sgo_training_results_artifact} from W&B")
                finish_run(run)
                return
            
            sgo_results_path = Path(sgo_results_path)
            logging.info(f"✅ Downloaded sgo_training_results to: {sgo_results_path}")
            
            # Load review data from corrected prediction files in sgo_training_results
            # Structure: cluster_X/micro_Y_summary_enhanced_delta_corrected.json
            review_data = {}
            user_to_tribe_map = {}
            
            # Process each cluster/micro file
            for cluster_dir in sgo_results_path.iterdir():
                if not cluster_dir.is_dir() or not cluster_dir.name.startswith('cluster_'):
                    continue
                
                cluster_id = cluster_dir.name
                for summary_file in cluster_dir.glob('*_summary_enhanced_delta_corrected.json'):
                    # Extract micro_id from filename (e.g., micro_15_summary_enhanced_delta_corrected.json)
                    micro_id = summary_file.stem.replace('_summary_enhanced_delta_corrected', '')
                    
                    try:
                        with open(summary_file, 'r', encoding='utf-8') as f:
                            summary_data = json.load(f)
                        
                        # Extract user_predictions from summary file
                        user_predictions = summary_data.get('user_predictions', {})
                        for user_id, user_reviews in user_predictions.items():
                            if isinstance(user_reviews, list):
                                review_data[user_id] = {'reviews': user_reviews}
                                # Map user to tribe (segment_X_micro_Y format)
                                tribe_id = f"segment_{cluster_id.split('_')[1]}_micro_{micro_id.split('_')[1]}"
                                user_to_tribe_map[user_id] = tribe_id
                    except Exception as e:
                        logging.warning(f"Error loading {summary_file}: {e}")
                        continue
            
            logging.info(f"✅ Loaded train data: {len(review_data)} users from sgo_training_results_V3")
        
        if not all([tribe_seed_data, user_backstories, review_data, topic_universe, user_to_tribe_map, category_mapping]):
            logging.error("❌ Failed to load required data")
            finish_run(run)
            return
        
        # Determine target clusters from arguments (before creating orchestrator)
        target_clusters = None
        if args.only_cluster:
            if args.only_micro:
                # Format: "cluster_X micro_Y" for filtering specific tribe
                target_clusters = [f"{args.only_cluster} {args.only_micro}"]
                logging.info(f"Processing only {args.only_cluster}/{args.only_micro}")
            else:
                target_clusters = [args.only_cluster]
        elif args.start_from_cluster:
            # Filter tribes starting from this cluster
            import re
            try:
                start_num = int(re.search(r'cluster_(\d+)', args.start_from_cluster).group(1))
                # Filter tribe_seed_data to only include clusters >= start_num
                filtered_tribes = {}
                for tribe_id, tribe_seed in tribe_seed_data.items():
                    # Parse tribe_id to get cluster_id (same logic as _parse_tribe_id)
                    if "_micro_" in tribe_id:
                        parts = tribe_id.split("_micro_")
                        raw_cluster = parts[0]
                        match = re.search(r'(\d+)', raw_cluster)
                        cluster_id = f"cluster_{match.group(1)}" if match else raw_cluster
                    else:
                        cluster_id = tribe_id
                    
                    cluster_num = int(re.search(r'cluster_(\d+)', cluster_id).group(1))
                    if cluster_num >= start_num:
                        filtered_tribes[tribe_id] = tribe_seed
                tribe_seed_data = filtered_tribes
                logging.info(f"Filtered to {len(tribe_seed_data)} tribes starting from {args.start_from_cluster}")
            except (AttributeError, ValueError):
                logging.warning(f"Invalid start_from_cluster format: {args.start_from_cluster}. Ignoring.")
        
        # Prepare hyperparams
        output_config = stage_config.get("output", {})
        output_stage = output_config.get("stage", "07_sgo_training")
        hyperparams_with_output = hyperparams.copy()
        hyperparams_with_output["output_stage"] = output_stage
        hyperparams_with_output["scoring_mode"] = scoring_mode
        if theme_prediction_model:
            hyperparams_with_output["theme_prediction_model"] = theme_prediction_model
        
        # Run Pipeline with memory
        orchestrator = FinalPredictionsOrchestrator(
            run, client, limiter,
            prompt_template,
            hyperparams_with_output,
            category_mapping=category_mapping,
            memory_base_dir=memory_base_dir,
            mode=mode
        )
        
        orchestrator.run_pipeline(
            dataset_type=dataset_type,
            tribe_seed_data=tribe_seed_data,
            user_backstories=user_backstories,
            review_data=review_data,
            topic_universe=topic_universe,
            user_to_tribe_map=user_to_tribe_map,
            output_artifact_name=output_artifact,
            target_clusters=target_clusters
        )
        
        logging.info("Final predictions generation completed successfully!")
        
    except Exception as e:
        logging.error(f"Error in main execution: {e}", exc_info=True)
        if 'run' in locals():
            from utils.wandb_utils import log_summary
            log_summary(run, {"status": "failed", "error": str(e)})
        raise
    
    finally:
        if 'run' in locals() and run is not None:
            finish_run(run)


if __name__ == "__main__":
    main()

