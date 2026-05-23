"""Per–outer-iteration S3 checkpoints for long LinkedIn SGO runs (resume across worker invocations)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.linkedin_tiered_s3 import LinkedinTieredS3ArtifactNames

from .linkedin_sgo_pipeline_async_errors import LinkedInSGOPipelineError

CHECKPOINT_VERSION = 1
PARTIAL_CHECKPOINT_VERSION = 1


def checkpoint_root(base_tiered: str, job_id: str) -> str:
    return f"{base_tiered.rstrip('/')}/linkedin_sgo/checkpoints/{job_id}"


def checkpoint_iteration_prefix(root: str, iteration_completed: int) -> str:
    return f"{root.rstrip('/')}/after_iter_{int(iteration_completed):04d}"


def checkpoint_during_iteration_root(root: str, outer_iteration: int) -> str:
    return f"{root.rstrip('/')}/during_iter_{int(outer_iteration):04d}"


def _sanitize_progress_slug(progress_reason: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "_", (progress_reason or "progress").strip())
    return (slug[:120] or "progress").strip("_") or "progress"


def checkpoint_partial_prefix(root: str, outer_iteration: int, progress_reason: str) -> str:
    slug = _sanitize_progress_slug(progress_reason)
    return f"{checkpoint_during_iteration_root(root, outer_iteration)}/partials/{slug}"


def latest_completed_checkpoint_iteration(
    *,
    base_tiered: str,
    job_id: str,
    scan_max: int,
) -> int:
    """
    Highest outer-iteration index that has a valid ``resume.json`` on S3 (0 if none).

    Readers only trust checkpoints where ``resume.json`` exists (commit marker uploaded last).
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


def latest_partial_checkpoint(
    *,
    base_tiered: str,
    job_id: str,
    outer_iteration: int,
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """
    Newest in-progress partial checkpoint for ``outer_iteration`` (by ``partial_resume.json``).

    Returns ``(s3_prefix, partial_resume_doc)`` or ``None``.
    """
    from app.config import s3_bucket, s3_client
    from app.utils.s3_utils import try_fetch_json_from_s3

    if not s3_client or not s3_bucket:
        return None

    root = checkpoint_root(base_tiered, job_id)
    partials_root = f"{checkpoint_during_iteration_root(root, outer_iteration)}/partials/"
    best: Optional[Tuple[str, Dict[str, Any], str]] = None

    paginator = s3_client.get_paginator("list_objects_v2")
    try:
        for page in paginator.paginate(Bucket=s3_bucket, Prefix=partials_root):
            for obj in page.get("Contents") or []:
                key = str(obj.get("Key") or "")
                if not key.endswith("/partial_resume.json"):
                    continue
                doc = try_fetch_json_from_s3(key)
                if not isinstance(doc, dict):
                    continue
                if int(doc.get("version") or 0) != PARTIAL_CHECKPOINT_VERSION:
                    continue
                hb = str(doc.get("heartbeat_at") or "")
                prefix = key[: -len("/partial_resume.json")]
                if best is None or hb > best[2]:
                    best = (prefix, doc, hb)
    except Exception:
        return None

    if best is None:
        return None
    return best[0], best[1]


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
        "checkpoint_kind": "complete",
        "iteration_completed": iteration_completed,
        "num_iterations_planned": num_iterations_planned,
        "next_outer_iteration": iteration_completed + 1,
        "tier_mode": tier_mode,
        "qualifying_keys_for_next": qualifying_keys_for_next,
        "pred_snapshot": pred_snapshot_jsonable,
        "heartbeat_at": datetime.now(timezone.utc).isoformat(),
    }


