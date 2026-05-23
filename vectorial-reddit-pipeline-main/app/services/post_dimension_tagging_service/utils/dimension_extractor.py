"""Dimension extraction with confidence scoring from OpenAI responses."""
import math
from typing import Dict, Any
from app.config import logger
from .logprobs_extractor import extract_dimension_logprobs


class DimensionExtractor:
    """Extracts dimensions with confidence scores from AI gateway responses (OpenAI response shape)."""

    @staticmethod
    def extract_section_values(
        post: Dict[str, Any],
        values: Dict[str, str],
        chat_completion: Any,
        response_text: str,
        keys: list,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Extract dimensions with confidence scores from logprobs.

        Args:
            post: Post dictionary
            values: Parsed yes/no values from JSON
            chat_completion: Chat completion object (gateway/direct API response)
            response_text: Response text string
            keys: List of keys to extract

        Returns:
            Dict mapping key -> {"value": "yes/no", "confidence": float}
        """
        logprobs_data = getattr(chat_completion.choices[0], "logprobs", None)
        extracted_result = {}

        if logprobs_data and logprobs_data.content:
            dimension_logprobs = extract_dimension_logprobs(
                values,
                logprobs_data.content,
                response_text,
                keys,
            )

            for key in keys:
                value = values.get(key, "no").lower()
                if value not in ["yes", "no"]:
                    value = "no"

                logprob = dimension_logprobs.get(key, {}).get(value, -10.0)
                confidence = math.exp(logprob) if logprob > -50 else 0.0

                extracted_result[key] = {
                    "value": value,
                    "confidence": round(confidence, 4),
                }
        else:
            logger.debug(f"No logprobs available for post {post.get('id', 'unknown')}")
            for key in keys:
                value = values.get(key, "no").lower()
                if value not in ["yes", "no"]:
                    value = "no"
                extracted_result[key] = {
                    "value": value,
                    "confidence": 0.5,
                }

        return extracted_result

    @staticmethod
    def extract_dimensions(
        post: Dict[str, Any],
        dimension_values: Dict[str, str],
        chat_completion: Any,
        response_text: str,
        dimensions: list,
    ) -> Dict[str, Dict[str, Any]]:
        """Backward-compatible dimensions extraction wrapper."""
        return DimensionExtractor.extract_section_values(
            post=post,
            values=dimension_values,
            chat_completion=chat_completion,
            response_text=response_text,
            keys=dimensions,
        )

    @staticmethod
    def create_fallback_result(post: Dict[str, Any], dimensions: list) -> Dict[str, Any]:
        """Create fallback result when processing fails."""
        return {
            "post": post,
            "dimensions": {
                dim: {"value": "no", "confidence": 0.0}
                for dim in dimensions
            },
        }
