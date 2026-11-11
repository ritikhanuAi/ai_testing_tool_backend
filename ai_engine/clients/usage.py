"""Collect provider-reported LLM token usage within one scan task."""

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Mapping, Optional


@dataclass
class TokenUsage:
    calls: int = 0
    reported_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    provider: Optional[str] = None
    model: Optional[str] = None


_current_usage: ContextVar[Optional[TokenUsage]] = ContextVar("llm_token_usage", default=None)


def begin_token_usage(usage: TokenUsage) -> Token:
    """Make ``usage`` the collector for LLM calls in this async context."""
    return _current_usage.set(usage)


def end_token_usage(token: Token) -> None:
    _current_usage.reset(token)


def _first_count(data: Mapping[str, Any], *keys: str) -> Optional[int]:
    for key in keys:
        value = data.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and value >= 0:
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def record_token_usage(
    usage_data: Optional[Mapping[str, Any]],
    *,
    provider: str,
    model: str,
) -> None:
    """Record a successful model response when a scan collector is active."""
    usage = _current_usage.get()
    if usage is None:
        return

    usage.calls += 1
    usage.provider = provider or usage.provider
    usage.model = model or usage.model
    if not isinstance(usage_data, Mapping):
        return

    prompt = _first_count(
        usage_data,
        "prompt_tokens", "promptTokens", "input_tokens", "inputTokens",
        "prompt_token_count", "promptTokenCount", "total_input_tokens", "totalInputTokens",
    )
    completion = _first_count(
        usage_data,
        "completion_tokens", "completionTokens", "output_tokens", "outputTokens",
        "candidates_token_count", "candidatesTokenCount", "total_output_tokens", "totalOutputTokens",
    )
    total = _first_count(
        usage_data,
        "total_tokens", "totalTokens", "total_token_count", "totalTokenCount",
    )

    details = usage_data.get("completion_tokens_details") or usage_data.get("completionTokensDetails")
    reasoning = _first_count(usage_data, "thoughts_token_count", "thoughtsTokenCount", "total_thought_tokens", "totalThoughtTokens")
    if reasoning is None and isinstance(details, Mapping):
        reasoning = _first_count(details, "reasoning_tokens", "reasoningTokens", "thoughts_tokens", "thoughtsTokens")

    if prompt is None and completion is None and total is None and reasoning is None:
        return

    usage.reported_calls += 1
    usage.prompt_tokens += prompt or 0
    usage.completion_tokens += completion or 0
    usage.reasoning_tokens += reasoning or 0
    usage.total_tokens += total if total is not None else (prompt or 0) + (completion or 0)
