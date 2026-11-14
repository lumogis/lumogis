# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""On-demand, on-box text refine for the email composer (LUM-684).

``refine_text`` rewrites the user's *own* composer body per a preset or a
free-form instruction, using the **local-first** LLM — the same on-box privacy
boundary as email classification (task ``email_compose_refine``,
:func:`email_classifier.require_local_model`):
the body is never sent to a cloud model. It is **best-effort** and never raises
(``""`` on any miss/error). Unlike the reply-draft, the input is the caller's own
text, so there is no signal-ownership chain.

Budgets are aligned so a *valid* body is never silently mutilated: the route caps
the request ``text`` at ~8000 chars (≈ the input token budget), refine truncates
defensively to :data:`_REFINE_INPUT_TOKENS`, and the output ceiling
:data:`_REFINE_OUTPUT_TOKENS` sits *above* the input budget so a "longer" rewrite
is not cut mid-sentence.
"""

from __future__ import annotations

import logging

from services.injection_sanitiser import neutralize_wrapper_breakout_literals

from services import context_budget
from services import email_classifier
from services import local_llm_task

_log = logging.getLogger(__name__)

# Server-owned preset → canonical instruction. The client sends a preset KEY (or a
# free-form instruction); the wording lives here (tunable, not client-spoofable).
PRESET_INSTRUCTIONS: dict[str, str] = {
    "formal": "Rewrite the text in a more formal, professional tone.",
    "shorter": "Make the text shorter and more concise while keeping its meaning.",
    "longer": "Expand the text with a little more detail, keeping the same intent.",
    "clearer": "Rewrite the text to be clearer and easier to read.",
    "friendlier": "Rewrite the text in a warmer, friendlier tone.",
    "fix_grammar": ("Fix spelling, grammar, and punctuation without changing the meaning or tone."),
}

# Aligned with the route's `text` max_length (~8000 chars ≈ 2000 tokens) so a valid
# body is fully rewritten, never truncated; output ceiling has headroom above the
# input budget for a "longer" rewrite.
_REFINE_INPUT_TOKENS = 2000
_REFINE_OUTPUT_TOKENS = 3000
# A full-body rewrite is heavier than a short reply draft, so allow a bit longer.
_REFINE_DEADLINE_SECONDS = 20.0

_SYSTEM = (
    "You are an editing assistant. Rewrite the user's plain-text email body "
    "according to the instruction. Output ONLY the rewritten body — no preamble, "
    "no explanation, no quotes, no subject line. Preserve the user's meaning and "
    "any names, dates, and facts."
)


def resolve_refine_model(user_id: str) -> str:
    """The on-box model id for refine, or ``""`` when none is configured.

    Thin wrapper over the hard privacy boundary; the route calls this ONCE to both
    derive ``available`` and pass the resolved model into :func:`refine_text`.
    """
    return email_classifier.require_local_model(user_id, task="email_compose_refine") or ""


def refine_text(text: str, *, instruction: str, user_id: str, model: str) -> str:
    """Best-effort refined body, or ``""``. Never raises. ``model`` is the
    already-resolved non-empty on-box model id."""
    try:
        truncated = context_budget.truncate_text(text, _REFINE_INPUT_TOKENS)
        prompt = _build_prompt(instruction, truncated)
        return local_llm_task.run_bounded_completion(
            model,
            system=_SYSTEM,
            prompt=prompt,
            user_id=user_id,
            max_tokens=_REFINE_OUTPUT_TOKENS,
            deadline_seconds=_REFINE_DEADLINE_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 - best-effort; never raises to the route
        _log.info("refine_text: degrading to blank (%s)", type(exc).__name__)
        return ""


def _build_prompt(instruction: str, body: str) -> str:
    # Delimit the user's text from the instruction so body content (which may
    # include pasted/quoted email) is treated as text-to-rewrite, never as
    # instructions, and constrain the output shape.
    # LUM-704: a body carrying the literal `</body>` would let a naive reading
    # treat what follows as instruction rather than content. Reuses the shipped
    # document-path neutraliser rather than a second copy of the idea.
    safe_body = neutralize_wrapper_breakout_literals(body, ("body",))
    return (
        f"Instruction: {instruction}\n\n"
        "Rewrite the email body between the <body> tags. "
        "Output only the rewritten body.\n\n"
        f"<body>\n{safe_body}\n</body>"
    )
