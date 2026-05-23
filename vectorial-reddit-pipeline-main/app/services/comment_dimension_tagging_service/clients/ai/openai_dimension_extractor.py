"""Dimension extraction with confidence scoring from OpenAI responses."""
import json
import math
from typing import Dict, Any
from app.config import logger
from ...utils.logprobs_extractor import extract_dimension_logprobs


class OpenAIDimensionExtractor:
    """
    Extracts dimensions with confidence scores from OpenAI API responses.
    
    Uses logprobs from OpenAI API to calculate confidence scores for each
    dimension's yes/no classification.
    """
    
    @staticmethod
    def extract_section_values(
        comment: Dict[str, Any],
        values: Dict[str, str],
        chat_completion: Any,
        response_text: str,
        keys: list,
        search_start: int = None,
        search_end: int = None
    ) -> Dict[str, Dict[str, Any]]:
        """
        Extract dimensions with confidence scores from logprobs.
        
        Args:
            comment: Comment dictionary
            values: Parsed yes/no values from JSON (key -> "yes"/"no")
            chat_completion: OpenAI chat completion object
            response_text: Response text string (full response for token mapping)
            keys: List of keys to extract
            search_start: Optional start position to limit search (for batch processing)
            search_end: Optional end position to limit search (for batch processing)
            
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
                search_start=search_start,
                search_end=search_end
            )
            
            for key in keys:
                value = values.get(key, "no").lower()
                if value not in ["yes", "no"]:
                    value = "no"
                
                logprob = dimension_logprobs.get(key, {}).get(value, -10.0)
                
                # Calculate confidence with better handling
                if logprob > -50:
                    # Valid logprob found
                    confidence = math.exp(logprob)
                    # Ensure minimum confidence for "yes" values (even if logprob is low)
                    if value == "yes" and confidence < 0.1:
                        confidence = 0.1
                elif value == "yes":
                    # Logprob extraction failed but dimension is marked as "yes"
                    # Use conservative minimum confidence instead of 0.0
                    confidence = 0.1
                    logger.debug(
                        f"Logprob extraction failed for {key}='yes' in comment "
                        f"{comment.get('id', 'unknown')}, using fallback confidence 0.1"
                    )
                else:
                    # Logprob extraction failed and value is "no"
                    confidence = 0.0
                
                extracted_result[key] = {
                    "value": value,
                    "confidence": round(confidence, 4)
                }
        else:
            logger.debug(f"No logprobs available for comment {comment.get('id', 'unknown')}")
            for key in keys:
                value = values.get(key, "no").lower()
                if value not in ["yes", "no"]:
                    value = "no"
                extracted_result[key] = {
                    "value": value,
                    "confidence": 0.5
                }
        
        return extracted_result

    @staticmethod
    def extract_dimensions(
        comment: Dict[str, Any],
        dimension_values: Dict[str, str],
        chat_completion: Any,
        response_text: str,
        dimensions: list,
        search_start: int = None,
        search_end: int = None
    ) -> Dict[str, Dict[str, Any]]:
        """Backward-compatible dimensions extraction wrapper."""
        return OpenAIDimensionExtractor.extract_section_values(
            comment=comment,
            values=dimension_values,
            chat_completion=chat_completion,
            response_text=response_text,
            keys=dimensions,
            search_start=search_start,
            search_end=search_end,
        )
    
    @staticmethod
    def create_fallback_result(
        comment: Dict[str, Any],
        dimensions: list
    ) -> Dict[str, Any]:
        """
        Create fallback result when processing fails.
        
        Args:
            comment: Comment dictionary
            dimensions: List of dimension names
            
        Returns:
            Dict with comment and default "no" dimensions with 0.0 confidence
        """
        return {
            "comment": comment,
            "dimensions": {
                dim: {"value": "no", "confidence": 0.0}
                for dim in dimensions
            }
        }
