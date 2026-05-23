#!/usr/bin/env python3
"""
Convert Memory Analyses to User Learnings
==========================================

This script processes memory analyses from SGO training and converts them into
clean, structured user learnings. It extracts key patterns and learnings from
the noisy analysis text and formats them for use in prompts.

Then uses LLM to convert learnings into NEW characteristics that are NOT already
in seed backstory, user characteristics, or category characteristics.

Usage:
    python 07_sgo_training/scripts/convert_memory_to_user_learnings.py --cluster cluster_4 --micro micro_0
    python 07_sgo_training/scripts/convert_memory_to_user_learnings.py --all-clusters
"""

import sys
import json
import logging
import argparse
import os
from pathlib import Path
from collections import defaultdict
import re
import time

# Add project root and scripts_sgo to path
project_root = Path(__file__).parent.parent.parent
scripts_sgo = Path(__file__).resolve().parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
if str(scripts_sgo) not in sys.path:
    sys.path.insert(0, str(scripts_sgo))

from utils.openai_chat_params import build_chat_completion_kwargs

# Load .env so OPENAI_API_KEY and OPENAI_BASE_URL (Bedrock) are set
try:
    from dotenv import load_dotenv
    load_dotenv(project_root / ".env", override=True)
except ImportError:
    pass

from utils.openai_client import create_openai_client
from utils.wandb_utils import load_config, get_stage_config

# Import io_utils directly
sys.path.insert(0, str(Path(__file__).parent.parent / 'utils'))
try:
    from io_utils import load_json_file, save_json_file
except ImportError:
    # Fallback: simple JSON loading
    def load_json_file(file_path, default=None):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return default if default is not None else {}
    
    def save_json_file(file_path, data):
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def extract_learnings_from_analysis(analysis_text: str) -> dict:
    """
    Extract structured learnings from a raw analysis text.
    
    Analyses typically have format:
    [Persona traits] [Learning/pattern description]
    
    Returns:
        dict with keys: traits, learning, patterns
    """
    if not analysis_text or not isinstance(analysis_text, str):
        return {'traits': [], 'learning': '', 'patterns': []}
    
    # Try to extract structured parts
    # Pattern: [trait1, trait2] [learning text]
    pattern = r'\[([^\]]+)\]\s*\[([^\]]+)\]'
    matches = re.findall(pattern, analysis_text)
    
    traits = []
    learnings = []
    
    for match in matches:
        if len(match) >= 2:
            # First bracket: traits
            trait_text = match[0].strip()
            if trait_text:
                # Split by comma if multiple traits
                traits.extend([t.strip() for t in trait_text.split(',') if t.strip()])
            
            # Second bracket: learning
            learning_text = match[1].strip()
            if learning_text:
                learnings.append(learning_text)
    
    # If no structured pattern found, use the whole text as learning
    if not learnings:
        learnings = [analysis_text.strip()] if analysis_text.strip() else []
    
    # Extract key patterns (look for common patterns)
    patterns = []
    learning_text = ' '.join(learnings)
    
    # Look for pattern indicators
    if 'over-predicted' in learning_text.lower() or 'overpredicted' in learning_text.lower():
        patterns.append('over-prediction')
    if 'missed' in learning_text.lower() or 'under-predicted' in learning_text.lower():
        patterns.append('under-prediction')
    if 'hallucinated' in learning_text.lower():
        patterns.append('hallucination')
    if 'falsely' in learning_text.lower() or 'false' in learning_text.lower():
        patterns.append('false-positive')
    
    return {
        'traits': list(set(traits)),  # Remove duplicates
        'learning': ' | '.join(learnings),
        'patterns': patterns,
        'raw': analysis_text
    }


