# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Context window budget management.

Provides model-aware token budgets so LLM calls never exceed the
model's practical context limit. Each model in config/models.yaml
has a context_budget field; models without it fall back to 2048.
"""

import logging
from dataclasses import dataclass
from dataclasses import field

import config

_log = logging.getLogger(__name__)

_DEFAULT_BUDGET = 2048


def get_budget(model_alias: str) -> int:
    """Read context_budget from models.yaml. Falls back to 2048."""
    try:
        cfg = config.get_model_config(model_alias)
        budget = cfg.get("context_budget")
        if budget is not None:
            return int(budget)
    except (ValueError, KeyError):
        pass
    _log.warning(
        "No context_budget for model '%s', using default %d. "
        "Set context_budget in config/models.yaml for better results.",
        model_alias,
        _DEFAULT_BUDGET,
    )
    return _DEFAULT_BUDGET


@dataclass
class ContentBudget:
    """Token budget split into named slots with priorities."""

    slots: dict[str, int] = field(default_factory=dict)
    total: int = 0

    def get(self, name: str) -> int:
        return self.slots.get(name, 0)


def allocate(budget: int, reserves: dict[str, float]) -> ContentBudget:
    """Split total budget into named slots by fractional priority.

    reserves maps slot names to fractions of the budget (should sum to <= 1.0).
    Example: allocate(8000, {"system": 0.1, "context": 0.15, "history": 0.65, "response": 0.1})
    """
    slots = {}
    for name, fraction in reserves.items():
        slots[name] = int(budget * fraction)
    return ContentBudget(slots=slots, total=budget)


def estimate_tokens(text: str) -> int:
    """Fast token approximation without tokenizer dependency.

    English averages ~4 chars/token; CJK averages ~3 chars/token.
    Checks first 200 chars to estimate CJK ratio.
    """
    if not text:
        return 0
    sample = text[:200]
    cjk_count = sum(
        1
        for c in sample
        if "\u4e00" <= c <= "\u9fff" or "\u3040" <= c <= "\u30ff" or "\uac00" <= c <= "\ud7af"
    )
    cjk_ratio = cjk_count / max(len(sample), 1)
    divisor = 3 if cjk_ratio > 0.3 else 4
    return max(1, len(text) // divisor)


def truncate_messages(messages: list[dict], max_tokens: int) -> list[dict]:
    """Trim conversation history from the middle to fit within max_tokens.

    Keeps the first message (system prompt) and last N turns.
    Drops middle turns until the total fits.
    """
    if not messages:
        return messages

    total = sum(estimate_tokens(m.get("content", "")) for m in messages)
    if total <= max_tokens:
        return messages

    result = list(messages)
    first = result[0] if result else None
    if not first:
        return result

    keep_first = result[:1]
    rest = result[1:]

    while (
        rest and sum(estimate_tokens(m.get("content", "")) for m in keep_first + rest) > max_tokens
    ):
        if len(rest) <= 2:
            break
        rest.pop(0)

    truncated = keep_first + rest
    if len(truncated) < len(messages):
        dropped = len(messages) - len(truncated)
        _log.warning("Truncated %d messages from conversation history to fit budget", dropped)
    return truncated


def truncate_text(text: str, max_tokens: int) -> str:
    """Hard truncate with sentence-boundary awareness."""
    if not text:
        return text

    tokens_est = estimate_tokens(text)
    if tokens_est <= max_tokens:
        return text

    char_limit = max_tokens * 4
    truncated = text[:char_limit]

    last_period = truncated.rfind(". ")
    last_newline = truncated.rfind("\n")
    boundary = max(last_period, last_newline)

    if boundary > char_limit * 0.5:
        truncated = truncated[: boundary + 1]

    return truncated.rstrip()
