"""Normalize LinkedIn post/comment text for tier exports (emoji, math symbols, whitespace).

Used by linkedin_post_tier_service: tier_builder runs clean_tiered_blob on aggregated tier JSON
before S3 upload; tier file basenames are in ``app/linkedin_tiered_s3/artifacts.yaml``. Objects ship with
normalized text. This is the production source of truth for that normalization.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict

# Emoji / pictograph ranges (U+2190–U+21FF arrows kept for LinkedIn bullet arrows)
_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "\U00002700-\U000027BF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\U00002600-\U000026FF"
    "\U00002300-\U000023FF"
    "\U0000200D"
    "\U0000FE0F"
    "\U0001F000-\U0001F02F"
    "]+",
    flags=re.UNICODE,
)

_GREEK_NAME_TO_ASCII = {
    "ALPHA": "alpha",
    "BETA": "beta",
    "GAMMA": "gamma",
    "DELTA": "delta",
    "EPSILON": "epsilon",
    "ZETA": "zeta",
    "ETA": "eta",
    "THETA": "theta",
    "IOTA": "iota",
    "KAPPA": "kappa",
    "LAMDA": "lambda",
    "MU": "mu",
    "NU": "nu",
    "XI": "xi",
    "OMICRON": "omicron",
    "PI": "pi",
    "RHO": "rho",
    "SIGMA": "sigma",
    "TAU": "tau",
    "UPSILON": "upsilon",
    "PHI": "phi",
    "CHI": "chi",
    "PSI": "psi",
    "OMEGA": "omega",
}

_DIGIT_NAMES = {
    "ZERO": "0",
    "ONE": "1",
    "TWO": "2",
    "THREE": "3",
    "FOUR": "4",
    "FIVE": "5",
    "SIX": "6",
    "SEVEN": "7",
    "EIGHT": "8",
    "NINE": "9",
}


def _math_alphanumeric_to_ascii(ch: str) -> str:
    cp = ord(ch)
    if not (0x1D400 <= cp <= 0x1D7FF):
        return ch
    try:
        name = unicodedata.name(ch)
    except ValueError:
        return ""
    if "MATHEMATICAL" not in name:
        return ch
    parts = name.split()
    if "DIGIT" in name:
        for word, digit in _DIGIT_NAMES.items():
            if word in parts:
                return digit
        return ""
    last = parts[-1]
    if last in _GREEK_NAME_TO_ASCII:
        w = _GREEK_NAME_TO_ASCII[last]
        return w.upper() if "CAPITAL" in name else w
    if "CAPITAL" in name:
        for p in reversed(parts):
            if len(p) == 1 and "A" <= p.upper() <= "Z":
                return p.upper()
    if "SMALL" in name:
        for p in reversed(parts):
            if len(p) == 1 and "A" <= p.upper() <= "Z":
                return p.lower()
    return ""


def normalize_math_string(s: str) -> str:
    return "".join(_math_alphanumeric_to_ascii(c) for c in s)


def strip_emojis(s: str) -> str:
    return _EMOJI_RE.sub("", s)


def clean_post_text(
    text: str,
    *,
    strip_emoji: bool = True,
    normalize_math: bool = True,
    nbsp_to_space: bool = True,
) -> str:
    if not text:
        return ""
    s = text
    if normalize_math:
        s = normalize_math_string(s)
    if strip_emoji:
        s = strip_emojis(s)
    if nbsp_to_space:
        s = s.replace("\u00a0", " ")
    s = s.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _normalize_interaction_row(row: Any, **kwargs: Any) -> Any:
    """Tier 2/3 row: ensure stimulus + response keys and clean text (math fonts, emoji, etc.)."""
    if not isinstance(row, dict):
        return row
    q = dict(row)
    if "stimulus" not in q and "original_post" in q:
        q["stimulus"] = q.pop("original_post")
    if "response" not in q and "user_text" in q:
        q["response"] = q.pop("user_text")
    if "stimulus" in q and isinstance(q["stimulus"], str):
        q["stimulus"] = clean_post_text(q["stimulus"], **kwargs)
    if "response" in q and isinstance(q["response"], str):
        q["response"] = clean_post_text(q["response"], **kwargs)
    return q


def clean_tiered_blob(data: Any, **kwargs: Any) -> Dict[str, Any]:
    """
    Tier JSON: profile_id -> block with optional posts[] and/or interactions[].

    Normalizes every posts[].text and interactions[].stimulus/response string in the tier export.
    """
    if not isinstance(data, dict):
        raise TypeError("Root JSON value must be an object")
    out: Dict[str, Any] = {}
    for pid, block in data.items():
        if not isinstance(block, dict):
            out[pid] = block
            continue
        new_block = dict(block)
        posts = block.get("posts")
        if isinstance(posts, list):
            new_posts = []
            for p in posts:
                if isinstance(p, dict) and "text" in p:
                    q = dict(p)
                    q["text"] = clean_post_text(p.get("text") or "", **kwargs)
                    new_posts.append(q)
                else:
                    new_posts.append(p)
            new_block["posts"] = new_posts
        interactions = block.get("interactions")
        if isinstance(interactions, list):
            new_block["interactions"] = [
                _normalize_interaction_row(r, **kwargs) for r in interactions
            ]
        out[pid] = new_block
    return out