def process_user_memory(memory_data: dict, user_id: str) -> dict:
    """
    Process all memory analyses for a specific user and extract learnings.
    
    Returns:
        dict with structured learnings
    """
    user_analyses = []
    
    # Extract all analyses for this user
    for batch_key, batch_data in memory_data.items():
        if not isinstance(batch_data, dict):
            continue
        
        reviews = batch_data.get('reviews', [])
        analyses = batch_data.get('analyses', [])
        
        if not isinstance(reviews, list) or not isinstance(analyses, list):
            continue
        
        # Find analyses for this user
        for idx, review_key in enumerate(reviews):
            if isinstance(review_key, str) and review_key.startswith(f"{user_id}_review_"):
                if idx < len(analyses):
                    analysis = analyses[idx]
                    if analysis:
                        user_analyses.append(str(analysis))
    
    if not user_analyses:
        return {
            'user_id': user_id,
            'num_analyses': 0,
            'learnings': [],
            'summary': ''
        }
    
    # Extract learnings from each analysis
    all_learnings = []
    all_traits = set()
    all_patterns = set()
    
    for analysis in user_analyses:
        learning_data = extract_learnings_from_analysis(analysis)
        all_learnings.append(learning_data)
        all_traits.update(learning_data['traits'])
        all_patterns.update(learning_data['patterns'])
    
    # Create summary of key learnings
    # Group by pattern type
    over_prediction_learnings = [l['learning'] for l in all_learnings if 'over-prediction' in l['patterns']]
    under_prediction_learnings = [l['learning'] for l in all_learnings if 'under-prediction' in l['patterns']]
    hallucination_learnings = [l['learning'] for l in all_learnings if 'hallucination' in l['patterns']]
    false_positive_learnings = [l['learning'] for l in all_learnings if 'false-positive' in l['patterns']]
    other_learnings = [l['learning'] for l in all_learnings if not l['patterns']]
    
    # Build summary
    summary_parts = []
    if over_prediction_learnings:
        summary_parts.append(f"Over-prediction patterns: {len(over_prediction_learnings)} instances")
    if under_prediction_learnings:
        summary_parts.append(f"Under-prediction patterns: {len(under_prediction_learnings)} instances")
    if hallucination_learnings:
        summary_parts.append(f"Hallucination patterns: {len(hallucination_learnings)} instances")
    if false_positive_learnings:
        summary_parts.append(f"False-positive patterns: {len(false_positive_learnings)} instances")
    if other_learnings:
        summary_parts.append(f"Other learnings: {len(other_learnings)} instances")
    
    summary = '; '.join(summary_parts) if summary_parts else 'No patterns identified'
    
    return {
        'user_id': user_id,
        'num_analyses': len(user_analyses),
        'learnings': all_learnings,
        'traits': list(all_traits),
        'patterns': list(all_patterns),
        'summary': summary,
        'formatted_learnings': ' | '.join([l['learning'] for l in all_learnings])
    }


