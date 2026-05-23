"""Tier pipeline ordering for LinkedIn SGO."""

from unittest.mock import AsyncMock, patch

from app.services.sgo_fargate.fargate_resume import assess_fargate_pipeline_resume
from app.services.sgo_fargate.sgo_tier_pipeline import normalize_tier_modes


def test_normalize_tier_modes_default_both():
    assert normalize_tier_modes(None) == ["tier1", "tier2"]
    assert normalize_tier_modes("both") == ["tier1", "tier2"]


def test_normalize_tier_modes_single():
    assert normalize_tier_modes("tier1") == ["tier1"]
    assert normalize_tier_modes("tier2") == ["tier2"]


def test_assess_resume_tier2_starts_after_completed_tier1():
    jobs = {
        "tier1": {
            "job_id": "t1",
            "job_type": "linkedin_sgo_pipeline",
            "audience_room_id": "room-1",
            "status": "COMPLETED",
            "result": {"tier_mode": "tier1", "num_iterations": 5},
        },
        "tier2": {
            "job_id": "t2",
            "job_type": "linkedin_sgo_pipeline",
            "audience_room_id": "room-1",
            "status": "PENDING",
            "result": {"tier_mode": "tier2", "num_iterations": 5, "outer_iteration_next": 1},
        },
    }

    def _load(job_id, **_kwargs):
        return jobs["tier1"] if job_id == "t1" else jobs["tier2"]

    with patch(
        "app.services.sgo_fargate.fargate_resume._base_tiered_for_room",
        return_value="rooms/x/tiered",
    ), patch(
        "app.services.sgo_fargate.fargate_resume._load_tier_job",
        side_effect=lambda job_id, **kw: _load(job_id),
    ), patch(
        "app.services.sgo_fargate.fargate_resume.assess_sgo_job_resume",
        return_value=(False, "no_checkpoint", {"has_resume_checkpoint": False}),
    ):
        out = assess_fargate_pipeline_resume(
            audience_room_id="room-1",
            tier1_job_id="t1",
            tier2_job_id="t2",
            force=True,
        )

    assert out["can_resume"] is True
    assert out["resume_tier"] == "tier2"
    assert out["tier_results"][1]["reason"] == "start_after_tier1"


def test_tier_pipeline_auto_starts_tier2_after_tier1():
    import asyncio

    from app.services.sgo_fargate import sgo_tier_pipeline as mod

    mark_calls = []

    def _mark(**kwargs):
        mark_calls.append(kwargs)

    async def _run():
        with patch.object(mod, "_mark_tier_job_status", side_effect=_mark), patch.object(
            mod,
            "_mark_tier2_waiting_for_tier1",
        ), patch.object(
            mod._handler,
            "trigger_async_linkedin_sgo_pipeline",
            side_effect=[
                {"job_id": "t1", "poll": "/poll/t1"},
                {"job_id": "t2", "poll": "/poll/t2"},
            ],
        ), patch.object(
            mod,
            "run_sgo_training_loop",
            new=AsyncMock(
                side_effect=[
                    {"status": "COMPLETED"},
                    {"status": "COMPLETED"},
                ]
            ),
        ):
            return await mod.run_sgo_tier_pipeline(
                audience_room_id="room-1",
                tier_mode="both",
                num_iterations=5,
                tier_job_ids={"tier1": "t1", "tier2": "t2"},
            )

    out = asyncio.run(_run())

    assert out["status"] == "COMPLETED"
    assert len(out["tier_results"]) == 2
    assert any(c.get("job_id") == "t2" and c.get("status") == "PROCESSING" for c in mark_calls)

