import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Repo prompt assets live under ``scripts/scripts_sgo/prompts`` (config may point elsewhere).
_SCRIPTS_SGO_PROMPTS = Path(__file__).resolve().parent.parent / "prompts"

# Set up package structure for sgo_training imports
scripts_dir = Path(__file__).parent.parent
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))
import _package_setup

from sgo_training.config import settings
from sgo_training.utils import io_utils

from linkedin.i0_linkedin_context import format_qualitative_summary_descriptions_only


def _load_prompt_part_a_or_fallback(prompt_name: str) -> str:
    """Prefer ``settings.PROMPT_DIR_PART_A``, then ``scripts/scripts_sgo/prompts``."""
    primary = Path(settings.PROMPT_DIR_PART_A)
    txt = io_utils.load_prompt(prompt_name, primary)
    if txt:
        return txt
    txt = io_utils.load_prompt(prompt_name, _SCRIPTS_SGO_PROMPTS)
    if txt:
        return txt
    raise FileNotFoundError(
        f"CRITICAL: '{prompt_name}.txt' not found in {primary} or fallback {_SCRIPTS_SGO_PROMPTS}. "
        "Aborting execution to prevent malformed prompt generation."
    )


# --- Prompt Generation Functions ---

def generate_part_a_batch_prompt(
    micro_persona_characteristics, 
    user_characteristics_list, 
    persona_name, 
    batch_data_list, 
    persona_memory, 
    relevant_themes, 
    batch_motives=None,
    user_chars_data=None,  # For per-review user characteristics
    category_mapping=None,  # For category-specific characteristics
    scoring_mode="confidence"  # For logprobs mode, themes section will be removed
):
    """
    Generates a prompt for Part A: Correction, for a whole batch.
    Strict Mode: Raises FileNotFoundError if prompt template is missing.
    """
    # 1. Load Template (Strict) - use logprobs version if in logprobs mode
    if scoring_mode in ["logprobs", "logprobs-without-persona-context"]:
        template_name = "part_a_batch_prompt_logprobs"
    else:
        template_name = "part_a_batch_prompt"
    
    template = _load_prompt_part_a_or_fallback(template_name)

    # 2. Build Context String
    profile_parts = []
    
    # Micro Persona Characteristics
    if micro_persona_characteristics:
        persona_summary = micro_persona_characteristics.get('persona_summary', '')
        if persona_summary:
            profile_parts.append(f"- **Micro Persona Characteristics:** {persona_summary}")
    
    # User Characteristics - Only add if provided (legacy support)
    # Note: Per-review user characteristics will be added in reviews section
    if user_characteristics_list:
        if isinstance(user_characteristics_list, list):
            user_chars_text = ' '.join(user_characteristics_list)
        else:
            user_chars_text = str(user_characteristics_list)
        profile_parts.append(f"- **User Characteristics (Aggregated - Legacy):** {user_chars_text}")
    
    if not profile_parts:
        # We allow this, but log it via context string. 
        # If strict data validation is needed, raise ValueError here.
        profile_parts.append("- **Context:** No profile or characteristics provided.")
        
    context_str = "\n".join(profile_parts)

    # 3. Format Reviews
    if not batch_data_list:
        raise ValueError("CRITICAL: batch_data_list is empty. Cannot generate batch prompt.")

    reviews_to_process_str = ""
    # Check if batch_data_list contains full review info (with user_id) or just data
    # If it's full review info, extract user characteristics per review
    is_full_review_info = isinstance(batch_data_list[0], dict) and 'user_id' in batch_data_list[0] if batch_data_list else False
    
    # Cache user characteristics per (user_id, category) to avoid recomputation
    # Key: (user_id, main_category), Value: formatted characteristics string
    user_chars_cache = {}
    
    def get_user_characteristics_cached(user_id, review_category):
        """Get user characteristics with caching to avoid recomputation for same user+category."""
        if user_id == 'unknown' or not user_chars_data or user_id not in user_chars_data:
            return 'None'
        
        # Map to main category for cache key
        if review_category and category_mapping:
            if isinstance(category_mapping, dict) and 'category_to_main_mapping' in category_mapping:
                main_category = category_mapping['category_to_main_mapping'].get(review_category, review_category)
            else:
                main_category = category_mapping.get(review_category, review_category)
        else:
            main_category = review_category
        
        cache_key = (user_id, main_category)
        if cache_key in user_chars_cache:
            return user_chars_cache[cache_key]
        
        # Compute user characteristics
        user_data = user_chars_data[user_id]
        characteristics = []
        
        # General characteristics
        llm_chars = user_data.get('llm_characteristics', {})
        if isinstance(llm_chars, dict):
            user_char = llm_chars.get('influencing_characteristics_summary', '')
            if user_char:
                characteristics.append(f"[General Characteristics] {user_char}")
        
        # Category-specific characteristics
        if main_category:
            category_chars = user_data.get('category_characteristics', {})
            if isinstance(category_chars, dict) and main_category in category_chars:
                cat_char = category_chars[main_category].get('influencing_characteristics_summary', '')
                if cat_char:
                    characteristics.append(f"[{main_category} Specific] {cat_char}")
        
        user_chars_text = ' '.join(characteristics) if characteristics else 'None'
        user_chars_cache[cache_key] = user_chars_text
        return user_chars_text
    
    for i, item in enumerate(batch_data_list):
        # Handle both formats: full review info or just data
        if is_full_review_info:
            # Full review info format: {user_id, review_idx, data, ...}
            user_id = item.get('user_id', 'unknown')
            data = item.get('data', item)  # Fallback to item itself if 'data' key doesn't exist
            review_category = data.get('category', 'unknown') if isinstance(data, dict) else 'unknown'
        else:
            # Legacy format: just data dict
            data = item
            user_id = 'unknown'
            review_category = data.get('category', 'unknown') if isinstance(data, dict) else 'unknown'
        
        # Get user characteristics for this specific review (with caching)
        user_chars_text = get_user_characteristics_cached(user_id, review_category)
        
        # Get the original failed prediction
        original_pred = data.get('prediction', {}) if isinstance(data, dict) else {}
        original_themes = original_pred.get('predicted_themes', {})
        
        rt_preview = (original_pred.get('review_text') or '').strip()
        if len(rt_preview) > 400:
            rt_preview = rt_preview[:400] + '…'
        original_pred_str = f"review_text preview: {rt_preview or 'N/A'}"
        if original_themes:
            top_3_themes = sorted(original_themes.items(), key=lambda x: x[1], reverse=True)[:3]
            themes_str = ', '.join([f"{theme} ({score:.2f})" for theme, score in top_3_themes])
            original_pred_str += f" | Top 3 Themes: {themes_str}"
        
        reviews_to_process_str += (
            f"\n--- Review Index {i} ---\n"
            f"User Characteristics: {user_chars_text}\n"
            f"Product Description: \"{data.get('product_description', 'N/A') if isinstance(data, dict) else 'N/A'}\"\n"
            f"Original (Failed) Prediction: {original_pred_str}\n"
        )

    # 4. Format Motives (Analysis from Part B)
    motives_section = ""
    if batch_motives:
        motives_list = []
        for analysis in batch_motives:
            # Filter out error messages or empty analyses
            if "LLM Error" not in analysis and "No missed motives" not in analysis:
                motives_list.append(analysis)
        
        if motives_list:
            motives_section = f"\n**Recent Failure Analyses (Motives & Explanations):**\n" + "\n".join([f"- {m}" for m in motives_list])

    # 5. Format Memory (Past Learnings)
    persona_data = persona_memory.get(persona_name, {})
    if isinstance(persona_data, list):
        enrichment_log = persona_data
    else:
        enrichment_log = persona_data.get('batch_analyses', [])
        
    past_learnings = ' | '.join([str(item) for item in enrichment_log]) if enrichment_log else 'None - this is the first iteration'
    
    # 6. Format Relevant Themes (skip for logprobs mode - themes will be classified separately)
    if scoring_mode in ["logprobs", "logprobs-without-persona-context"]:
        relevant_themes_str = "N/A - Themes will be classified separately using logprobs"
    else:
        relevant_themes_str = ', '.join(relevant_themes) if relevant_themes else 'None'
    
    # 7. Fill Template
    return template.format(
        context_str=context_str,
        past_learnings=past_learnings,
        motives_section=motives_section,
        relevant_themes=relevant_themes_str,
        reviews_to_process_str=reviews_to_process_str.strip()
    )