def convert_learnings_to_characteristics(
    user_learnings: dict,
    seed_characteristics: dict,
    user_characteristics: dict,
    category_characteristics: dict,
    client,
    model: str
) -> dict:
    """
    Use LLM to convert user learnings into NEW characteristics that are NOT already
    in seed backstory, user characteristics, or category characteristics.
    
    Returns:
        dict with new characteristics in format:
        {
            'core_characteristics': [...],
            'key_motivations': [...],
            'common_praises': [...],
            'common_criticisms': [...],
            'potential_goals': [...]
        }
    """
    if not user_learnings or user_learnings.get('num_analyses', 0) == 0:
        return {}
    
    learnings_text = user_learnings.get('formatted_learnings', '')
    if not learnings_text:
        return {}
    
    # Format existing characteristics for comparison
    seed_core = seed_characteristics.get('core_characteristics', [])
    seed_motivations = seed_characteristics.get('key_motivations', [])
    seed_praises = seed_characteristics.get('common_praises', [])
    seed_criticisms = seed_characteristics.get('common_criticisms', [])
    seed_goals = seed_characteristics.get('potential_goals', [])
    
    user_chars_text = user_characteristics.get('influencing_characteristics_summary', '')
    category_chars_text = category_characteristics.get('influencing_characteristics_summary', '')
    
    prompt = f"""You are analyzing user learnings from SGO training to extract NEW characteristics that are NOT already present in existing persona, user, or category characteristics.

**EXISTING SEED PERSONA CHARACTERISTICS:**
- Core Characteristics: {', '.join(seed_core) if seed_core else 'None'}
- Key Motivations: {', '.join(seed_motivations) if seed_motivations else 'None'}
- Common Praises: {', '.join(seed_praises) if seed_praises else 'None'}
- Common Criticisms: {', '.join(seed_criticisms) if seed_criticisms else 'None'}
- Potential Goals: {', '.join(seed_goals) if seed_goals else 'None'}

**EXISTING USER CHARACTERISTICS:**
{user_chars_text if user_chars_text else 'None'}

**EXISTING CATEGORY CHARACTERISTICS:**
{category_chars_text if category_chars_text else 'None'}

**USER LEARNINGS FROM SGO TRAINING:**
{learnings_text}

**TASK:**
Based on the learnings above, write a CLEAR, CONCISE written summary of what was learned about this user from the training mistakes. The summary should:
1. Describe 4-5 NEW characteristics that are NOT already covered by existing seed persona, user, or category characteristics
2. Be directly derived from the learnings (what patterns/mistakes were identified)
3. Explain how these characteristics would help improve future predictions for this user
4. Be written as a natural paragraph (not bullet points, not categorized)

**OUTPUT FORMAT:**
Return ONLY a valid JSON object with a single "summary" key:
{{
  "summary": "Write a clear, natural paragraph summarizing the key characteristics learned about this user from the training mistakes. Write it as if describing the user to someone else - be specific and actionable."
}}

**IMPORTANT:**
- Write a natural, flowing paragraph (not bullet points, not lists)
- Focus on 4-5 most important characteristics
- Be specific and actionable - explain what was learned and why it matters
- Write in plain language as if describing the user
- If no new characteristics can be extracted, return empty string
- Return ONLY JSON, no markdown, no explanations"""

    try:
        response = client.chat.completions.create(
            **build_chat_completion_kwargs(
                model,
                [
                    {
                        "role": "system",
                        "content": "You are an expert at extracting behavioral characteristics from learning patterns. Return only valid JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
                json_mode=True,
            )
        )
        
        result_text = response.choices[0].message.content
        result = json.loads(result_text)
        
        # Validate structure - expect "summary" key
        if 'summary' not in result:
            result['summary'] = ''
        elif not isinstance(result['summary'], str):
            result['summary'] = str(result['summary']) if result['summary'] else ''
        
        return result
        
    except Exception as e:
        logging.error(f"  ❌ Error converting learnings to characteristics: {e}")
        return {
            'summary': ''
        }


def format_user_learnings_for_prompt(user_learnings: dict) -> str:
    """
    Format user learnings into a clean prompt string.
    
    Returns:
        Formatted string for prompt inclusion
    """
    if not user_learnings or user_learnings.get('num_analyses', 0) == 0:
        return ""
    
    learnings = user_learnings.get('learnings', [])
    if not learnings:
        return ""
    
    # Extract just the learning text (not traits/patterns)
    learning_texts = [l.get('learning', '') for l in learnings if l.get('learning')]
    
    if not learning_texts:
        return ""
    
    # Join with separator
    past_learnings = ' | '.join(learning_texts)
    
    return f"""
**Past Learnings (Memory from SGO Training):**
{past_learnings}
"""


def load_seed_characteristics(tribe_seed_path: str, cluster_id: str, micro_id: str) -> dict:
    """Load seed characteristics from tribe_seed file."""
    # Convert cluster_4/micro_0 to segment_4_micro_0 or cluster_4_micro_0
    cluster_num = cluster_id.replace('cluster_', '') if 'cluster_' in cluster_id else cluster_id
    micro_num = micro_id.replace('micro_', '') if 'micro_' in micro_id else micro_id
    tribe_id = f"cluster_{cluster_num}_micro_{micro_num}"
    
    possible_files = [
        Path(tribe_seed_path) / f"{tribe_id}.json",
        Path(tribe_seed_path) / f"segment_{cluster_num}_micro_{micro_num}.json",
    ]
    
    for file_path in possible_files:
        if file_path.exists():
            data = load_json_file(file_path, {})
            if data:
                qualitative_summary = data.get('qualitative_summary', {})
                return {
                    'core_characteristics': qualitative_summary.get('core_characteristics', []),
                    'key_motivations': qualitative_summary.get('key_motivations', []),
                    'common_praises': qualitative_summary.get('common_praises', []),
                    'common_criticisms': qualitative_summary.get('common_criticisms', []),
                    'potential_goals': qualitative_summary.get('potential_goals', [])
                }
    
    return {
        'core_characteristics': [],
        'key_motivations': [],
        'common_praises': [],
        'common_criticisms': [],
        'potential_goals': []
    }


def load_user_characteristics_for_user(user_backstories_path: str, user_id: str, category: str = None) -> dict:
    """Load user characteristics for a specific user."""
    user_backstories_file = Path(user_backstories_path) / "user_overall_characteristics.json"
    if not user_backstories_file.exists():
        return {}
    
    data = load_json_file(user_backstories_file, {})
    if not data or user_id not in data:
        return {}
    
    user_data = data[user_id]
    result = {}
    
    # Overall characteristics
    overall = user_data.get('overall_characteristics', {})
    if overall:
        result['influencing_characteristics_summary'] = overall.get('influencing_characteristics_summary', '')
    
    # Category-specific characteristics
    if category:
        category_chars = user_data.get('category_characteristics', {})
        if category in category_chars:
            cat_char = category_chars[category].get('influencing_characteristics_summary', '')
            if cat_char:
                result['category_influencing_characteristics_summary'] = cat_char
    
    return result


def get_test_users_from_file(test_file_path: str) -> set:
    """Extract all user IDs from test data file."""
    if not test_file_path or not os.path.exists(test_file_path):
        return set()
    
    try:
        data = load_json_file(test_file_path, {})
        if 'user_predictions' not in data:
            return set()
        
        user_ids = set()
        for user_id in data['user_predictions'].keys():
            # Strip _1 suffix if present
            clean_id = user_id[:-2] if user_id.endswith('_1') else user_id
            user_ids.add(clean_id)
        
        return user_ids
    except Exception as e:
        logging.warning(f"  ⚠️  Failed to load test users from {test_file_path}: {e}")
        return set()


def find_user_memory_in_all_micros(memory_dir: str, cluster_id: str, user_id: str) -> dict:
    """Search ALL memory files in cluster for a specific user."""
    cluster_path = Path(memory_dir) / cluster_id
    if not cluster_path.exists():
        return {}
    
    # Search all memory files in the cluster
    all_memory_data = {}
    for memory_file in cluster_path.glob('*_batch_analyses.json'):
        memory_data = load_json_file(str(memory_file), {})
        if not memory_data:
            continue
        
        # Check if user exists in this memory file
        user_found = False
        for batch_key, batch_data in memory_data.items():
            if isinstance(batch_data, dict):
                reviews = batch_data.get('reviews', [])
                for review_key in reviews:
                    if isinstance(review_key, str) and review_key.startswith(f"{user_id}_review_"):
                        user_found = True
                        break
                if user_found:
                    break
        
        if user_found:
            # Merge this memory file's data
            for batch_key, batch_data in memory_data.items():
                if batch_key not in all_memory_data:
                    all_memory_data[batch_key] = batch_data
                else:
                    # Merge reviews and analyses
                    existing_reviews = all_memory_data[batch_key].get('reviews', [])
                    existing_analyses = all_memory_data[batch_key].get('analyses', [])
                    new_reviews = batch_data.get('reviews', [])
                    new_analyses = batch_data.get('analyses', [])
                    all_memory_data[batch_key]['reviews'] = existing_reviews + new_reviews
                    all_memory_data[batch_key]['analyses'] = existing_analyses + new_analyses
    
    return all_memory_data


def process_cluster_micro(
    memory_dir: str, 
    cluster_id: str, 
    micro_id: str, 
    output_dir: Path,
    tribe_seed_path: str = None,
    user_backstories_path: str = None,
    convert_to_chars: bool = False,
    openai_api_key: str = None,
    test_file_path: str = None,
    model_name: str = None
):
    """
    Process memory for a specific cluster/micro and generate user learnings.
    Optionally converts learnings to characteristics using LLM.
    If test_file_path is provided, also processes all users from test data.
    """
    # Get users from test data if provided
    test_user_ids = set()
    if test_file_path:
        test_user_ids = get_test_users_from_file(test_file_path)
        logging.info(f"  Found {len(test_user_ids)} users in test data")
    
    # Load memory from specific micro
    memory_file = Path(memory_dir) / cluster_id / f"{micro_id}_batch_analyses.json"
    memory_data = {}
    if memory_file.exists():
        memory_data = load_json_file(memory_file, {})
        logging.info(f"  Loaded memory from {memory_file}")
    else:
        logging.warning(f"  ⚠️  Memory file not found: {memory_file}")
    
    # Extract all unique user IDs from memory
    memory_user_ids = set()
    for batch_key, batch_data in memory_data.items():
        if isinstance(batch_data, dict):
            reviews = batch_data.get('reviews', [])
            for review_key in reviews:
                if isinstance(review_key, str) and '_review_' in review_key:
                    user_id = review_key.split('_review_')[0]
                    memory_user_ids.add(user_id)
    
    # Combine: users from memory + users from test data
    all_user_ids = memory_user_ids | test_user_ids
    
    logging.info(f"  Processing {cluster_id}/{micro_id}...")
    logging.info(f"  Total users to process: {len(all_user_ids)} ({len(memory_user_ids)} from memory, {len(test_user_ids)} from test)")
    
    # Load seed characteristics if needed
    seed_characteristics = {}
    if convert_to_chars and tribe_seed_path:
        seed_characteristics = load_seed_characteristics(tribe_seed_path, cluster_id, micro_id)
        logging.info(f"  Loaded seed characteristics: {len(seed_characteristics.get('core_characteristics', []))} core traits")
    
    # Initialize client if converting
    client = None
    if convert_to_chars:
        if not openai_api_key:
            openai_api_key = os.getenv('OPENAI_API_KEY')
        if not openai_api_key:
            logging.error("  ❌ OPENAI_API_KEY not set, cannot convert to characteristics")
            convert_to_chars = False
        else:
            client = create_openai_client(api_key=openai_api_key, timeout=120.0)
    
    # Process each user
    user_learnings_dict = {}
    for idx, user_id in enumerate(sorted(all_user_ids), 1):
        logging.info(f"  Processing user {idx}/{len(all_user_ids)}: {user_id}")
        
        # Get memory for this user (from specific micro or search all micros)
        user_memory_data = memory_data
        if user_id not in memory_user_ids:
            # User not in this micro's memory, search all micros in cluster
            logging.info(f"    Searching all micro clusters for user {user_id}...")
            user_memory_data = find_user_memory_in_all_micros(memory_dir, cluster_id, user_id)
            if not user_memory_data:
                logging.warning(f"    ⚠️  No memory found for user {user_id} in any micro cluster")
                # Create empty learnings
                user_learnings = {
                    'user_id': user_id,
                    'num_analyses': 0,
                    'learnings': [],
                    'new_characteristics': {}
                }
                user_learnings_dict[user_id] = user_learnings
                continue
        
        user_learnings = process_user_memory(user_memory_data, user_id)
        
        # Convert to characteristics if requested
        if convert_to_chars and user_learnings.get('num_analyses', 0) > 0:
            # Load user and category characteristics
            user_chars = {}
            category_chars = {}
            if user_backstories_path:
                user_chars = load_user_characteristics_for_user(user_backstories_path, user_id)
                # Note: category would need to be extracted from reviews, but for now use general
                category_chars = {}
            
            # Convert learnings to characteristics
            new_characteristics = convert_learnings_to_characteristics(
                user_learnings,
                seed_characteristics,
                user_chars,
                category_chars,
                client,
                model=model_name
            )
            user_learnings['new_characteristics'] = new_characteristics
            
            # Log summary
            summary = new_characteristics.get('summary', '')
            if summary:
                summary_length = len(summary.split())
                logging.info(f"    ✅ Extracted summary ({summary_length} words)")
            else:
                logging.warning(f"    ⚠️  No summary extracted for user {user_id}")
        elif convert_to_chars:
            logging.warning(f"    ⚠️  No analyses found for user {user_id}, cannot generate summary")
            user_learnings['new_characteristics'] = {}
        
        user_learnings_dict[user_id] = user_learnings
        
        # Rate limiting
        if convert_to_chars:
            time.sleep(0.5)  # Small delay to avoid rate limits
    
    # Save user learnings - one file per user in tribe_user_learnings directory
    tribe_user_learnings_dir = output_dir / "tribe_user_learnings" / cluster_id / micro_id
    tribe_user_learnings_dir.mkdir(parents=True, exist_ok=True)
    
    # Save each user's learnings separately - ONLY SUMMARY, NO LEARNINGS ARRAY
    for user_id, user_learnings in user_learnings_dict.items():
        user_file = tribe_user_learnings_dir / f"{user_id}_user_learnings.json"
        
        # Only save summary, not the detailed learnings array
        new_characteristics = user_learnings.get('new_characteristics', {})
        user_data = {
            'user_id': user_id,
            'cluster_id': cluster_id,
            'micro_id': micro_id,
            'num_analyses': user_learnings.get('num_analyses', 0),
            'new_characteristics': new_characteristics  # Only summary, no learnings array
        }
        
        with open(user_file, 'w', encoding='utf-8') as f:
            json.dump(user_data, f, indent=2, ensure_ascii=False)
        
        summary = new_characteristics.get('summary', '')
        if summary:
            logging.info(f"  ✅ Saved summary for user {user_id} ({len(summary)} chars)")
        else:
            logging.warning(f"  ⚠️  Saved empty summary for user {user_id}")
    
    # Also save a summary file with all users
    summary_file = output_dir / cluster_id / f"{micro_id}_user_learnings.json"
    summary_file.parent.mkdir(parents=True, exist_ok=True)
    
    summary_data = {
        'cluster_id': cluster_id,
        'micro_id': micro_id,
        'total_users': len(user_learnings_dict),
        'user_learnings': user_learnings_dict
    }
    
    with open(summary_file, 'w', encoding='utf-8') as f:
        json.dump(summary_data, f, indent=2, ensure_ascii=False)
    
    logging.info(f"  ✅ Saved summary to: {summary_file}")
    
    return user_learnings_dict


def main():
    parser = argparse.ArgumentParser(description='Convert memory analyses to user learnings')
    parser.add_argument('--cluster', type=str, help='Process specific cluster (e.g., cluster_4)')
    parser.add_argument('--micro', type=str, help='Process specific micro cluster (e.g., micro_0)')
    parser.add_argument('--all-clusters', action='store_true', help='Process all clusters')
    parser.add_argument('--memory-dir', type=str, 
                       default='07_sgo_training/artifacts/sgo_training_results_v6/_memory',
                       help='Base directory for memory files')
    parser.add_argument('--output-dir', type=str,
                       default='07_sgo_training/artifacts/user_learnings',
                       help='Output directory for user learnings')
    parser.add_argument('--convert-to-chars', action='store_true',
                       help='Use LLM to convert learnings to new characteristics')
    parser.add_argument('--tribe-seed-path', type=str,
                       help='Path to tribe_seed directory (required if --convert-to-chars)')
    parser.add_argument('--user-backstories-path', type=str,
                       help='Path to user_backstories directory (optional, for --convert-to-chars)')
    parser.add_argument('--openai-api-key', type=str,
                       help='OpenAI API key (or set OPENAI_API_KEY env var)')
    parser.add_argument('--test-file', type=str,
                       help='Path to test data file to extract all users from (e.g., micro_1_test_summary_enhanced_persona_micro_cluster_accuracy.json)')
    
    args = parser.parse_args()
    
    memory_dir = Path(project_root) / args.memory_dir
    output_dir = Path(project_root) / args.output_dir

    load_config()
    stage_config = get_stage_config("07_sgo_training")
    hyperparams = stage_config.get("hyperparameters", {}) if stage_config else {}
    model_name = hyperparams.get("analysis_model", "gpt-5-mini")
    base_url = os.environ.get("OPENAI_BASE_URL", "").strip()
    if base_url and "bedrock-mantle" in base_url and stage_config.get("bedrock_model_id"):
        model_name = stage_config["bedrock_model_id"]
    
    if not memory_dir.exists():
        logging.error(f"❌ Memory directory not found: {memory_dir}")
        return
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Validate convert-to-chars requirements
    if args.convert_to_chars:
        if not args.tribe_seed_path:
            logging.error("❌ --tribe-seed-path required when using --convert-to-chars")
            return
        tribe_seed_path = Path(project_root) / args.tribe_seed_path
        if not tribe_seed_path.exists():
            logging.error(f"❌ Tribe seed path not found: {tribe_seed_path}")
            return
    else:
        tribe_seed_path = None
    
    user_backstories_path = None
    if args.user_backstories_path:
        user_backstories_path = Path(project_root) / args.user_backstories_path
        if not user_backstories_path.exists():
            logging.warning(f"⚠️  User backstories path not found: {user_backstories_path}")
            user_backstories_path = None
    
    # Resolve test file path if provided
    test_file_path = None
    if args.test_file:
        test_file_path = Path(project_root) / args.test_file
        if not test_file_path.exists():
            logging.warning(f"⚠️  Test file not found: {test_file_path}")
            test_file_path = None
    
    if args.cluster and args.micro:
        # Process specific cluster/micro
        process_cluster_micro(
            str(memory_dir), args.cluster, args.micro, output_dir,
            tribe_seed_path=str(tribe_seed_path) if tribe_seed_path else None,
            user_backstories_path=str(user_backstories_path) if user_backstories_path else None,
            convert_to_chars=args.convert_to_chars,
            openai_api_key=args.openai_api_key,
            test_file_path=str(test_file_path) if test_file_path else None,
            model_name=model_name
        )
    elif args.all_clusters:
        # Process all clusters
        logging.info("Processing all clusters...")
        for cluster_dir in sorted(memory_dir.iterdir()):
            if not cluster_dir.is_dir() or not cluster_dir.name.startswith('cluster_'):
                continue
            
            cluster_id = cluster_dir.name
            for memory_file in sorted(cluster_dir.glob('*_batch_analyses.json')):
                micro_id = memory_file.stem.replace('_batch_analyses', '')
                process_cluster_micro(
                    str(memory_dir), cluster_id, micro_id, output_dir,
                    tribe_seed_path=str(tribe_seed_path) if tribe_seed_path else None,
                    user_backstories_path=str(user_backstories_path) if user_backstories_path else None,
                    convert_to_chars=args.convert_to_chars,
                    openai_api_key=args.openai_api_key,
                    test_file_path=str(test_file_path) if test_file_path else None,
                    model_name=model_name
                )
    else:
        logging.error("❌ Must specify --cluster and --micro, or --all-clusters")
        return
    
    logging.info("✅ User learnings conversion completed!")


if __name__ == "__main__":
    main()

