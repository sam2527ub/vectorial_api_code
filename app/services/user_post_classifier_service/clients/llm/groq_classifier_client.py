"""Groq LLM client for post classification."""
import asyncio
import os
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from app.config import groq_client, logger

from ...utils.prompt_builder import build_classifier_prompts
from ...utils.json_parser import parse_classifier_response
from ...utils.response_matcher import match_classifications_to_posts
from ...utils.response_normalizer import normalize_and_pad
from .interface import PostClassifierClientInterface


class GroqPostClassifierClient(PostClassifierClientInterface):
    """Groq implementation for post classification (single call, retries, normalize)."""

    def is_configured(self) -> bool:
        return groq_client is not None

    async def classify_posts_single_call(
        self,
        posts_texts: List[str],
        classifier_name: str,
        classifier_prompt: str,
        classifier_description: str,
        classifier_labels: List[str],
        classifier_examples: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        if not groq_client:
            raise HTTPException(status_code=503, detail="Groq client not initialized. Set GROQ_API_KEY.")
        if not posts_texts:
            return []

        system_prompt, user_prompt = build_classifier_prompts(
            posts_texts, classifier_name, classifier_prompt,
            classifier_description, classifier_labels, classifier_examples,
        )
        num_posts = len(posts_texts)
        model_name = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
        max_retries = 5
        max_count_mismatch_retries = 2
        base_delay = 2

        classifications = None
        for attempt in range(max_retries):
            count_mismatch_retry = 0
            while count_mismatch_retry <= max_count_mismatch_retries:
                try:
                    temp = 0.1 if count_mismatch_retry == 0 else 0.1 + (count_mismatch_retry * 0.1)
                    response = groq_client.chat.completions.create(
                        model=model_name,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        temperature=temp,
                        response_format={"type": "json_object"},
                    )
                    content = response.choices[0].message.content or ""
                    raw_list = parse_classifier_response(content)
                    if len(raw_list) != num_posts and count_mismatch_retry < max_count_mismatch_retries:
                        count_mismatch_retry += 1
                        await asyncio.sleep(0.5)
                        continue
                    if len(raw_list) != num_posts:
                        raw_list = match_classifications_to_posts(raw_list, num_posts, classifier_labels)
                    classifications = normalize_and_pad(raw_list, num_posts, classifier_labels)
                    break
                except HTTPException:
                    raise
                except Exception as e:
                    err_lower = str(e).lower()
                    if "tokens per day" in err_lower or "tpd" in err_lower or "token quota" in err_lower:
                        raise HTTPException(
                            status_code=429,
                            detail={
                                "error": "token_quota_exceeded",
                                "message": "Daily token quota limit reached for Groq API.",
                                "type": "token_quota",
                                "suggestion": "Wait for daily reset or upgrade plan.",
                                "groq_error": str(e),
                            },
                        )
                    if count_mismatch_retry < max_count_mismatch_retries:
                        count_mismatch_retry += 1
                        await asyncio.sleep(0.5)
                        continue
                    raise
            if classifications:
                break
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning(f"Retry {attempt + 1}/{max_retries}, waiting {delay}s")
                await asyncio.sleep(delay)

        if not classifications:
            raise HTTPException(status_code=500, detail="Failed to get classifications from API")
        return classifications