def generate_part_a_individual_prompt_logprobs(
    micro_persona_characteristics,
    user_characteristics_text,
    persona_name,
    review_data,
    persona_memory,
    batch_motives=None,
    category_mapping=None
):
    """
    Generates an individual prompt for Part A: Correction in logprobs mode (single review, not batch).
    This is used when scoring_mode is "logprobs" or "logprobs-without-persona-context".
    
    Args:
        micro_persona_characteristics: Persona profile dict
        user_characteristics_text: User characteristics string for this specific review
        persona_name: Name of the persona
        review_data: Single review data dict (with 'prediction', 'category', 'product_description', etc.)
        persona_memory: Persona memory dict
        batch_motives: List of batch analyses (optional)
        category_mapping: Category mapping dict (optional)
    
    Returns:
        Formatted prompt string for a single review
    """
    # 1. Load Template (Strict)
    # LinkedIn-flavored prompt: audience-room persona + user characteristics + memory.
    template = _load_prompt_part_a_or_fallback("part_a_individual_prompt_logprobs_linkedin")

    # 2. Format persona profile fields
    persona_summary = ""
    key_motivations = ""
    common_praises = ""
    common_criticisms = ""
    core_characteristics = ""
    potential_goals = ""
    if isinstance(micro_persona_characteristics, dict):
        persona_summary = str(micro_persona_characteristics.get("persona_summary") or "").strip()
        key_motivations = str(micro_persona_characteristics.get("key_motivations") or "").strip()
        common_praises = str(micro_persona_characteristics.get("common_praises") or "").strip()
        common_criticisms = str(micro_persona_characteristics.get("common_criticisms") or "").strip()
        core_characteristics = str(micro_persona_characteristics.get("core_characteristics") or "").strip()
        potential_goals = str(micro_persona_characteristics.get("potential_goals") or "").strip()

    # 3. Format Past Learnings (full memory)
    persona_data = persona_memory.get(persona_name, {})
    if isinstance(persona_data, list):
        enrichment_log = persona_data
    else:
        enrichment_log = persona_data.get('batch_analyses', [])
    past_learnings = ' | '.join([str(item) for item in enrichment_log]) if enrichment_log else 'None - this is the first iteration'
    
    # 4. Format Motives (recent root-cause analyses from Part B)
    motives_section = "None"
    if batch_motives:
        motives_list = []
        for analysis in batch_motives:
            if "LLM Error" not in analysis and "No missed motives" not in analysis:
                motives_list.append(analysis)
        
        if motives_list:
            motives_section = "\n".join([f"- {m}" for m in motives_list])
    
    # 5. Format Original Prediction (reference only)
    original_pred = review_data.get('prediction', {}) if isinstance(review_data, dict) else {}
    original_themes = original_pred.get('predicted_themes', {})

    rt_preview = (original_pred.get('review_text') or '').strip()
    if len(rt_preview) > 400:
        rt_preview = rt_preview[:400] + '…'
    original_pred_str = f"review_text preview: {rt_preview or 'N/A'}"
    if original_themes:
        top_3_themes = sorted(original_themes.items(), key=lambda x: x[1], reverse=True)[:3]
        themes_str = ', '.join([f"{theme} ({score:.2f})" for theme, score in top_3_themes])
        original_pred_str += f" | Top 3 Themes: {themes_str}"
    
    # 6. Get Stimulus (kept as product_description field for pipeline compatibility)
    product_description = review_data.get('product_description', 'N/A') if isinstance(review_data, dict) else 'N/A'
    
    # 7. Category + category-specific user section (optional)
    category = review_data.get("category", "") if isinstance(review_data, dict) else ""
    category_section = ""
    if category:
        category_section = f"\n[Category] {category}"

    # 8. Fill Template
    return template.format(
        persona_name=persona_name or "unknown_persona",
        persona_summary=persona_summary or "None",
        key_motivations=key_motivations or "None",
        common_praises=common_praises or "None",
        common_criticisms=common_criticisms or "None",
        core_characteristics=core_characteristics or "None",
        potential_goals=potential_goals or "None",
        user_char_summary=user_characteristics_text or "None",
        category_section=category_section,
        memory_section=past_learnings,
        category=category or "unknown",
        product_description=product_description,
        original_prediction=original_pred_str,
        motive_analyses=motives_section,
    )


