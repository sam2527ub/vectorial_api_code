"""CLI/local orchestration: S3 fetch → temp workspace → ``run_initial_prediction`` (not used by the HTTP app)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from app import database
from app.utils.s3_utils import get_audience_type_from_source

from app.services.linkedin_initial_prediction_service.errors import InitialPredictionError
from app.services.linkedin_initial_prediction_service.initial_prediction_config import (
    get_initial_prediction_config,
)
from app.services.linkedin_initial_prediction_service.initial_prediction_processor import (
    run_initial_prediction,
)
from app.services.linkedin_initial_prediction_service.initial_prediction_s3 import (
    download_room_profiles_from_s3,
    fetch_s3_inputs_for_initial_prediction,
    profile_ids_from_contextual_payload,
)
from app.services.linkedin_initial_prediction_service.initial_prediction_types import (
    InitialPredictionRunParams,
    InitialPredictionRunResult,
)


async def run_initial_prediction_for_linkedin_room(
    audience_room_id: str,
    *,
    enterprise_name: Optional[str] = None,
    tier: int = 1,
    model: Optional[str] = None,
    source: Optional[str] = None,
    s3_upload: bool = True,
    no_s3_partial: bool = False,
    print_seed_context: bool = False,
    debug_i0_prompt: bool = False,
) -> Dict[str, Any]:
    """
    Load artifacts from S3, run full-room initial prediction, upload result JSON.

    For production-scale runs use ``POST .../linkedin-initial-prediction/async``.
    Requires ground-truth extraction to have completed for the chosen tier.
    """
    room = database.find_audience_room_by_id(
        audience_room_id, include_profiles=False, enterprise_name=enterprise_name
    )
    if not room:
        raise InitialPredictionError(f"Audience room {audience_room_id!r} not found")
    if get_audience_type_from_source(room.source) != "linkedin-audience":
        return {
            "audience_room_id": audience_room_id,
            "skipped": True,
            "reason": "not_linkedin_audience",
            "source": room.source,
        }

    cfg = get_initial_prediction_config()
    tier_n = max(1, min(2, int(tier)))
    mdl = model or cfg.default_gen_model

    try:
        stim, mapping, desc = fetch_s3_inputs_for_initial_prediction(
            audience_room_id=audience_room_id,
            tier=tier_n,
            enterprise_name=enterprise_name,
            source=source if source is not None else room.source,
        )
    except FileNotFoundError as e:
        raise InitialPredictionError(str(e)) from e

    td: Optional[tempfile.TemporaryDirectory] = None
    try:
        td = tempfile.TemporaryDirectory(prefix="linkedin_initial_pred_")
        root = Path(td.name)
        dp = root / "description.json"
        mp = root / "mapping.json"
        dp.write_text(json.dumps(desc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        mp.write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        profiles_root = root / "profiles"
        ids = profile_ids_from_contextual_payload(stim)
        download_room_profiles_from_s3(
            audience_room_id=audience_room_id,
            profile_ids=ids,
            profiles_root=profiles_root,
            enterprise_name=enterprise_name,
            source=source if source is not None else room.source,
        )

        params = InitialPredictionRunParams(
            tier=tier_n,
            contextual_payload=stim,
            description_seed_path=dp,
            mapping_path=mp,
            profiles_root=profiles_root,
            output_path=None,
            mirror_repo_predictions_path=None,
            gen_model=mdl,
            topic_model=mdl,
            gt_prob_threshold=cfg.gt_prob_threshold,
            checkpoint_every=cfg.checkpoint_every,
            concurrent=cfg.max_concurrent_posts,
            prob_threshold=cfg.prob_threshold,
            max_retries=cfg.max_retries,
            retry_delay=cfg.retry_delay_sec,
            max_text_chars=cfg.max_text_chars,
            print_seed_context=print_seed_context,
            debug_i0_prompt=debug_i0_prompt,
            no_initial_i0_prompt=True,
            s3_upload=s3_upload,
            s3_partial_upload=not no_s3_partial,
            audience_room_id=audience_room_id,
            enterprise_name=enterprise_name,
            source=source if source is not None else room.source,
        )

        result: InitialPredictionRunResult = await run_initial_prediction(params)
        out: Dict[str, Any] = {
            "audience_room_id": audience_room_id,
            "tier": tier_n,
            "model": mdl,
            "config": cfg.to_public_dict(),
            "posts_attempted": result.posts_attempted,
            "posts_ok": result.posts_ok,
            "posts_failed": result.posts_failed,
            "s3_key": result.s3_key,
            "s3_url": result.s3_url,
            "artifact": result.artifact,
        }
        return out
    finally:
        if td is not None:
            td.cleanup()
