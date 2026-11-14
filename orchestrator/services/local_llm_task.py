# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Bounded, deadline-guarded on-box LLM completion, shared across on-demand
composer LLM features (LUM-684).

``run_bounded_completion`` is the machinery extracted from
``email_reply_draft._complete_with_deadline``: a single completion behind an
explicit deadline (the provider's ``chat()`` takes no timeout) and a **shared**
global concurrency cap. The cap protects the one on-box model the whole household
also uses — so the reply-draft and the compose-refine features **draw from the
same semaphore** rather than each spawning their own quota against Ollama.

Exception contract: returns ``""`` on **cap-reached** and on **TimeoutError**; a
**provider exception inside the completion re-raises** (the "never raises"
guarantee belongs to each caller's own try/except, as in ``suggest_reply_draft``).
The permit is always released in the ``_call`` ``finally`` — even on that raise
and even past the deadline (a lingering call still counts against the cap until it
actually finishes, so a slow model can't be double-spent).
"""

from __future__ import annotations

import concurrent.futures
import logging
import threading

import config

_log = logging.getLogger(__name__)

# The single cap shared by every on-demand composer LLM feature (reply-draft,
# compose-refine). Email classification runs its own path; this bounds the
# on-demand features' concurrent load on the shared on-box model.
_MAX_CONCURRENT_LLM_TASKS = 3
_LLM_TASK_SLOTS = threading.BoundedSemaphore(_MAX_CONCURRENT_LLM_TASKS)


def run_bounded_completion(
    model: str,
    *,
    system: str,
    prompt: str,
    user_id: str,
    max_tokens: int,
    deadline_seconds: float,
) -> str:
    """One completion under an explicit deadline and the shared concurrency cap.

    ``""`` when the cap is reached (fail-fast, no thread spawned) or on timeout.
    Re-raises a provider exception (callers wrap for their "never raises").
    """
    if not _LLM_TASK_SLOTS.acquire(blocking=False):
        _log.info("local_llm_task: concurrency cap reached; returning blank")
        return ""

    def _call() -> str:
        # Hold the permit for the FULL real completion (even past the deadline),
        # so a lingering call still counts against the cap until it finishes.
        try:
            llm = config.get_llm_provider(model, user_id=user_id)
            response = llm.chat(
                messages=[{"role": "user", "content": prompt}],
                system=system,
                max_tokens=max_tokens,
            )
            return (response.text or "").strip()
        finally:
            _LLM_TASK_SLOTS.release()

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_call)
    try:
        return future.result(timeout=deadline_seconds)
    except concurrent.futures.TimeoutError:
        _log.info("local_llm_task: LLM deadline exceeded; returning blank")
        return ""
    finally:
        # Never block the request worker on a lingering completion.
        executor.shutdown(wait=False)
