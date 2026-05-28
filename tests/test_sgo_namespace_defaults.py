"""Namespace defaults for async/Fargate SGO runs."""

from app.services.linkedin_sgo_pipeline_service_async.linkedin_sgo_pipeline_async_config import (
    get_default_namespace,
)


def test_namespace_includes_shrink_defaults_without_yaml_keys():
    ns = get_default_namespace({"tier_mode": "tier1", "contextual_json": "/x"})
    assert getattr(ns, "group_summary_min_words") == 500
    assert getattr(ns, "group_summary_max_words") == 700
    assert getattr(ns, "max_traits_per_category") == 20
    assert getattr(ns, "shrink_floor_words") == 500
