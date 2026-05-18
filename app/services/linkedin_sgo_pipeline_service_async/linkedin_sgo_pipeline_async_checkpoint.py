"""Per–outer-iteration S3 checkpoints for long LinkedIn SGO runs (resume across worker invocations)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.linkedin_tiered_s3 import LinkedinTieredS3ArtifactNames

from .linkedin_sgo_pipeline_async_errors import LinkedInSGOPipelineError

CHECKPOINT_VERSION = 1


def checkpoint_root(base_tiered: str, job_id: str) -> str:
    return f"{base_tiered.rstrip('/')}/linkedin_sgo/checkpoints/{job_id}"


def checkpoint_iteration_prefix(root: str, iteration_completed: int) -> str:
    return f"{root.rstrip('/')}/after_iter_{int(iteration_completed):04d}"


def latest_completed_checkpoint_iteration(
    *,
    base_tiered: str,
    job_id: str,
    scan_max: int,
) -> int:
    """
    Highest outer-iteration index that has a valid ``resume.json`` on S3 (0 if none).

    Used to reconcile DB progress when the DB update fails after a successful checkpoint upload,
    and to resume ``FAILED`` jobs without redoing completed iterations.
    """
    from app.utils.s3_utils import try_fetch_json_from_s3

    root = checkpoint_root(base_tiered, job_id)
    cap = max(0, min(int(scan_max), 50))
    for k in range(cap, 0, -1):
        prefix = checkpoint_iteration_prefix(root, k)
        doc = try_fetch_json_from_s3(f"{prefix}/resume.json")
        if isinstance(doc, dict) and int(doc.get("version") or 0) == CHECKPOINT_VERSION:
            return k
    return 0


def build_resume_payload(
    *,
    iteration_completed: int,
    num_iterations_planned: int,
    tier_mode: str,
    qualifying_keys_for_next: List[List[str]],
    pred_snapshot_jsonable: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "version": CHECKPOINT_VERSION,
        "iteration_completed": iteration_completed,
        "num_iterations_planned": num_iterations_planned,
        "next_outer_iteration": iteration_completed + 1,
        "tier_mode": tier_mode,
        "qualifying_keys_for_next": qualifying_keys_for_next,
        "pred_snapshot": pred_snapshot_jsonable,
        "heartbeat_at": datetime.now(timezone.utc).isoformat(),
    }


def upload_iteration_checkpoint(
    *,
    base_tiered: str,
    job_id: str,
    iteration_completed: int,
    resume_doc: Dict[str, Any],
    user_pred_jsonable: Dict[str, Any],
    work_dir: Path,
    state_path: Path,
    memory_path: Path,
    trace_path: Path,
    iteration_stats_path: Path,
    out_path: Path,
) -> str:
    """
    Persist one checkpoint prefix on S3; returns the iteration prefix.

    **Idempotency:** Re-uploading the same ``(job_id, iteration_completed)`` overwrites
    object keys under that prefix with full PUTs. Readers see one coherent snapshot per key
    (no in-place corruption). A duplicate run after a successful upload should replace with
    equivalent iteration output, not append or merge partially.
    """
    from .linkedin_sgo_pipeline_async_s3_workersafe import (
        upload_json_to_s3_worker,
        upload_text_to_s3_worker,
    )

    root = checkpoint_root(base_tiered, job_id)
    prefix = checkpoint_iteration_prefix(root, iteration_completed)
    upload_json_to_s3_worker(f"{prefix}/resume.json", resume_doc)
    upload_json_to_s3_worker(f"{prefix}/user_predictions.json", user_pred_jsonable)

    if state_path.is_file():
        upload_json_to_s3_worker(
            f"{prefix}/evolution_state.json",
            json.loads(state_path.read_text(encoding="utf-8")),
        )
    if memory_path.is_file():
        upload_json_to_s3_worker(
            f"{prefix}/memory_batch_analyses.json",
            json.loads(memory_path.read_text(encoding="utf-8")),
        )
    if out_path.is_file():
        upload_json_to_s3_worker(
            f"{prefix}/delta_method_predictions.json",
            json.loads(out_path.read_text(encoding="utf-8")),
        )
    if trace_path.is_file():
        upload_text_to_s3_worker(
            f"{prefix}/post_traces.jsonl",
            trace_path.read_text(encoding="utf-8"),
            content_type="application/x-ndjson; charset=utf-8",
        )
    if iteration_stats_path.is_file():
        upload_text_to_s3_worker(
            f"{prefix}/iteration_stats.jsonl",
            iteration_stats_path.read_text(encoding="utf-8"),
            content_type="application/x-ndjson; charset=utf-8",
        )

    agg_path = work_dir / "linkedin_tier1_all_reviews_deltas.json"
    if agg_path.is_file():
        upload_json_to_s3_worker(
            f"{prefix}/aggregate_deltas.json",
            json.loads(agg_path.read_text(encoding="utf-8")),
        )
    return prefix


def restore_checkpoint_into_workdir(
    *,
    base_tiered: str,
    job_id: str,
    iteration_completed: int,
    work_dir: Path,
) -> Tuple[Dict[str, Any], Path]:
    """
    Restore files after ``iteration_completed`` so the worker can start outer iteration ``iteration_completed + 1``.

    Returns ``(resume_doc, user_pred_seed_path)``.
    """
    from app.utils.s3_utils import try_fetch_json_from_s3

    from .linkedin_sgo_pipeline_async_s3_workersafe import try_fetch_text_from_s3

    root = checkpoint_root(base_tiered, job_id)
    prefix = checkpoint_iteration_prefix(root, iteration_completed)
    resume = try_fetch_json_from_s3(f"{prefix}/resume.json")
    if not resume or not isinstance(resume, dict):
        raise LinkedInSGOPipelineError(f"Missing checkpoint resume.json at {prefix}/resume.json")
    up = try_fetch_json_from_s3(f"{prefix}/user_predictions.json")
    if not isinstance(up, dict):
        raise LinkedInSGOPipelineError(f"Missing user_predictions.json at {prefix}/")

    work_dir.mkdir(parents=True, exist_ok=True)
    seed_path = work_dir / ".sgo_resume_user_predictions.json"
    seed_path.write_text(json.dumps(up, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    evo = try_fetch_json_from_s3(f"{prefix}/evolution_state.json")
    if isinstance(evo, dict):
        ed = work_dir / "evolution"
        ed.mkdir(parents=True, exist_ok=True)
        (ed / "evolution_state.json").write_text(
            json.dumps(evo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    mem = try_fetch_json_from_s3(f"{prefix}/memory_batch_analyses.json")
    if isinstance(mem, dict):
        md = work_dir / "memory"
        md.mkdir(parents=True, exist_ok=True)
        (md / "batch_analyses.json").write_text(
            json.dumps(mem, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    pred = try_fetch_json_from_s3(f"{prefix}/delta_method_predictions.json")
    if isinstance(pred, dict):
        (work_dir / "delta_method_predictions.json").write_text(
            json.dumps(pred, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    traces = try_fetch_text_from_s3(f"{prefix}/post_traces.jsonl")
    if traces is not None:
        (work_dir / "post_traces.jsonl").write_text(traces, encoding="utf-8")

    stats = try_fetch_text_from_s3(f"{prefix}/iteration_stats.jsonl")
    if stats is not None:
        (work_dir / "iteration_stats.jsonl").write_text(stats, encoding="utf-8")

    return resume, seed_path


def promote_checkpoint_to_canonical_artifacts(
    *,
    base_tiered: str,
    job_id: str,
    tier_int: int,
    last_iteration_completed: int,
    art: LinkedinTieredS3ArtifactNames,
    manifest_key: str,
) -> Dict[str, str]:
    """
    After all outer iterations finished, copy the last checkpoint objects to canonical tier keys
    (pred / evolution / memory / aggregate) and refresh manifest — **without** a local work_dir.

    **Idempotency:** Uses the same deterministic S3 keys each time. Re-running promote with the
    same ``last_iteration_completed`` re-uploads the same logical documents (overwrite PUTs) and
    refreshes manifest timestamps; it does not concatenate or patch JSON in place, so canonical
    artifacts stay whole objects.
    """
    from app.utils.s3_utils import try_fetch_json_from_s3

    from .linkedin_sgo_pipeline_async_s3_workersafe import upload_json_to_s3_worker

    from .linkedin_sgo_pipeline_async_s3 import update_manifest_linkedin_sgo

    prefix = checkpoint_iteration_prefix(checkpoint_root(base_tiered, job_id), last_iteration_completed)
    base = base_tiered.rstrip("/")

    if tier_int == 2:
        pred_k = art.linkedin_sgo_predictions_tier2
        evo_k = art.linkedin_sgo_evolution_state_tier2
        mem_k = art.linkedin_sgo_memory_batches_tier2
        agg_k = art.linkedin_sgo_aggregate_deltas_tier2
    else:
        pred_k = art.linkedin_sgo_predictions_tier1
        evo_k = art.linkedin_sgo_evolution_state_tier1
        mem_k = art.linkedin_sgo_memory_batches_tier1
        agg_k = art.linkedin_sgo_aggregate_deltas_tier1

    urls: Dict[str, str] = {}

    pred = try_fetch_json_from_s3(f"{prefix}/delta_method_predictions.json")
    if isinstance(pred, dict):
        urls["predictions"] = upload_json_to_s3_worker(f"{base}/{pred_k}", pred)

    evo = try_fetch_json_from_s3(f"{prefix}/evolution_state.json")
    if isinstance(evo, dict):
        urls["evolution_state"] = upload_json_to_s3_worker(f"{base}/{evo_k}", evo)

    mem = try_fetch_json_from_s3(f"{prefix}/memory_batch_analyses.json")
    if isinstance(mem, dict):
        urls["memory_batches"] = upload_json_to_s3_worker(f"{base}/{mem_k}", mem)

    agg_path_key = f"{prefix}/aggregate_deltas.json"
    agg = try_fetch_json_from_s3(agg_path_key)
    if not isinstance(agg, dict):
        agg = try_fetch_json_from_s3(f"{prefix}/linkedin_all_reviews_deltas.json")
    if isinstance(agg, dict):
        urls["aggregate_deltas"] = upload_json_to_s3_worker(f"{base}/{agg_k}", agg)

    if urls:
        update_manifest_linkedin_sgo(base, manifest_key, tier_int, urls)
    return urls
