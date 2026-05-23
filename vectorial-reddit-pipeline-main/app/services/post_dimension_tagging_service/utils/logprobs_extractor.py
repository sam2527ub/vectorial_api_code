"""Utilities for extracting logprobs from OpenAI API responses."""
from typing import Dict, Any, List


def extract_dimension_logprobs(
    dimension_values: Dict[str, str],
    logprobs_content: List[Any],
    response_text: str,
    dimensions: List[str]
) -> Dict[str, Dict[str, float]]:
    """
    Extract logprobs for each dimension's yes/no value (OpenAI response shape).
    
    Args:
        dimension_values: Dict mapping dimension name -> "yes"/"no"
        logprobs_content: List of logprob tokens from OpenAI API response
        response_text: The JSON response text
        dimensions: List of dimension names
        
    Returns:
        Dict mapping dimension name -> {"yes": logprob, "no": logprob}
    """
    result = {}
    char_to_token_index = {}
    current_char_pos = 0
    
    # Build character to token index mapping
    for token_idx, token_data in enumerate(logprobs_content):
        token = getattr(token_data, "token", None)
        if token is None:
            continue
        token_length = len(token)
        for i in range(token_length):
            char_to_token_index[current_char_pos + i] = token_idx
        current_char_pos += token_length
    
    # Extract logprobs for each dimension
    for dim_name in dimensions:
        value = dimension_values.get(dim_name, "no").lower()
        if value not in ["yes", "no"]:
            value = "no"
        
        dim_logprobs = {"yes": -10.0, "no": -10.0}
        dim_pattern = f'"{dim_name}"'
        dim_pos = response_text.find(dim_pattern)
        
        if dim_pos != -1:
            colon_pos = response_text.find(':', dim_pos)
            if colon_pos != -1:
                value_quote_start = response_text.find('"', colon_pos)
                if value_quote_start != -1:
                    value_quote_end = response_text.find('"', value_quote_start + 1)
                    if value_quote_end != -1:
                        actual_value = response_text[value_quote_start + 1:value_quote_end].lower()
                        value_start_char = value_quote_start + 1
                        
                        if value_start_char in char_to_token_index:
                            token_idx = char_to_token_index[value_start_char]
                            
                            if token_idx < len(logprobs_content):
                                token_data = logprobs_content[token_idx]
                                
                                if hasattr(token_data, "logprob"):
                                    logprob = token_data.logprob
                                    if actual_value == "yes":
                                        dim_logprobs["yes"] = logprob
                                    elif actual_value == "no":
                                        dim_logprobs["no"] = logprob
                                
                                if hasattr(token_data, "top_logprobs") and token_data.top_logprobs:
                                    for top_logprob in token_data.top_logprobs:
                                        top_token = getattr(top_logprob, "token", "").strip('"').strip("'").lower()
                                        top_logprob_val = getattr(top_logprob, "logprob", -10.0)
                                        if top_token == "yes" and top_logprob_val > dim_logprobs["yes"]:
                                            dim_logprobs["yes"] = top_logprob_val
                                        elif top_token == "no" and top_logprob_val > dim_logprobs["no"]:
                                            dim_logprobs["no"] = top_logprob_val
                        
                        # Fallback: search nearby tokens if not found
                        if dim_logprobs["yes"] == -10.0 and dim_logprobs["no"] == -10.0:
                            search_start = max(0, token_idx - 5)
                            search_end = min(len(logprobs_content), token_idx + 5)
                            for i in range(search_start, search_end):
                                if i < len(logprobs_content):
                                    token_data = logprobs_content[i]
                                    token = getattr(token_data, "token", "").strip('"').strip("'").lower()
                                    logprob = getattr(token_data, "logprob", -10.0)
                                    if token == "yes" and logprob > dim_logprobs["yes"]:
                                        dim_logprobs["yes"] = logprob
                                    elif token == "no" and logprob > dim_logprobs["no"]:
                                        dim_logprobs["no"] = logprob
                                    if hasattr(token_data, "top_logprobs") and token_data.top_logprobs:
                                        for top_logprob in token_data.top_logprobs:
                                            top_token = getattr(top_logprob, "token", "").strip('"').strip("'").lower()
                                            top_logprob_val = getattr(top_logprob, "logprob", -10.0)
                                            if top_token == "yes" and top_logprob_val > dim_logprobs["yes"]:
                                                dim_logprobs["yes"] = top_logprob_val
                                            elif top_token == "no" and top_logprob_val > dim_logprobs["no"]:
                                                dim_logprobs["no"] = top_logprob_val
        
        result[dim_name] = dim_logprobs
    
    return result
