"""
Tribe Schema Evolver: incremental refinement of persona (qualitative_summary) from prediction error logs.
Integrated with SGO training: run after every N batches; refined schema is used as LLM input for subsequent batches.
Prompts are loaded from ``scripts/scripts_sgo/prompts`` (e.g. refine_characteristics.txt, shrink_refinement.txt).
"""
import copy
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from openai import OpenAI

_SCRIPTS_SGO = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_SGO) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_SGO))

from utils.openai_chat_params import build_chat_completion_kwargs

# Canonical five-list keys for qualitative_summary (same as LinkedIn tier1 evolution state).
QUALITATIVE_SUMMARY_KEYS: Tuple[str, ...] = (
    "skills_and_expertise",
    "working_style",
    "motivations_and_values",
    "pain_points_and_needs",
    "org_leadership_and_psychographic_profile",
)


def persona_dict_for_prompt(group_summary: str, qualitative_summary: Dict[str, Any]) -> Dict[str, Any]:
    """Single JSON object for refine/shrink prompts: prose summary + five trait lists."""
    q = qualitative_summary if isinstance(qualitative_summary, dict) else {}
    gs = (group_summary or "").strip()
    if not gs:
        g2 = q.get("group_summary")
        if isinstance(g2, str):
            gs = g2.strip()
    out: Dict[str, Any] = {"group_summary": gs}
    for k in QUALITATIVE_SUMMARY_KEYS:
        rows = q.get(k)
        out[k] = copy.deepcopy(rows) if isinstance(rows, list) else []
    return out