# Template `part_a_individual_prompt_logprobs.txt` — placeholders:
# group_summary, group_traits, past_learnings, motives_section, posts_to_process_str
_PART_A_LINKEDIN_GROUP_LOGPROBS = "part_a_individual_prompt_logprobs"
_LINKEDIN_TIER1_POSTS_SECTION_MAX_CHARS = 16000


def _normalize_confidence_label(raw: Any) -> str:
    s = str(raw or "").strip().upper()
    if s.startswith("HIGH"):
        return "HIGH"
    if s.startswith("MED"):
        return "MEDIUM"
    if s.startswith("LOW"):
        return "LOW"
    return s or "LOW"


def _normalize_bool_field(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    s = str(raw or "").strip().lower()
    return s in ("true", "yes", "1")


_VALID_BATCH_VERDICTS = frozenset(
    {"new_gaps_found", "no_new_gaps", "prior_gaps_confirmed"}
)


def _normalize_batch_verdict(raw: Any) -> Optional[str]:
    """Normalize Part B ``batch_verdict`` (``part_b_memory_analysis.txt``)."""
    s = str(raw or "").strip().lower().replace(" ", "_").replace("-", "_")
    if s in _VALID_BATCH_VERDICTS:
        return s
    if "new" in s and "gap" in s:
        return "new_gaps_found"
    if "no" in s and "gap" in s:
        return "no_new_gaps"
    if "confirm" in s or "prior" in s:
        return "prior_gaps_confirmed"
    return None


def _normalize_prior_gap_confirmations(raw: Any) -> List[str]:
    """Normalize Part B ``prior_gap_confirmations[]`` strings."""
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    seen: Set[str] = set()
    for row in raw:
        t = str(row or "").strip()
        if len(t) < 8:
            continue
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def _normalize_memory_outlier_row(row: Any) -> Optional[Dict[str, Any]]:
    """Normalize one Part B ``outliers[]`` object (per-post divergences outside main batch gap)."""
    if not isinstance(row, dict):
        return None
    gap = str(row.get("behavioral_gap") or row.get("behavior") or "").strip()
    if len(gap) < 8:
        return None
    post_id = str(row.get("post_id") or "").strip()
    why_raw = row.get("why_outlier")
    why_outlier: Optional[str]
    if why_raw is None or str(why_raw).strip().lower() in ("", "null", "none"):
        why_outlier = None
    else:
        why_outlier = str(why_raw).strip()
    nuance_raw = row.get("missing_nuance")
    missing_nuance: Optional[str]
    if nuance_raw is None or str(nuance_raw).strip().lower() in ("", "null", "none"):
        missing_nuance = None
    else:
        missing_nuance = str(nuance_raw).strip()
    return {
        "post_id": post_id,
        "behavioral_gap": gap,
        "why_outlier": why_outlier,
        "exists_in_context": _normalize_bool_field(row.get("exists_in_context")),
        "missing_nuance": missing_nuance,
    }


def _normalize_memory_gap_row(row: Any) -> Optional[Dict[str, Any]]:
    """Normalize one Part B ``gaps[]`` object from ``part_b_memory_analysis.txt``."""
    if not isinstance(row, dict):
        return None
    gap = str(row.get("behavioral_gap") or row.get("behavior") or "").strip()
    if len(gap) < 8:
        return None
    nuance_raw = row.get("missing_nuance")
    missing_nuance: Optional[str]
    if nuance_raw is None or str(nuance_raw).strip().lower() in ("", "null", "none"):
        missing_nuance = None
    else:
        missing_nuance = str(nuance_raw).strip()
    return {
        "behavioral_gap": gap,
        "exists_in_context": _normalize_bool_field(row.get("exists_in_context")),
        "missing_nuance": missing_nuance,
        "evidence": str(row.get("evidence") or "").strip(),
        "confidence": _normalize_confidence_label(row.get("confidence")),
    }


def _legacy_patterns_to_gaps(
    data: Dict[str, Any],
    *,
    batch_size: int,
) -> List[Dict[str, Any]]:
    """Convert older memory JSON (patterns/outliers/analyses) into ``gaps`` rows."""
    out: List[Dict[str, Any]] = []
    for row in data.get("patterns") or []:
        if not isinstance(row, dict):
            continue
        beh = str(row.get("behavior") or "").strip()
        if len(beh) < 8:
            continue
        why = row.get("why")
        nuance = None
        if why is not None and str(why).strip().lower() not in ("", "null", "none"):
            nuance = str(why).strip()
        out.append(
            {
                "behavioral_gap": beh,
                "exists_in_context": False,
                "missing_nuance": nuance,
                "evidence": str(row.get("evidence") or "").strip(),
                "confidence": _normalize_confidence_label(row.get("confidence")),
            }
        )
    legacy = data.get("analyses")
    if not out and isinstance(legacy, list):
        merged = "\n\n".join(str(x).strip() for x in legacy if str(x).strip())
        if len(merged) >= 12:
            out.append(
                {
                    "behavioral_gap": merged[:4000],
                    "exists_in_context": False,
                    "missing_nuance": None,
                    "evidence": f"{len(legacy)}/{max(1, batch_size)} posts",
                    "confidence": "LOW",
                }
            )
    return out


def _legacy_outliers_list(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Convert legacy ``outliers`` (behavior-only rows) into structured outlier objects."""
    out: List[Dict[str, Any]] = []
    for row in data.get("outliers") or []:
        norm = _normalize_memory_outlier_row(row)
        if norm:
            out.append(norm)
    return out


def parse_linkedin_memory_batch_gaps_response(
    data: Any,
    *,
    batch_size: int,
    batch_key: str = "",
    fallback_preliminary_notes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Parse Part B ``part_b_memory_analysis.txt`` JSON (``batch_id``, ``gaps[]``, ``outliers[]``,
    ``batch_verdict``, ``prior_gap_confirmations[]``).
    On failure, returns empty lists and preserves preliminary per-post notes.
    Empty ``gaps`` with ``batch_verdict=no_new_gaps`` is a valid success (no fb synthesis).
    """
    fb = list(fallback_preliminary_notes or [])
    empty: Dict[str, Any] = {
        "batch_id": str(batch_key or "").strip(),
        "gaps": [],
        "outliers": [],
        "batch_verdict": None,
        "prior_gap_confirmations": [],
        "preliminary_delta_notes": fb[: max(0, int(batch_size))],
        "parse_ok": False,
    }
    if not isinstance(data, dict):
        return empty

    batch_id = str(data.get("batch_id") or batch_key or "").strip()
    gaps: List[Dict[str, Any]] = []
    for row in data.get("gaps") or []:
        norm = _normalize_memory_gap_row(row)
        if norm:
            gaps.append(norm)

    outliers: List[Dict[str, Any]] = []
    for row in data.get("outliers") or []:
        norm = _normalize_memory_outlier_row(row)
        if norm:
            outliers.append(norm)

    if not gaps:
        gaps = _legacy_patterns_to_gaps(data, batch_size=batch_size)
    if not outliers:
        outliers = _legacy_outliers_list(data)

    batch_verdict = _normalize_batch_verdict(data.get("batch_verdict"))
    prior_gap_confirmations = _normalize_prior_gap_confirmations(
        data.get("prior_gap_confirmations")
    )

    if not gaps and fb and not batch_verdict:
        merged_fb = "\n\n---\n\n".join(str(x).strip() for x in fb if str(x).strip())
        if merged_fb and "[queued_for_batch_root_cause_memory]" not in merged_fb:
            gaps.append(
                {
                    "behavioral_gap": merged_fb[:4000],
                    "exists_in_context": False,
                    "missing_nuance": None,
                    "evidence": f"{len([x for x in fb if str(x).strip()])}/{max(1, batch_size)} posts",
                    "confidence": "LOW",
                }
            )

    parse_ok = bool(
        batch_verdict or gaps or outliers or prior_gap_confirmations
    )

    return {
        "batch_id": batch_id,
        "gaps": gaps,
        "outliers": outliers,
        "batch_verdict": batch_verdict,
        "prior_gap_confirmations": prior_gap_confirmations,
        "preliminary_delta_notes": fb[: max(0, int(batch_size))],
        "parse_ok": parse_ok,
    }


# Backward-compatible alias
parse_linkedin_memory_batch_patterns_response = parse_linkedin_memory_batch_gaps_response

_MEM_KEY_NEW = re.compile(r"^iteration_(\d+)_batch_(\d+)$")
_MEM_KEY_LEGACY = re.compile(r"^i(\d+)_batch_(\d+)$")


def memory_batch_iteration_number(batch_key: str) -> Optional[int]:
    """Parse outer iteration from ``iteration_N_batch_M`` or legacy ``iN_batch_M`` keys."""
    m = _MEM_KEY_NEW.match(str(batch_key or "").strip())
    if m:
        return int(m.group(1))
    m = _MEM_KEY_LEGACY.match(str(batch_key or "").strip())
    if m:
        return int(m.group(1))
    return None


def format_memory_batch_entry_lines(
    batch_key: str,
    data: Dict[str, Any],
    *,
    max_chars_per_line: int = 1200,
    include_gaps: bool = True,
    include_outliers: bool = True,
    include_gap_metadata: bool = True,
) -> List[str]:
    """Human-readable lines for one ``memory/batch_analyses.json`` batch entry."""
    cap = max(400, int(max_chars_per_line))
    lines: List[str] = [f"--- {batch_key} ---"]
    cat = (data.get("category") or "").strip()
    if cat:
        lines.append(f"category: {cat}")
    revs = data.get("reviews")
    if revs is None:
        revs = data.get("post_ids") or []
    if revs:
        lines.append("reviews: " + ", ".join(str(x) for x in revs))

    bid = str(data.get("batch_id") or batch_key or "").strip()
    if bid:
        lines.append(f"batch_id: {bid}")

    verdict = _normalize_batch_verdict(data.get("batch_verdict"))
    if verdict:
        lines.append(f"batch_verdict: {verdict}")
    confirmations = data.get("prior_gap_confirmations") or []
    if isinstance(confirmations, list) and confirmations:
        lines.append("prior_gap_confirmations:")
        for c in confirmations:
            t = str(c or "").strip()
            if t:
                lines.append(f"- {t}")

    gaps = data.get("gaps") or []
    if not gaps and (data.get("patterns") or data.get("outliers") or data.get("analyses")):
        gaps = _legacy_patterns_to_gaps(data, batch_size=max(1, len(revs) if isinstance(revs, list) else 1))

    if include_gaps and gaps:
        lines.append("gaps:")
        for g in gaps:
            if not isinstance(g, dict):
                continue
            conf = _normalize_confidence_label(g.get("confidence"))
            gap_txt = str(g.get("behavioral_gap") or g.get("behavior") or "").strip()
            if len(gap_txt) > cap:
                gap_txt = gap_txt[: cap - 3].rstrip() + "..."
            ev = str(g.get("evidence") or "").strip()
            ev_s = f" ({ev})" if ev else ""
            meta_s = ""
            if include_gap_metadata:
                in_ctx = (
                    "in_context"
                    if _normalize_bool_field(g.get("exists_in_context"))
                    else "not_in_context"
                )
                nuance = g.get("missing_nuance")
                nuance_s = ""
                if nuance is not None and str(nuance).strip().lower() not in (
                    "",
                    "null",
                    "none",
                ):
                    nuance_s = f" | missing_nuance: {str(nuance).strip()}"
                meta_s = f" [{in_ctx}]{nuance_s}"
            lines.append(f"- [{conf}]{ev_s}{meta_s} {gap_txt}")

    outliers = data.get("outliers") or []
    if not outliers and (data.get("patterns") or data.get("analyses")):
        outliers = _legacy_outliers_list(data)
    if include_outliers and outliers:
        lines.append("outliers:")
        for o in outliers:
            if not isinstance(o, dict):
                continue
            pid = str(o.get("post_id") or "").strip()
            pid_s = f"post_id={pid} | " if pid else ""
            gap_txt = str(o.get("behavioral_gap") or "").strip()
            if len(gap_txt) > cap:
                gap_txt = gap_txt[: cap - 3].rstrip() + "..."
            why = o.get("why_outlier")
            why_s = ""
            if why is not None and str(why).strip().lower() not in ("", "null", "none"):
                why_s = f" | why_outlier: {str(why).strip()}"
            meta_s = ""
            if include_gap_metadata:
                in_ctx = (
                    "in_context"
                    if _normalize_bool_field(o.get("exists_in_context"))
                    else "not_in_context"
                )
                nuance = o.get("missing_nuance")
                nuance_s = ""
                if nuance is not None and str(nuance).strip().lower() not in (
                    "",
                    "null",
                    "none",
                ):
                    nuance_s = f" | missing_nuance: {str(nuance).strip()}"
                meta_s = f" [{in_ctx}]{nuance_s}"
            lines.append(f"- {pid_s}{meta_s} {gap_txt}{why_s}")

    if not (include_gaps and gaps) and not (include_outliers and outliers):
        for a in data.get("analyses") or []:
            s = str(a).strip()
            if not s:
                continue
            if len(s) > cap:
                s = s[: cap - 3].rstrip() + "..."
            lines.append(f"- {s}")

    if (
        not (include_gaps and gaps)
        and not (include_outliers and outliers)
        and not (data.get("analyses") or [])
    ):
        prelim = data.get("preliminary_delta_notes") or []
        if prelim:
            lines.append("preliminary_delta_notes:")
            for note in prelim:
                s = str(note).strip()
                if not s:
                    continue
                if len(s) > cap:
                    s = s[: cap - 3].rstrip() + "..."
                lines.append(f"- {s}")

    return lines


def _sort_memory_batch_keys(keys: List[str]) -> List[str]:
    def sort_key(k: str) -> Tuple[int, int, int, str]:
        m = _MEM_KEY_NEW.match(k)
        if m:
            return (0, int(m.group(1)), int(m.group(2)), k)
        m = _MEM_KEY_LEGACY.match(k)
        if m:
            return (1, int(m.group(1)), int(m.group(2)), k)
        return (2, 99999, 99999, k)

    return sorted(keys, key=sort_key)


def count_outliers_for_iteration(memory: Dict[str, Any], iteration: int) -> int:
    """Count structured outlier rows across memory batches for one outer iteration."""
    n = 0
    it = int(iteration)
    for batch_key, data in (memory or {}).items():
        if memory_batch_iteration_number(str(batch_key)) != it:
            continue
        if not isinstance(data, dict):
            continue
        for row in data.get("outliers") or []:
            if isinstance(row, dict) and str(row.get("behavioral_gap") or "").strip():
                n += 1
    return n


def memory_batches_outliers_only_for_iteration(
    memory: Dict[str, Any], iteration: int
) -> Dict[str, Dict[str, Any]]:
    """Subset of memory entries that have outliers for the given outer iteration."""
    out: Dict[str, Dict[str, Any]] = {}
    it = int(iteration)
    for batch_key in _sort_memory_batch_keys(list((memory or {}).keys())):
        if memory_batch_iteration_number(str(batch_key)) != it:
            continue
        data = memory.get(batch_key)
        if not isinstance(data, dict):
            continue
        outliers = data.get("outliers") or []
        if not outliers:
            continue
        slim = dict(data)
        slim["gaps"] = []
        slim["outliers"] = outliers
        out[str(batch_key)] = slim
    return out


def format_batch_analyses_memory_for_part_a(memory: Dict[str, Any], max_chars: int = 14000) -> str:
    """
    Serialize persisted ``memory/batch_analyses.json`` for Part A ``past_learnings``
    (LinkedIn tier1 delta-method + feedback loop).
    """
    if not memory:
        return (
            "None — no persisted batch analyses yet "
            "(early iterations or empty memory/batch_analyses.json)."
        )

    lines: List[str] = []
    for batch_key in _sort_memory_batch_keys(list(memory.keys())):
        data = memory.get(batch_key)
        if not isinstance(data, dict):
            continue
        lines.extend(format_memory_batch_entry_lines(str(batch_key), data))
    blob = "\n".join(lines) if lines else "(no memory batches yet)"
    if len(blob) > max_chars:
        return blob[: max_chars - 40].rstrip() + "\n... [truncated]"
    return blob


def _topic_probability_lines(theme_probs: Dict[str, float], *, limit: int = 12) -> str:
    if not theme_probs:
        return "(none)"
    items = sorted(theme_probs.items(), key=lambda x: float(x[1]), reverse=True)[:limit]
    return "\n".join(f"- {k}: {float(v):.4f}" for k, v in items)


def generate_part_a_linkedin_tier1_correction_prompt(
    *,
    group_summary: str,
    qualitative_summary: Dict[str, Any],
    individual_ctx: str,
    category: str,
    stimulus: str,
    prior_text: str,
    prior_topics: Dict[str, float],
    gt_probs: Dict[str, float],
    topics_ordered: List[str],
    analysis_used: str,
    past_learnings: str,
    max_group_chars: int,
    max_qual_json_chars: int,
    max_individual_chars: int,
) -> str:
    """
    Fill ``prompts/part_a_individual_prompt_logprobs.txt`` for LinkedIn tier1 delta-method
    correction (single post). Used by ``linkedin/tier1_delta_method_predictions.regenerate_post``.
    """
    tpl = _load_prompt_part_a_or_fallback(_PART_A_LINKEDIN_GROUP_LOGPROBS)

    gs = (group_summary or "").strip()
    if len(gs) > max_group_chars:
        gs = gs[:max_group_chars] + "\n... [truncated]"

    gt_json = format_qualitative_summary_descriptions_only(qualitative_summary or {})
    if len(gt_json) > max_qual_json_chars:
        gt_json = gt_json[:max_qual_json_chars] + "\n... [truncated]"

    ind = (individual_ctx or "").strip()
    if len(ind) > max_individual_chars:
        ind = ind[:max_individual_chars] + "\n... [truncated]"

    pt = (prior_text or "").strip()
    if len(pt) > 8000:
        pt = pt[:8000] + "\n... [truncated]"

    topic_lines = "\n".join(f"- {t}" for t in topics_ordered) if topics_ordered else "- (none)"

    posts_block = (
        "### Post Index 0 (single correction target)\n\n"
        f"**Category:** {category}\n"
        f"**Stimulus:** {stimulus}\n\n"
        "**Topics in evaluation order (this category):**\n"
        f"{topic_lines}\n\n"
        "**Ground-truth topic probabilities (reference — do not paste into the post):**\n"
        f"{_topic_probability_lines(gt_probs)}\n\n"
        "**Individual profile (this author):**\n"
        f"{ind}\n\n"
        "**Original (failed) synthetic post — post_text:**\n"
        f'"""\n{pt}\n"""\n\n'
        "**Original predicted topic weights before this correction step:**\n"
        f"{_topic_probability_lines(prior_topics)}\n"
    )

    if len(posts_block) > _LINKEDIN_TIER1_POSTS_SECTION_MAX_CHARS:
        posts_block = posts_block[: _LINKEDIN_TIER1_POSTS_SECTION_MAX_CHARS - 40].rstrip() + "\n... [truncated]"

    motives = (analysis_used or "").strip()
    if motives:
        motives_section = (
            "**Recent failure / gap analysis (this step — address in the rewrite):**\n" + motives
        )
    else:
        motives_section = "None"

    past = (past_learnings or "").strip() or "None"

    return tpl.format(
        group_summary=gs,
        group_traits=gt_json,
        past_learnings=past,
        motives_section=motives_section,
        posts_to_process_str=posts_block,
    )


_PART_B_LINKEDIN_GROUP_MEMORY = "part_b_memory_analysis"
_LINKEDIN_GROUP_POSTS_SECTION_MAX = 100000


def _fill_named_placeholders(template: str, **values: str) -> str:
    """Replace ``{key}`` only for explicit keys (safe when template contains JSON ``{{`` blocks)."""
    out = template
    for key, val in values.items():
        out = out.replace("{" + key + "}", str(val))
    return out


def format_linkedin_group_memory_posts_section(
    review_items: List[Dict[str, Any]],
    *,
    max_section_chars: int = _LINKEDIN_GROUP_POSTS_SECTION_MAX,
) -> str:
    """
    Build **Posts:** body for ``part_b_memory_analysis.txt`` (LinkedIn batch root-cause / memory).
    ``review_items`` entries match ``linkedin_memory_batch_root_cause_analyses`` slim dicts.
    """
    if not review_items:
        return "(no posts)"

    blocks: List[str] = []
    for idx, it in enumerate(review_items):
        if not isinstance(it, dict):
            it = {}
        rk = str(it.get("review_key") or "").strip()
        post_id = str(it.get("post_id") or "").strip()
        cat = str(it.get("category") or "").strip()
        stim = str(it.get("stimulus") or "").strip()
        actual = str(it.get("actual_review_text") or "").strip()
        failed = str(it.get("failed_prediction_text") or "").strip()
        prelim = str(it.get("preliminary_delta_notes") or "").strip()
        topics = it.get("topics_ordered") or []
        if not isinstance(topics, list):
            topics = []
        pred = it.get("predicted_themes") or {}
        if not isinstance(pred, dict):
            pred = {}
        gt = it.get("ground_truth_topic_probabilities") or {}
        if not isinstance(gt, dict):
            gt = {}

        pred_line = json.dumps(pred, ensure_ascii=False) if pred else "{}"
        gt_line = json.dumps(gt, ensure_ascii=False) if gt else "{}"
        if len(pred_line) > 4000:
            pred_line = pred_line[:4000] + "…"
        if len(gt_line) > 4000:
            gt_line = gt_line[:4000] + "…"

        topic_order = ", ".join(str(t) for t in topics[:80]) if topics else "(none)"

        ab = actual[:12000] + ("…" if len(actual) > 12000 else "")
        fb = failed[:12000] + ("…" if len(failed) > 12000 else "")
        pr = prelim[:8000] + ("…" if len(prelim) > 8000 else "")

        blocks.append(
            f"### Post index {idx} — post_id `{post_id or '(unknown)'}` — review_key `{rk}`\n\n"
            f"- **Category:** {cat}\n"
            f"- **Batch inclusion:** high-delta prediction failure (queued for Part B memory — "
            f"treat as prediction failure unless Phase 1 shows otherwise)\n"
            f"- **Topics (evaluation order):** {topic_order}\n\n"
            "**Stimulus:**\n"
            f"{stim}\n\n"
            "**Actual post text:**\n"
            f'"""\n{ab}\n"""\n\n'
            "**Failed prediction (synthetic) text:**\n"
            f'"""\n{fb}\n"""\n\n'
            "- **predicted_themes:** "
            f"{pred_line}\n"
            "- **ground_truth_topic_probabilities:** "
            f"{gt_line}\n\n"
            "**Preliminary delta notes (trainer — use as hints, may refine):**\n"
            f"{pr}\n"
        )

    out = "\n---\n\n".join(blocks)
    if len(out) > max_section_chars:
        return out[: max_section_chars - 60].rstrip() + "\n\n... [posts_section truncated]"
    return out


def generate_part_b_linkedin_group_memory_prompt(
    *,
    group_summary: str,
    group_traits: str,
    posts_section: str,
    past_learnings: str = "",
    prior_batch_gaps: str = "",
) -> str:
    """
    Fill ``prompts/part_b_memory_analysis.txt`` for LinkedIn **group** batch memory (Part B).
    Used by ``linkedin_memory_batch_root_cause_analyses`` in ``tier1_sgo_feedback_loop``.
    """
    tpl = io_utils.load_prompt(_PART_B_LINKEDIN_GROUP_MEMORY, settings.PROMPT_DIR_PART_B)
    if not tpl:
        tpl = io_utils.load_prompt(_PART_B_LINKEDIN_GROUP_MEMORY, _SCRIPTS_SGO_PROMPTS)
    if not tpl:
        raise FileNotFoundError(
            f"CRITICAL: '{_PART_B_LINKEDIN_GROUP_MEMORY}.txt' not found under "
            f"{settings.PROMPT_DIR_PART_B} or {_SCRIPTS_SGO_PROMPTS}."
        )
    return _fill_named_placeholders(
        tpl,
        group_summary=(group_summary or "").strip() or "(none)",
        group_traits=(group_traits or "").strip() or "(none)",
        past_learnings=(past_learnings or "").strip() or "(no prior memory batches yet)",
        prior_batch_gaps=(prior_batch_gaps or "").strip()
        or "(no prior batch gaps yet — first memory batch for this run)",
        posts_section=(posts_section or "").strip() or "(none)",
    )


def generate_part_b_batch_prompt(
    micro_persona_characteristics, 
    persona_name, 
    batch_reviews_data, 
    persona_memory, 
    user_chars_data, 
    iteration_number=1
):
    """
    Generates a prompt for Part B: Analysis, for a BATCH of reviews.
    Strict Mode: Raises FileNotFoundError if prompt template is missing.
    """
    # 1. Load Template (Strict)
    template = io_utils.load_prompt("part_b_batch_prompt", settings.PROMPT_DIR_PART_B)
    if not template:
        raise FileNotFoundError(
            f"CRITICAL: 'part_b_batch_prompt.txt' not found in {settings.PROMPT_DIR_PART_B}. "
            "Aborting execution."
        )

    # 2. Build Context String
    profile_parts = []
    if micro_persona_characteristics:
        persona_summary = micro_persona_characteristics.get('persona_summary', '')
        if persona_summary:
            profile_parts.append(f"- **Micro Persona Characteristics:** {persona_summary}")
    
    if not profile_parts:
        profile_parts.append("- **Context:** No profile provided.")
    context_str = "\n".join(profile_parts)

    # 3. Build Reviews Section
    if not batch_reviews_data:
        raise ValueError("CRITICAL: batch_reviews_data is empty. Cannot generate batch prompt.")

    reviews_section = ""
    for i, review_info in enumerate(batch_reviews_data):
        review_data = review_info['data']
        user_id = review_info.get('user_id', 'unknown')
        
        user_chars_text = 'None'
        if user_chars_data and user_id in user_chars_data:
             data = user_chars_data[user_id]
             summary = data.get('llm_characteristics', {}).get('influencing_characteristics_summary', '')
             if summary: user_chars_text = f"[General] {summary}"

        # Get Themes
        predicted_themes = review_data['prediction'].get('predicted_themes', {})
        actual_themes = review_data['actual'].get('predicted_themes', [])
        failed_text = review_data['prediction'].get('review_text', 'N/A')
        
        k = len(actual_themes) if actual_themes else 0
        n = max(3, k)
        
        actual_themes_set = set(str(theme).strip().lower() for theme in actual_themes) if isinstance(actual_themes, list) else set()
        
        # Find incorrect themes in top N
        incorrect_themes = []
        if isinstance(predicted_themes, dict):
            top_n_predicted = sorted(predicted_themes.items(), key=lambda x: x[1], reverse=True)[:n]
            for theme, score in top_n_predicted:
                if str(theme).strip().lower() not in actual_themes_set:
                    incorrect_themes.append(theme)
        
        incorrect_str = ', '.join(incorrect_themes) if incorrect_themes else "None (all top predicted themes correct)"
        
        # Format Lists
        actual_str = ', '.join([str(t) for t in actual_themes]) if actual_themes else 'None'
        pred_keys = list(predicted_themes.keys()) if isinstance(predicted_themes, dict) else []
        pred_str = ', '.join([str(t) for t in pred_keys[:n]]) if pred_keys else 'None'
        
        prediction_context = ""
        if iteration_number > 1:
            prediction_context = f"**NOTE: This is a CORRECTED prediction from iteration {iteration_number - 1} that still needs improvement.**"

        reviews_section += f"""
--- Review Index {i} ---
{prediction_context}
**User Characteristics:** {user_chars_text}
**Product Description:** "{review_data.get('product_description', 'N/A')}"
**Actual Review Text:** "{review_data['actual']['review_text']}"
**Actual Themes:** {actual_str}
**Failed Prediction Text:** "{failed_text}"
**Predicted Themes (top {n}):** {pred_str}
**Incorrectly Predicted:** {incorrect_str}
"""

    # 4. Format Memory
    persona_data = persona_memory.get(persona_name, {})
    if isinstance(persona_data, list):
        enrichment_log = persona_data
    else:
        enrichment_log = persona_data.get('batch_analyses', [])
        
    past_learnings = ' | '.join([str(item) for item in enrichment_log]) if enrichment_log else 'None'

    # 5. Fill Template
    return template.format(
        context_str=context_str,
        refined_chars_section="", 
        past_learnings=past_learnings,
        reviews_section=reviews_section.strip()
    )
def generate_part_b_individual_prompt(micro_persona_characteristics, user_characteristics_list, persona_name, review_data, persona_memory):
    """Generates a prompt for Part B: Analysis, for a SINGLE review. Loads template from file."""
    # Load prompt template from file
    prompt_template = load_prompt("part_b_individual_prompt", PROMPT_DIR_PART_B)
    if not prompt_template:
        logging.error("part_b_individual_prompt.txt not found. Cannot generate prompt.")
        return ""
    
    # Handle both old format (list) and new format (dict with batch_analyses)
    persona_data = persona_memory.get(persona_name, {})
    if isinstance(persona_data, list):
        enrichment_log = persona_data
    else:
        enrichment_log = persona_data.get('batch_analyses', [])

    # Get predicted and actual themes to identify incorrect ones in top 3
    predicted_themes = review_data['prediction'].get('predicted_themes', {})
    actual_themes = review_data['actual'].get('predicted_themes', [])
    k = len(actual_themes) if actual_themes else 0
    n = max(3, k)
    
    # Convert actual themes to a set for comparison (normalize strings)
    actual_themes_set = set(str(theme).strip().lower() for theme in actual_themes) if isinstance(actual_themes, list) else set()
    
    # Find incorrectly predicted themes from TOP n
    incorrect_themes_top_n = []
    if isinstance(predicted_themes, dict):
        # Get top n predicted themes (sorted by score)
        top_n_predicted = sorted(predicted_themes.items(), key=lambda x: x[1], reverse=True)[:n]
        for theme, score in top_n_predicted:
            theme_normalized = str(theme).strip().lower()
            # Check if this theme is not in actual themes
            if theme_normalized not in actual_themes_set:
                incorrect_themes_top_n.append(theme)
    
    # Format incorrect themes string (from top n)
    if incorrect_themes_top_n:
        incorrect_themes_str = ', '.join(incorrect_themes_top_n)
    else:
        incorrect_themes_str = "None (all top n predicted themes are correct)"
    
    review_to_process_str = (
        f"Product Description: \"{review_data.get('product_description', 'N/A')}\"\n"
        f"Actual Review Text: \"{review_data['actual']['review_text']}\"\n"
        f"Incorrectly Predicted Themes (from top {n}): {incorrect_themes_str}\n"
    )
    
    # Get persona summary text
    persona_summary_text = ""
    if micro_persona_characteristics:
        persona_summary_text = micro_persona_characteristics.get('persona_summary', '')
    
    # Get actual themes as a list for better context
    actual_themes_list = actual_themes if isinstance(actual_themes, list) else []
    actual_themes_str = ', '.join([str(t) for t in actual_themes_list]) if actual_themes_list else 'None'
    
    # Get predicted themes as a list for comparison
    predicted_themes_list = list(predicted_themes.keys()) if isinstance(predicted_themes, dict) else []
    predicted_themes_str = ', '.join([str(t) for t in predicted_themes_list[:n]]) if predicted_themes_list else 'None'
    
    # Format past learnings
    past_learnings = ' | '.join([str(item) for item in enrichment_log]) if enrichment_log else 'None'
    
    # Format the prompt using the template
    prompt = prompt_template.format(
        persona_summary_text=persona_summary_text if persona_summary_text else 'N/A',
        past_learnings=past_learnings,
        review_to_process_str=review_to_process_str.strip(),
        actual_themes_str=actual_themes_str,
        predicted_themes_str=predicted_themes_str,
        incorrect_themes_str=incorrect_themes_str
    )
    
    return prompt