def build_partial_resume_payload(
    *,
    outer_iteration: int,
    num_iterations_planned: int,
    tier_mode: str,
    progress_reason: str,
    processed_post_ids: List[str],
    pred_snapshot_jsonable: Dict[str, Any],
    failed_posts: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    return {
        "version": PARTIAL_CHECKPOINT_VERSION,
        "checkpoint_kind": "partial",
        "outer_iteration": int(outer_iteration),
        "num_iterations_planned": int(num_iterations_planned),
        "tier_mode": tier_mode,
        "progress_reason": progress_reason,
        "processed_post_ids": list(processed_post_ids),
        "pred_snapshot": pred_snapshot_jsonable,
        "failed_posts": list(failed_posts or []),
        "heartbeat_at": datetime.now(timezone.utc).isoformat(),
    }


def _upload_checkpoint_artifact_files(
    *,
    prefix: str,
    user_pred_jsonable: Dict[str, Any],
    work_dir: Path,
    state_path: Path,
    memory_path: Path,
    trace_path: Path,
    iteration_stats_path: Path,
    out_path: Path,
) -> None:
    from .linkedin_sgo_pipeline_async_s3_workersafe import (
        upload_json_to_s3_worker,
        upload_text_to_s3_worker,
    )

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

    for agg_name in (
        "linkedin_tier1_all_reviews_deltas.json",
        "linkedin_tier2_all_reviews_deltas.json",
    ):
        agg_path = work_dir / agg_name
        if agg_path.is_file():
            upload_json_to_s3_worker(
                f"{prefix}/aggregate_deltas.json",
                json.loads(agg_path.read_text(encoding="utf-8")),
            )
            break


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
    Persist one completed outer-iteration checkpoint.

    All artifact objects are uploaded first; ``resume.json`` is written last as the commit marker.
    """
    from .linkedin_sgo_pipeline_async_s3_workersafe import upload_json_to_s3_worker

    root = checkpoint_root(base_tiered, job_id)
    prefix = checkpoint_iteration_prefix(root, iteration_completed)
    _upload_checkpoint_artifact_files(
        prefix=prefix,
        user_pred_jsonable=user_pred_jsonable,
        work_dir=work_dir,
        state_path=state_path,
        memory_path=memory_path,
        trace_path=trace_path,
        iteration_stats_path=iteration_stats_path,
        out_path=out_path,
    )
    upload_json_to_s3_worker(f"{prefix}/resume.json", resume_doc)
    delete_partial_checkpoints_for_iteration(
        base_tiered=base_tiered,
        job_id=job_id,
        outer_iteration=iteration_completed,
    )
    return prefix


def upload_partial_iteration_checkpoint(
    *,
    base_tiered: str,
    job_id: str,
    outer_iteration: int,
    progress_reason: str,
    partial_doc: Dict[str, Any],
    user_pred_jsonable: Dict[str, Any],
    work_dir: Path,
    state_path: Path,
    memory_path: Path,
    trace_path: Path,
    iteration_stats_path: Path,
    out_path: Path,
) -> str:
    """Mid–outer-iteration checkpoint (commit marker: ``partial_resume.json`` uploaded last)."""
    from .linkedin_sgo_pipeline_async_s3_workersafe import upload_json_to_s3_worker

    root = checkpoint_root(base_tiered, job_id)
    prefix = checkpoint_partial_prefix(root, outer_iteration, progress_reason)
    _upload_checkpoint_artifact_files(
        prefix=prefix,
        user_pred_jsonable=user_pred_jsonable,
        work_dir=work_dir,
        state_path=state_path,
        memory_path=memory_path,
        trace_path=trace_path,
        iteration_stats_path=iteration_stats_path,
        out_path=out_path,
    )
    upload_json_to_s3_worker(f"{prefix}/partial_resume.json", partial_doc)
    return prefix


def delete_partial_checkpoints_for_iteration(
    *,
    base_tiered: str,
    job_id: str,
    outer_iteration: int,
) -> None:
    """Remove in-progress partials after a full outer iteration completes."""
    from app.config import s3_bucket, s3_client

    if not s3_client or not s3_bucket:
        return

    root = checkpoint_root(base_tiered, job_id)
    partials_root = f"{checkpoint_during_iteration_root(root, outer_iteration)}/partials/"
    to_delete: List[Dict[str, str]] = []
    paginator = s3_client.get_paginator("list_objects_v2")
    try:
        for page in paginator.paginate(Bucket=s3_bucket, Prefix=partials_root):
            for obj in page.get("Contents") or []:
                key = str(obj.get("Key") or "")
                if key:
                    to_delete.append({"Key": key})
        for i in range(0, len(to_delete), 1000):
            chunk = to_delete[i : i + 1000]
            if chunk:
                s3_client.delete_objects(Bucket=s3_bucket, Delete={"Objects": chunk})
    except Exception:
        pass


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
    root = checkpoint_root(base_tiered, job_id)
    prefix = checkpoint_iteration_prefix(root, iteration_completed)
    return _restore_prefix_into_workdir(prefix, work_dir)


def restore_partial_checkpoint_into_workdir(
    *,
    base_tiered: str,
    job_id: str,
    s3_prefix: str,
    work_dir: Path,
) -> Tuple[Dict[str, Any], Path]:
    """Restore mid–outer-iteration state from a partial checkpoint prefix."""
    return _restore_prefix_into_workdir(s3_prefix, work_dir, partial=True)


def _restore_prefix_into_workdir(
    prefix: str,
    work_dir: Path,
    *,
    partial: bool = False,
) -> Tuple[Dict[str, Any], Path]:
    from app.utils.s3_utils import try_fetch_json_from_s3

    from .linkedin_sgo_pipeline_async_s3_workersafe import try_fetch_text_from_s3

    marker = "partial_resume.json" if partial else "resume.json"
    resume = try_fetch_json_from_s3(f"{prefix}/{marker}")
    if not resume or not isinstance(resume, dict):
        raise LinkedInSGOPipelineError(f"Missing checkpoint {marker} at {prefix}/{marker}")
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