def qualitative_slice_from_persona_response(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Extract qualitative_summary-shaped dict from a full persona JSON object."""
    base: Dict[str, Any] = {k: [] for k in QUALITATIVE_SUMMARY_KEYS}
    if not isinstance(data, dict):
        return base
    nested = data.get("qualitative_summary")
    if isinstance(nested, dict):
        data = nested
    for k in QUALITATIVE_SUMMARY_KEYS:
        rows = data.get(k)
        if isinstance(rows, list):
            base[k] = rows
    return base


def _get_prompt_dir() -> Path:
    """Resolve prompts directory: use settings if set and exists, else ``scripts/scripts_sgo/prompts``."""
    try:
        from sgo_training.config import settings
        prompt_dir = getattr(settings, "PROMPT_DIR_PART_A", None)
        if prompt_dir is not None and Path(prompt_dir).exists():
            return Path(prompt_dir)
    except Exception:
        pass
    # Default: prompts next to scripts_sgo (scripts/scripts_sgo/prompts)
    local = Path(__file__).resolve().parent.parent / "prompts"
    if local.exists():
        return local
    return Path(__file__).resolve().parent.parent.parent / "prompts"


def _load_prompt(name: str) -> str:
    """Load prompt template from prompts dir. Raises FileNotFoundError if missing."""
    prompt_dir = _get_prompt_dir()
    path = prompt_dir / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _get_client():
    """OpenAI client using API key from settings or environment."""
    from sgo_training.config import settings
    api_key = getattr(settings, "OPENAI_API_KEY", None) or os.getenv("OPENAI_API_KEY")
    base_url = getattr(settings, "OPENAI_BASE_URL", None) or os.getenv("OPENAI_BASE_URL")
    if not api_key:
        raise ValueError("OPENAI_API_KEY must be set in config or environment for TribeSchemaEvolver")
    return OpenAI(api_key=api_key, base_url=base_url or None)


def enforce_hard_limit(summary_block: Dict[str, Any], max_items: int = 20) -> Dict[str, Any]:
    """
    Programmatically enforces the hard limit of max_items per section.
    If >max_items exist, DELETE the items with the lowest confidence/relevance.
    """
    # Canonical 5-category persona schema: enforce max_items per category list by dropping
    # the lowest-confidence rows (keep strongest evidence).
    for k in QUALITATIVE_SUMMARY_KEYS:
        items = summary_block.get(k, [])
        if isinstance(items, list) and len(items) > max_items:
            items_with_scores = []
            for item in items:
                if isinstance(item, dict):
                    score = float(item.get("confidence_score", 0.0) or 0.0)
                    items_with_scores.append((score, item))
                else:
                    items_with_scores.append((0.0, item))
            items_with_scores.sort(key=lambda x: x[0])
            summary_block[k] = [item for _, item in items_with_scores[-max_items:]]

    return summary_block


class TribeSchemaEvolver:
    """
    Incremental persona (qualitative_summary) refinement from batch error logs.
    Used by SGO pipeline: run one refinement step every N batches; optionally shrink every M refinement steps.
    """

    def __init__(
        self,
        cluster_id: str,
        micro_id: str,
        memory_dir: str,
        refined_schema_dir: Optional[str] = None,
        evolution_model: str = "gpt-4o",
        refine_prompt_basename: str = "persona_refine_prompt",
        shrink_prompt_basename: str = "persona_shrink_prompt",
    ):
        self.cluster_id = cluster_id
        self.micro_id = micro_id
        self.memory_dir = memory_dir
        self.refined_schema_dir = refined_schema_dir or memory_dir
        self.evolution_model = evolution_model
        self.refine_prompt_basename = refine_prompt_basename
        self.shrink_prompt_basename = shrink_prompt_basename
        self._client = None

    @property
    def client(self):
        if self._client is None:
            self._client = _get_client()
        return self._client

    def _call_llm(self, prompt: str, temperature: float = 0.2) -> Optional[Dict]:
        try:
            response = self.client.chat.completions.create(
                **build_chat_completion_kwargs(
                    self.evolution_model,
                    [{"role": "system", "content": prompt}],
                    temperature=temperature,
                    json_mode=True,
                )
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            print(f"   [TribeSchemaEvolver] LLM Error: {e}")
            return None

    def refine_step(
        self,
        current_summary_block: Dict[str, Any],
        batch_logs: Dict[str, Any],
        baseline_summary_block: Dict[str, Any],
        batch_error_logs: Optional[str] = None,
        batch_analyses_text: Optional[str] = None,
        *,
        group_summary: str = "",
        refine_prompt_basename: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Run one refinement step: evolve qualitative_summary using batch memory error logs.

        ``batch_logs``: dict mapping batch_key -> { gaps[], outliers[], batch_verdict, ... }.
        If ``batch_error_logs`` is set, it is used verbatim (from ``build_refine_error_logs_text``).
        Deprecated alias: ``batch_analyses_text``.
        """
        _ = baseline_summary_block
        prebuilt_logs = (
            batch_error_logs
            if batch_error_logs is not None
            else batch_analyses_text
        )
        try:
            from generate_synthetic_review_and_memory_analysis.prompt_generation_for_both_parts import (
                _fill_named_placeholders,
                format_memory_batch_entry_lines,
                _sort_memory_batch_keys,
            )
        except ImportError:
            def _fill_named_placeholders(template: str, **values: str) -> str:
                out = template
                for key, val in values.items():
                    out = out.replace("{" + key + "}", str(val))
                return out

            format_memory_batch_entry_lines = None  # type: ignore
            _sort_memory_batch_keys = None  # type: ignore

        if prebuilt_logs is not None:
            logs_text = prebuilt_logs
        else:
            try:
                if format_memory_batch_entry_lines is None:
                    raise ImportError

                formatted_logs: list = []
                for batch_key in _sort_memory_batch_keys(list(batch_logs.keys())):
                    data = batch_logs.get(batch_key)
                    if not isinstance(data, dict):
                        continue
                    formatted_logs.extend(
                        format_memory_batch_entry_lines(
                            str(batch_key),
                            data,
                            include_gaps=True,
                            include_outliers=False,
                            refine_gaps_only=True,
                        )
                    )
                logs_text = "\n".join(formatted_logs) if formatted_logs else "(no memory batches)"
            except ImportError:
                formatted_logs = []
                for batch_key, data in batch_logs.items():
                    formatted_logs.append(f"--- {batch_key} ---")
                    for analysis in data.get("analyses", []):
                        formatted_logs.append(f"- {analysis}")
                    for g in data.get("gaps") or []:
                        if isinstance(g, dict):
                            formatted_logs.append(
                                f"- {g.get('behavioral_gap', g)}"
                            )
                    for o in data.get("outliers") or []:
                        if isinstance(o, dict):
                            pid = str(o.get("post_id") or "").strip()
                            prefix = f"[outlier {pid}] " if pid else "[outlier] "
                            formatted_logs.append(
                                prefix + str(o.get("behavioral_gap") or o)
                            )
                    for p in data.get("patterns") or []:
                        if isinstance(p, dict):
                            formatted_logs.append(f"- {p.get('behavior', p)}")
                logs_text = "\n".join(formatted_logs)

        prompt_name = refine_prompt_basename or self.refine_prompt_basename
        template = _load_prompt(prompt_name)
        persona_json = json.dumps(
            persona_dict_for_prompt(group_summary, current_summary_block),
            ensure_ascii=False,
            indent=2,
        )
        full_prompt = _fill_named_placeholders(
            template,
            current_group_persona_json=persona_json,
            batch_error_logs=logs_text,
        )
        refined = self._call_llm(full_prompt, temperature=0.2)
        if not refined:
            return None
        qual = qualitative_slice_from_persona_response(refined)
        enforce_hard_limit(qual, max_items=20)
        return qual

    def shrink_step(
        self,
        current_summary_block: Dict[str, Any],
        *,
        group_summary: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Shrink persona (traits) via shrink_refinement.txt; returns qualitative_summary only."""
        template = _load_prompt(self.shrink_prompt_basename)
        persona_json = json.dumps(
            persona_dict_for_prompt(group_summary, current_summary_block),
            ensure_ascii=False,
            indent=2,
        )
        try:
            from generate_synthetic_review_and_memory_analysis.prompt_generation_for_both_parts import (
                _fill_named_placeholders,
            )
        except ImportError:

            def _fill_named_placeholders(template: str, **values: str) -> str:
                out = template
                for key, val in values.items():
                    out = out.replace("{" + key + "}", str(val))
                return out

        full_prompt = _fill_named_placeholders(
            template,
            current_group_persona_json=persona_json,
        )
        shrunk = self._call_llm(full_prompt, temperature=0.3)
        if not shrunk:
            return None
        qual = qualitative_slice_from_persona_response(shrunk)
        enforce_hard_limit(qual, max_items=20)
        return qual

    def run_single_refinement_step(
        self,
        new_batch_logs: Dict[str, Any],
        current_qualitative_summary: Dict[str, Any],
        baseline_qualitative_summary: Dict[str, Any],
        batch_error_logs: Optional[str] = None,
        batch_analyses_text: Optional[str] = None,
        *,
        group_summary: str = "",
        refine_prompt_basename: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Run one refinement step on the given batch logs and return updated qualitative_summary.
        Pass ``batch_error_logs`` from ``build_refine_error_logs_text`` (window of memory batches).
        """
        return self.refine_step(
            current_qualitative_summary,
            new_batch_logs,
            baseline_qualitative_summary,
            batch_error_logs=batch_error_logs,
            batch_analyses_text=batch_analyses_text,
            group_summary=group_summary,
            refine_prompt_basename=refine_prompt_basename,
        )
