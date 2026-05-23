"""
Build OpenAI Chat Completions kwargs per model family.

- **o-series (o1, o3, o4):** ``max_completion_tokens``; no ``temperature`` or ``response_format``.
- **gpt-4o-mini / gpt-4*:** ``max_tokens``, ``temperature``, optional ``response_format`` / ``logprobs``.
- **gpt-5*:** ``max_completion_tokens``; ``reasoning_effort`` on gpt-5.2 when requested.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def normalize_model_name(model: str) -> str:
    return (model or "").strip().split("/")[-1]


def is_o_series_model(model: str) -> bool:
    m = normalize_model_name(model).lower()
    return any(m.startswith(p) for p in ("o1", "o3", "o4"))


def is_gpt5_family(model: str) -> bool:
    return "gpt-5" in normalize_model_name(model).lower()


def uses_max_completion_tokens(model: str) -> bool:
    return is_o_series_model(model) or is_gpt5_family(model)


def supports_temperature(model: str) -> bool:
    return not is_o_series_model(model) and not is_gpt5_family(model)


def default_reasoning_effort(model: str) -> Optional[str]:
    """gpt-5.2 refine/memory calls use medium reasoning unless overridden."""
    if "gpt-5.2" in normalize_model_name(model).lower():
        return "medium"
    return None


def supports_response_format_json(model: str) -> bool:
    return not is_o_series_model(model)


def model_supports_logprobs(model_name: str) -> bool:
    """Whether the model accepts ``logprobs`` on chat.completions (e.g. gpt-4o-mini)."""
    if is_o_series_model(model_name):
        return False
    m = normalize_model_name(model_name).lower()
    return any(x in m for x in ("gpt-4", "gpt-3.5", "gpt-4o", "gpt-4-turbo", "gpt-oss", "oss"))


def build_chat_completion_kwargs(
    model: str,
    messages: List[Dict[str, str]],
    *,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    json_mode: bool = False,
    logprobs: bool = False,
    top_logprobs: Optional[int] = None,
    reasoning_effort: Optional[str] = None,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Return kwargs for ``client.chat.completions.create(**kwargs)`` (sync or async).

    ``json_mode``: request JSON object output. o-series rely on prompt wording (no ``response_format``).
    """
    kwargs: Dict[str, Any] = {"model": model, "messages": messages}

    if uses_max_completion_tokens(model):
        if max_tokens is not None:
            kwargs["max_completion_tokens"] = max_tokens
    elif max_tokens is not None:
        kwargs["max_tokens"] = max_tokens

    if temperature is not None and supports_temperature(model):
        kwargs["temperature"] = temperature

    if json_mode and supports_response_format_json(model):
        kwargs["response_format"] = {"type": "json_object"}

    if logprobs and model_supports_logprobs(model):
        kwargs["logprobs"] = True
        if top_logprobs is not None:
            kwargs["top_logprobs"] = top_logprobs

    effort = reasoning_effort or default_reasoning_effort(model)
    if effort and "gpt-5.2" in normalize_model_name(model).lower():
        kwargs["reasoning_effort"] = effort

    if timeout is not None:
        kwargs["timeout"] = timeout

    return kwargs


def apply_json_chat_payload(
    request_params: Dict[str, Any],
    model: str,
    *,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Add JSON-completion fields to an existing ``request_params`` dict (model + messages already set).

    o-series: no ``response_format`` or ``temperature``; optional ``max_completion_tokens``.
    gpt-5.2: ``max_completion_tokens``, ``reasoning_effort``; no temperature.
    gpt-4o-mini and similar: ``response_format``, ``max_tokens``, ``temperature``.
    """
    if is_o_series_model(model) or is_gpt5_family(model):
        if max_tokens is not None:
            request_params["max_completion_tokens"] = max_tokens
        effort = default_reasoning_effort(model)
        if effort:
            request_params["reasoning_effort"] = effort
        if is_gpt5_family(model) and not is_o_series_model(model):
            request_params["response_format"] = {"type": "json_object"}
    else:
        request_params["response_format"] = {"type": "json_object"}
        if max_tokens is not None:
            request_params["max_tokens"] = max_tokens
        if temperature is not None:
            request_params["temperature"] = temperature
    return request_params
