"""Async multi-post initial prediction (i0 prompts + topic scoring)."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

from openai import AsyncOpenAI, OpenAI

from app.config import s3_bucket as _s3_bucket
from app.config import s3_client as _s3_client
from app.linkedin_tiered_s3 import get_linkedin_tiered_s3_artifact_names
from app.utils.s3_utils import get_s3_key_for_audience, upload_json_to_s3

from .initial_prediction_config import get_initial_prediction_config
from .initial_prediction_s3 import partial_s3_key_for_artifact, update_manifest_initial_prediction
from .initial_prediction_types import (
    InitialPredictionRunParams,
    InitialPredictionRunResult,
    _post_key_to_meta_for_posts,
    build_initial_prediction_artifact,
    resolve_openai_api_key_for_tier,
    write_prediction_json_local,
)
from .description_json_io import (
    load_tribe_description,
    try_load_qualitative_from_description_json,
)
from .stimulus_posts import iter_posts_with_ground_truth
from .i0.contextual_payload import coalesce_contextual_payload
from .i0.i0_audience_room import (
    build_i0_initial_prediction_linkedin_prompt,
    category_themes_for_post,
    load_category_themes_map,
    run_audience_room_i0_for_post,
)
from .i0.i0_linkedin_context import (
    extract_individual_profile_for_i0,
    load_profile_json,
    resolve_group_traits_text,
)

logger = logging.getLogger(__name__)


def _resolve_run_weights_and_i0(params: InitialPredictionRunParams) -> Tuple[float, float, str, str, float]:
    cfg = get_initial_prediction_config()
    wt = float(params.weight_text) if params.weight_text is not None else float(cfg.weight_text_delta)
    wm = float(params.weight_theme) if params.weight_theme is not None else float(cfg.weight_theme_delta)
    sm = (params.scoring_mode or cfg.scoring_mode or "logprobs").strip() or "logprobs"
    em = (params.embedding_model or cfg.embedding_model).strip()
    emi = float(
        params.embedding_min_interval_sec
        if params.embedding_min_interval_sec is not None
        else cfg.embedding_min_interval_sec
    )
    return wt, wm, sm, em, emi


async def run_initial_prediction(params: InitialPredictionRunParams) -> InitialPredictionRunResult:
    """Run initial prediction over all posts with ground-truth topic probabilities."""
    tier = max(1, min(2, int(params.tier)))
    tier_hint = "tier1" if tier == 1 else "tier2"

    weight_text, weight_theme, scoring_mode, embedding_model, embedding_min_interval_sec = (
        _resolve_run_weights_and_i0(params)
    )

    api_key = resolve_openai_api_key_for_tier(tier)
    if not api_key:
        raise RuntimeError(
            "No OpenAI API key: set OPENAI_API_KEY or OPENAI_API_KEY_TIER1 / OPENAI_API_KEY_TIER2"
        )

    mapping_path = Path(params.mapping_path).expanduser().resolve()
    profiles_root = Path(params.profiles_root).expanduser().resolve()
    description_seed_path = Path(params.description_seed_path).expanduser().resolve()
    if not description_seed_path.is_file():
        raise FileNotFoundError(f"description.json not found: {description_seed_path}")

    group_text = load_tribe_description(description_seed_path)[0].strip()
    qual = copy.deepcopy(try_load_qualitative_from_description_json(description_seed_path))
    desc_label = str(description_seed_path)

    payload = coalesce_contextual_payload(dict(params.contextual_payload))
    if not str(payload.get("source_file") or "").strip():
        payload["source_file"] = "contextual_stimulus.json"

    theme_map = load_category_themes_map(mapping_path)

    all_posts = iter_posts_with_ground_truth(payload, tier_hint=tier_hint)
    if params.max_posts > 0:
        all_posts = all_posts[: params.max_posts]

    post_key_to_meta = _post_key_to_meta_for_posts(all_posts)
    contextual_label = str(payload.get("source_file") or "contextual")

    logger.info(
        "[initial_prediction] tier=%s contextual=%s posts_with_GT=%s description=%s",
        tier,
        contextual_label,
        len(all_posts),
        desc_label,
    )

    if params.print_seed_context:
        _log_seed_context(description_label=desc_label, group_text=group_text, qual=qual)

    description_path_for_prompts = description_seed_path

    if all_posts and not params.no_initial_i0_prompt:
        fpid, _fb, fpost = all_posts[0]
        _log_initial_prompt_preview(
            profiles_root=profiles_root,
            description_seed_path=description_path_for_prompts,
            group_summary=group_text,
            qualitative_summary=qual,
            profile_id=str(fpid),
            post=fpost,
            build_prompt=build_i0_initial_prediction_linkedin_prompt,
            load_card=lambda pid: load_profile_json(profiles_root, pid),
            extract_ctx=extract_individual_profile_for_i0,
            resolve_traits=resolve_group_traits_text,
        )

    base_url = os.environ.get("OPENAI_BASE_URL")
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=base_url if base_url and base_url.strip() else None,
        timeout=120.0,
    )
    sync_client = OpenAI(
        api_key=api_key,
        base_url=base_url if base_url and base_url.strip() else None,
        timeout=120.0,
    )

    sem = asyncio.Semaphore(max(1, int(params.concurrent)))
    emb_cache: Dict[str, Any] = {}
    emb_lock = Lock()
    delta_rows_lock = asyncio.Lock()
    all_delta_rows: List[Dict[str, Any]] = []

    user_pred_by_idx: Dict[str, Dict[int, Dict[str, Any]]] = {}
    pred_lock = asyncio.Lock()
    errors: List[Dict[str, Any]] = []
    err_lock = asyncio.Lock()
    prompt_print_lock = asyncio.Lock()
    completed_ok = 0
    done_lock = asyncio.Lock()

    checkpoint_every = max(0, int(params.checkpoint_every))

    s3_key_final: Optional[str] = None
    s3_url: Optional[str] = None
    partial_key: Optional[str] = None

    async def _maybe_upload_s3(art: Dict[str, Any], *, is_partial: bool) -> None:
        nonlocal s3_key_final, s3_url, partial_key
        if not params.s3_upload or not params.audience_room_id:
            return
        art_names = get_linkedin_tiered_s3_artifact_names()
        base = get_s3_key_for_audience(
            params.audience_room_id,
            art_names.tiered_folder,
            params.enterprise_name,
            params.source,
        ).rstrip("/")
        rel = art_names.initial_prediction_tier1 if tier == 1 else art_names.initial_prediction_tier2
        key_final = f"{base}/{rel}"
        payload_u = copy.deepcopy(art)
        if is_partial and params.s3_partial_upload:
            pkey = partial_s3_key_for_artifact(base, rel)
            partial_key = pkey
            upload_json_to_s3(pkey, payload_u)
            logger.info("[initial_prediction] S3 partial → %s", pkey)
            return
        url = upload_json_to_s3(key_final, payload_u)
        s3_key_final = key_final
        s3_url = url
        logger.info("[initial_prediction] S3 final → %s", key_final)
        update_manifest_initial_prediction(base, art_names.manifest, tier, url)
        if params.s3_partial_upload and _s3_client and _s3_bucket:
            try:
                _s3_client.delete_object(Bucket=_s3_bucket, Key=partial_s3_key_for_artifact(base, rel))
            except Exception:
                pass

    async def _flush_checkpoint(label: str) -> None:
        async with pred_lock:
            snap_pred = copy.deepcopy(user_pred_by_idx)
        async with err_lock:
            snap_err = copy.deepcopy(errors)
        art = build_initial_prediction_artifact(
            tier=tier,
            gen_model=params.gen_model,
            topic_model=params.topic_model,
            gt_prob_threshold=params.gt_prob_threshold,
            weight_text=weight_text,
            weight_theme=weight_theme,
            checkpoint_every=checkpoint_every,
            contextual_label=contextual_label,
            description_label=desc_label,
            mapping_label=str(mapping_path),
            profiles_root=profiles_root,
            model_type_used=params.model_type_used,
            user_pred_by_idx=snap_pred,
            errors=snap_err,
            extra_meta={"partial_checkpoint": True, "checkpoint_label": label},
        )
        json_text = json.dumps(art, ensure_ascii=False, indent=2)
        outp = Path(params.output_path).expanduser().resolve() if params.output_path else None
        if outp or params.mirror_repo_predictions_path:
            await asyncio.to_thread(
                write_prediction_json_local,
                json_text,
                outp,
                mirror_repo_predictions_path=params.mirror_repo_predictions_path,
            )
            if outp:
                logger.info("[initial_prediction] checkpoint %s → %s", label, outp)
        await _maybe_upload_s3(art, is_partial=True)

    async def _one(pid: str, _bundle: Dict[str, Any], post: Dict[str, Any]) -> None:
        nonlocal completed_ok
        _pk = (str(pid), str(post.get("post_id") or ""))
        ridx, rkey = post_key_to_meta.get(_pk, (0, f"{pid}_review_0"))
        post_id = str(post.get("post_id") or "")
        cat_list = category_themes_for_post(str(post.get("category") or ""), theme_map)
        try:
            rec = await run_audience_room_i0_for_post(
                review_key=rkey,
                profile_id=pid,
                review_idx=ridx,
                post=post,
                client=client,
                sync_client=sync_client,
                gen_model=params.gen_model,
                topic_model=params.topic_model,
                sem=sem,
                profiles_root=profiles_root,
                group_summary=group_text,
                qualitative_summary=qual,
                emb_cache=emb_cache,
                emb_lock=emb_lock,
                category_themes_list=cat_list,
                all_delta_rows=all_delta_rows,
                delta_rows_lock=delta_rows_lock,
                prob_threshold=params.prob_threshold,
                max_retries=params.max_retries,
                retry_delay=params.retry_delay,
                max_text_chars=params.max_text_chars,
                delta_rows_iter_prefix=f"initial_pred_t{tier}_",
                description_json_path=description_path_for_prompts,
                debug_i0_prompt=bool(params.debug_i0_prompt),
                gt_prob_threshold=params.gt_prob_threshold,
                weight_text=weight_text,
                weight_theme=weight_theme,
                print_i0_prompt=bool(params.print_every_i0_prompt),
                prompt_print_lock=prompt_print_lock,
                scoring_mode=scoring_mode,
                embedding_model=embedding_model,
                embedding_min_interval_sec=embedding_min_interval_sec,
            )
        except Exception as e:
            async with err_lock:
                errors.append(
                    {
                        "review_key": rkey,
                        "profile_id": pid,
                        "review_idx": ridx,
                        "post_id": post_id,
                        "error": str(e),
                        "error_type": type(e).__name__,
                    }
                )
            logger.exception("[initial_prediction] post failed review_key=%s post_id=%s", rkey, post_id)
            return

        async with pred_lock:
            user_pred_by_idx.setdefault(pid, {})[ridx] = rec

        async with done_lock:
            completed_ok += 1
            c = completed_ok
        if checkpoint_every > 0 and c % checkpoint_every == 0:
            await _flush_checkpoint(f"after {c} ok posts")

    await asyncio.gather(*[_one(pid, b, p) for pid, b, p in all_posts])

    artifact = build_initial_prediction_artifact(
        tier=tier,
        gen_model=params.gen_model,
        topic_model=params.topic_model,
        gt_prob_threshold=params.gt_prob_threshold,
        weight_text=weight_text,
        weight_theme=weight_theme,
        checkpoint_every=checkpoint_every,
        contextual_label=contextual_label,
        description_label=desc_label,
        mapping_label=str(mapping_path),
        profiles_root=profiles_root,
        model_type_used=params.model_type_used,
        user_pred_by_idx=user_pred_by_idx,
        errors=errors,
        extra_meta={
            "posts_completed_successfully": completed_ok,
            "posts_attempted": len(all_posts),
            "partial_checkpoint": False,
        },
    )

    json_text = json.dumps(artifact, ensure_ascii=False, indent=2)
    out_written: Optional[Path] = None
    outp = Path(params.output_path).expanduser().resolve() if params.output_path else None
    if outp or params.mirror_repo_predictions_path:
        await asyncio.to_thread(
            write_prediction_json_local,
            json_text,
            outp,
            mirror_repo_predictions_path=params.mirror_repo_predictions_path,
        )
        if outp:
            out_written = outp
            logger.info("[initial_prediction] wrote %s", outp)

    await _maybe_upload_s3(artifact, is_partial=False)

    if errors:
        logger.warning(
            '[initial_prediction] completed with %s failed post(s); see "errors" in artifact',
            len(errors),
        )

    return InitialPredictionRunResult(
        artifact=artifact,
        output_path_written=out_written,
        s3_key=s3_key_final,
        s3_url=s3_url,
        partial_s3_key=partial_key,
        posts_attempted=len(all_posts),
        posts_ok=completed_ok,
        posts_failed=len(errors),
    )


async def run_initial_prediction_posts_batch(
    params: InitialPredictionRunParams,
    *,
    all_posts_for_meta: List[Tuple[str, Dict[str, Any], Dict[str, Any]]],
    posts_to_run: List[Tuple[str, Dict[str, Any], Dict[str, Any]]],
    user_pred_by_idx: Dict[str, Dict[int, Dict[str, Any]]],
    errors: List[Dict[str, Any]],
) -> Tuple[int, int]:
    """
    Run i0 only for ``posts_to_run``, merging into ``user_pred_by_idx`` / ``errors``.

    ``all_posts_for_meta`` must be the full post list (same order as a full-room run) so
    ``review_idx`` / ``review_key`` match :func:`run_initial_prediction`.
    Returns ``(posts_ok_this_batch, len(posts_to_run))``.
    """
    tier = max(1, min(2, int(params.tier)))

    weight_text, weight_theme, scoring_mode, embedding_model, embedding_min_interval_sec = (
        _resolve_run_weights_and_i0(params)
    )

    api_key = resolve_openai_api_key_for_tier(tier)
    if not api_key:
        raise RuntimeError(
            "No OpenAI API key: set OPENAI_API_KEY or OPENAI_API_KEY_TIER1 / OPENAI_API_KEY_TIER2"
        )

    mapping_path = Path(params.mapping_path).expanduser().resolve()
    profiles_root = Path(params.profiles_root).expanduser().resolve()
    description_seed_path = Path(params.description_seed_path).expanduser().resolve()
    if not description_seed_path.is_file():
        raise FileNotFoundError(f"description.json not found: {description_seed_path}")

    group_text = load_tribe_description(description_seed_path)[0].strip()
    qual = copy.deepcopy(try_load_qualitative_from_description_json(description_seed_path))

    theme_map = load_category_themes_map(mapping_path)

    post_key_to_meta = _post_key_to_meta_for_posts(all_posts_for_meta)
    description_path_for_prompts = description_seed_path

    base_url = os.environ.get("OPENAI_BASE_URL")
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=base_url if base_url and base_url.strip() else None,
        timeout=120.0,
    )
    sync_client = OpenAI(
        api_key=api_key,
        base_url=base_url if base_url and base_url.strip() else None,
        timeout=120.0,
    )

    sem = asyncio.Semaphore(max(1, int(params.concurrent)))
    emb_cache: Dict[str, Any] = {}
    emb_lock = Lock()
    delta_rows_lock = asyncio.Lock()
    all_delta_rows: List[Dict[str, Any]] = []

    err_lock = asyncio.Lock()
    prompt_print_lock = asyncio.Lock()
    completed_ok = 0
    done_lock = asyncio.Lock()
    pred_lock = asyncio.Lock()

    async def _one(pid: str, _bundle: Dict[str, Any], post: Dict[str, Any]) -> None:
        nonlocal completed_ok
        _pk = (str(pid), str(post.get("post_id") or ""))
        ridx, rkey = post_key_to_meta.get(_pk, (0, f"{pid}_review_0"))
        post_id = str(post.get("post_id") or "")
        cat_list = category_themes_for_post(str(post.get("category") or ""), theme_map)
        try:
            rec = await run_audience_room_i0_for_post(
                review_key=rkey,
                profile_id=pid,
                review_idx=ridx,
                post=post,
                client=client,
                sync_client=sync_client,
                gen_model=params.gen_model,
                topic_model=params.topic_model,
                sem=sem,
                profiles_root=profiles_root,
                group_summary=group_text,
                qualitative_summary=qual,
                emb_cache=emb_cache,
                emb_lock=emb_lock,
                category_themes_list=cat_list,
                all_delta_rows=all_delta_rows,
                delta_rows_lock=delta_rows_lock,
                prob_threshold=params.prob_threshold,
                max_retries=params.max_retries,
                retry_delay=params.retry_delay,
                max_text_chars=params.max_text_chars,
                delta_rows_iter_prefix=f"initial_pred_t{tier}_",
                description_json_path=description_path_for_prompts,
                debug_i0_prompt=bool(params.debug_i0_prompt),
                gt_prob_threshold=params.gt_prob_threshold,
                weight_text=weight_text,
                weight_theme=weight_theme,
                print_i0_prompt=bool(params.print_every_i0_prompt),
                prompt_print_lock=prompt_print_lock,
                scoring_mode=scoring_mode,
                embedding_model=embedding_model,
                embedding_min_interval_sec=embedding_min_interval_sec,
            )
        except Exception as e:
            async with err_lock:
                errors.append(
                    {
                        "review_key": rkey,
                        "profile_id": pid,
                        "review_idx": ridx,
                        "post_id": post_id,
                        "error": str(e),
                        "error_type": type(e).__name__,
                    }
                )
            logger.exception("[initial_prediction batch] post failed review_key=%s post_id=%s", rkey, post_id)
            return

        async with pred_lock:
            user_pred_by_idx.setdefault(pid, {})[ridx] = rec

        async with done_lock:
            completed_ok += 1

    await asyncio.gather(*[_one(pid, b, p) for pid, b, p in posts_to_run])

    return completed_ok, len(posts_to_run)


def _log_seed_context(
    *,
    description_label: str,
    group_text: str,
    qual: Dict[str, Any],
) -> None:
    bh = "=" * 72
    logger.info("%s\n[SEED CONTEXT] description=%s\n%s", bh, description_label, bh)
    gs_preview = group_text if len(group_text) <= 4000 else group_text[:4000] + "\n… [truncated]"
    logger.info("[GROUP_SUMMARY]\n%s", gs_preview)
    qdump = json.dumps(qual, ensure_ascii=False, indent=2)
    qprev = qdump if len(qdump) <= 6000 else qdump[:6000] + "\n… [truncated]"
    logger.info("[QUALITATIVE_SUMMARY]\n%s", qprev)


def _log_initial_prompt_preview(
    *,
    profiles_root: Path,
    description_seed_path: Path,
    group_summary: str,
    qualitative_summary: Dict[str, Any],
    profile_id: str,
    post: Dict[str, Any],
    build_prompt: Any,
    load_card: Any,
    extract_ctx: Any,
    resolve_traits: Any,
) -> None:
    card = load_card(profile_id)
    individual_ctx = extract_ctx(card, profile_id=profile_id)
    group_traits_text = resolve_traits(description_seed_path, qualitative_summary)
    stim = (post.get("stimulus") or "").strip()
    category = str(post.get("category") or "")
    prompt = build_prompt(
        group_summary=group_summary,
        group_traits_text=group_traits_text,
        individual_ctx=individual_ctx,
        stimulus=stim,
        category=category,
    )
    pid = post.get("post_id")
    logger.info(
        "[initial_prediction] prompt preview (first post) profile_id=%s post_id=%s\n%s",
        profile_id,
        pid,
        prompt,
    )
