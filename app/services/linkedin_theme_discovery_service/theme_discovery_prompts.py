"""
Prompt text for MAP / SHRINK / CATEGORIZE lives in ``prompts/*.txt`` (same style as
``post_topic_classification/prompts/*`` in scripts).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


@lru_cache
def _read(name: str) -> str:
    p = PROMPT_DIR / f"{name}.txt"
    return p.read_text(encoding="utf-8")


def map_tuple_tier1() -> tuple[str, str, str, str]:
    """(round1_sys, round1_usr, subsequent_sys, subsequent_usr) for tier-1 authored posts."""
    return (
        _read("map_round1_system_tier1"),
        _read("map_round1_user_tier1"),
        _read("map_subsequent_system_tier1"),
        _read("map_subsequent_user_tier1"),
    )


def map_tuple_tier2() -> tuple[str, str, str, str]:
    """(round1_sys, round1_usr, subsequent_sys, subsequent_usr) for tier-2 interaction text."""
    return (
        _read("map_round1_system_tier2"),
        _read("map_round1_user_tier2"),
        _read("map_subsequent_system_tier2"),
        _read("map_subsequent_user_tier2"),
    )


def shrink_system() -> str:
    return _read("shrink_system")


def shrink_user() -> str:
    return _read("shrink_user")


def categorize_system() -> str:
    return _read("categorize_system")


def categorize_user_tier1() -> str:
    return _read("categorize_user_tier1")


def categorize_user_tier2() -> str:
    return _read("categorize_user_tier2")
