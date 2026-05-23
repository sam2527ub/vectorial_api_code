"""Tier pipeline ordering for LinkedIn SGO."""

from app.services.sgo_fargate.sgo_tier_pipeline import normalize_tier_modes


def test_normalize_tier_modes_default_both():
    assert normalize_tier_modes(None) == ["tier1", "tier2"]
    assert normalize_tier_modes("both") == ["tier1", "tier2"]


def test_normalize_tier_modes_single():
    assert normalize_tier_modes("tier1") == ["tier1"]
    assert normalize_tier_modes("tier2") == ["tier2"]
