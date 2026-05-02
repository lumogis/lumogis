# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Signal processing pipeline.

process_signal(raw_signal):
  1. Truncates raw_content to fit model context budget.
  2. Calls LLM (SIGNAL_LLM_MODEL, default llama) for summarisation, entity tagging, topic
     classification, and importance scoring in a single prompt.
  3. Runs match_relevance() against the stored RelevanceProfile.
  4. If relevance_score >= threshold: notifies via config.get_notifier()
     and fires Event.SIGNAL_RECEIVED hook.
  5. Persists the processed signal to Postgres and embeds summary to Qdrant.

score_importance() is also available standalone; it re-uses cached LLM results.
"""

import hashlib
import json
import logging
import os
import uuid

import hooks
from events import Event
from models.signals import RelevanceProfile
from models.signals import Signal
from services.context_budget import get_budget
from services.context_budget import truncate_text

import config

_log = logging.getLogger(__name__)

_score_cache: dict[str, float] = {}

_RELEVANCE_THRESHOLD_DEFAULT = 0.4

_PROCESS_PROMPT_TEMPLATE = """\
Analyse the following content item. Respond with a single JSON object — no markdown.

Title: {title}
Content: {content}

Required JSON keys:
  "content_summary": one or two sentence summary (max 200 chars)
  "topics": list of 1-5 short lowercase topic strings
  "entities": list of objects with "name" (string) and "type" ("PERSON"|"ORG"|"PLACE"|"CONCEPT")
  "importance_score": float 0.0-1.0 — higher means more novel, specific, and actionable

Example:
{{"content_summary":"RBA holds cash rate at 4.35%.",\
"topics":["monetary policy","interest rates"],\
"entities":[{{"name":"Reserve Bank of Australia","type":"ORG"}}],\
"importance_score":0.75}}
"""


def process_signal(raw_signal: Signal, user_id: str = "default") -> Signal:
    """Process a raw signal through the full pipeline.

    Mutates nothing on the input — returns a new Signal with populated fields.
    raw_content is cleared on the returned signal (transient, not persisted).
    """
    threshold = float(
        os.environ.get("SIGNAL_RELEVANCE_THRESHOLD", str(_RELEVANCE_THRESHOLD_DEFAULT))
    )

    signal_model = os.environ.get("SIGNAL_LLM_MODEL", "llama")
    budget = get_budget(signal_model) - 500
    content = truncate_text(raw_signal.raw_content, budget)

    llm_data = _call_llm(raw_signal.title, content, user_id=user_id)

    importance = float(llm_data.get("importance_score", 0.0))
    importance = max(0.0, min(1.0, importance))

    cache_key = hashlib.md5(raw_signal.url.encode()).hexdigest()
    _score_cache[cache_key] = importance

    signal = Signal(
        signal_id=raw_signal.signal_id,
        source_id=raw_signal.source_id,
        title=raw_signal.title,
        url=raw_signal.url,
        published_at=raw_signal.published_at,
        content_summary=llm_data.get("content_summary", raw_signal.title)[:500],
        raw_content="",  # cleared — transient only
        entities=llm_data.get("entities", []),
        topics=llm_data.get("topics", []),
        importance_score=importance,
        relevance_score=0.0,
        notified=False,
        created_at=raw_signal.created_at,
        user_id=user_id,
    )

    profile = _load_profile(user_id)
    if profile:
        signal.relevance_score = match_relevance(signal, profile)

    if signal.relevance_score >= threshold:
        notified = _notify(signal)
        signal.notified = notified
        hooks.fire_background(Event.SIGNAL_RECEIVED, signal=signal)

    _persist(signal)
    return signal


def score_importance(signal: Signal) -> float:
    """Return the cached importance score for a signal URL, or recompute."""
    cache_key = hashlib.md5(signal.url.encode()).hexdigest()
    if cache_key in _score_cache:
        return _score_cache[cache_key]
    # Re-run LLM only if not cached.
    signal_model = os.environ.get("SIGNAL_LLM_MODEL", "llama")
    budget = get_budget(signal_model) - 500
    content = truncate_text(signal.raw_content or signal.content_summary, budget)
    data = _call_llm(signal.title, content, user_id=signal.user_id)
    score = max(0.0, min(1.0, float(data.get("importance_score", 0.0))))
    _score_cache[cache_key] = score
    return score


def match_relevance(signal: Signal, profile: RelevanceProfile) -> float:
    """Weighted relevance score: topic + location + entity + keyword + importance.

    Weights:  topic 0.30 | location 0.20 | entity 0.20 | keyword 0.20 | importance 0.10
    Each component is clamped to [0, 1] before weighting.
    """
    signal_topics = {t.lower() for t in signal.topics}
    signal_entities = {e.get("name", "").lower() for e in signal.entities}
    signal_text = (signal.title + " " + signal.content_summary).lower()

    topic_score = _fraction_match(profile.tracked_topics, signal_topics, exact=True)
    location_score = _fraction_match(profile.tracked_locations, signal_text, exact=False)
    entity_score = _fraction_match(profile.tracked_entities, signal_entities, exact=True)
    keyword_score = _fraction_match(profile.tracked_keywords, signal_text, exact=False)

    score = (
        0.30 * topic_score
        + 0.20 * location_score
        + 0.20 * entity_score
        + 0.20 * keyword_score
        + 0.10 * signal.importance_score
    )
    return round(min(1.0, score), 4)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _call_llm(
    title: str, content: str, *, user_id: str | None = None
) -> dict:
    """Run the combined summarise+score LLM call. Returns parsed dict or {}.

    Plan llm_provider_keys_per_user_migration Pass 2.9: ``user_id`` is
    keyword-only and required for cloud ``SIGNAL_LLM_MODEL`` values
    (any model with an ``api_key_env``). Local models still resolve
    without a user. The boot-time ``_check_background_model_defaults``
    refuses to start with a cloud default + ``AUTH_ENABLED=true``, so
    the only way to land here with a cloud model and no ``user_id`` is
    a per-call signal source that forgot to thread it — we WARN and
    skip enrichment so the signal still lands.
    """
    prompt = _PROCESS_PROMPT_TEMPLATE.format(title=title, content=content)
    model_name = os.environ.get("SIGNAL_LLM_MODEL", "llama")
    try:
        needs_user_key = bool(
            config.get_model_config(model_name).get("api_key_env")
        )
    except Exception:
        needs_user_key = False
    if needs_user_key and not user_id:
        _log.warning(
            "signal_llm: SIGNAL_LLM_MODEL=%s needs a per-user API key but no "
            "user_id was supplied; skipping LLM enrichment for this signal.",
            model_name,
        )
        return {}
    try:
        from services.connector_credentials import ConnectorNotConfigured
        from services.connector_credentials import CredentialUnavailable

        llm = config.get_llm_provider(model_name, user_id=user_id)
        response = llm.chat(
            messages=[{"role": "user", "content": prompt}],
            system="You are a concise content analyst. Always respond with valid JSON only.",
            max_tokens=512,
        )
        raw = response.text.strip()
        # Strip markdown code fences if present.
        if raw.startswith("```"):
            raw = "\n".join(line for line in raw.splitlines() if not line.startswith("```"))
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        _log.warning("LLM returned non-JSON for signal %r: %s", title[:60], exc)
    except ConnectorNotConfigured as exc:
        _log.warning(
            "signal_llm: missing per-user credential for model=%s user=%s: %s",
            model_name, user_id, exc,
        )
    except CredentialUnavailable as exc:
        _log.warning(
            "signal_llm: stored credential unusable for model=%s user=%s: %s",
            model_name, user_id, exc,
        )
    except Exception as exc:
        _log.warning("LLM call failed for signal %r: %s", title[:60], exc)
    return {}


def _fraction_match(tracked: list[str], signal_data, exact: bool) -> float:
    """Compute fraction of tracked items that appear in signal_data.

    exact=True: signal_data is a set of strings (exact token match).
    exact=False: signal_data is a string (substring match).
    """
    if not tracked:
        return 0.0
    matches = 0
    for item in tracked:
        if exact:
            if item.lower() in signal_data:
                matches += 1
        else:
            if item.lower() in signal_data:
                matches += 1
    return min(1.0, matches / len(tracked))


def _load_profile(user_id: str) -> RelevanceProfile | None:
    try:
        ms = config.get_metadata_store()
        # SCOPE-EXEMPT: `relevance_profiles` is in plan §2.10's
        # excluded-from-scope list — relevance is a per-user signal-routing
        # config, not memory content; no `scope` column exists.
        row = ms.fetch_one(
            "SELECT id, tracked_locations, tracked_topics, tracked_entities, "
            "tracked_keywords, updated_at FROM relevance_profiles "
            "WHERE user_id = %s ORDER BY updated_at DESC LIMIT 1",
            (user_id,),
        )
        if not row:
            return None
        return RelevanceProfile(
            id=str(row["id"]),
            tracked_locations=row.get("tracked_locations") or [],
            tracked_topics=row.get("tracked_topics") or [],
            tracked_entities=row.get("tracked_entities") or [],
            tracked_keywords=row.get("tracked_keywords") or [],
            updated_at=row.get("updated_at"),
            user_id=user_id,
        )
    except Exception as exc:
        _log.warning("Could not load relevance profile for %s: %s", user_id, exc)
        return None


def _notify(signal: Signal) -> bool:
    try:
        notifier = config.get_notifier()
        return notifier.notify(
            signal.title,
            signal.content_summary,
            signal.importance_score,
            user_id=signal.user_id,
        )
    except Exception as exc:
        _log.warning("Notifier error for signal %r: %s", signal.title[:60], exc)
        return False


_SYSTEM_SOURCE_SENTINEL = "__system__"


def _persist(signal: Signal) -> None:
    """Insert signal into Postgres and embed summary into Qdrant.

    Scope semantics (per plan §2.12 + §5.6):

    * ``source_id == '__system__'`` → ``scope='system'`` (the only writer
      that produces non-personal rows in v1; backfilled by migration 013
      for any pre-013 system signals).
    * everything else → ``scope='personal'``.

    The ``source_url`` and ``source_label`` columns are denormalized via
    sub-SELECTs against the publisher's `sources` row so shared/system
    projection rows remain renderable without a `sources` join (which is
    intentionally NOT in the scope-bearing set per §2.12). For
    `__system__` signals both sub-SELECTs harmlessly return NULL.
    """
    is_system = signal.source_id == _SYSTEM_SOURCE_SENTINEL
    scope = "system" if is_system else "personal"
    try:
        ms = config.get_metadata_store()
        ms.execute(
            "INSERT INTO signals "
            "(signal_id, user_id, source_id, title, url, published_at, content_summary, "
            "entities, topics, importance_score, relevance_score, notified, created_at, "
            "scope, source_url, source_label) "
            "VALUES ("
            "%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, "
            "%s, "
            "(SELECT url  FROM sources WHERE id::text = %s LIMIT 1), "
            "(SELECT name FROM sources WHERE id::text = %s LIMIT 1)"
            ") "
            "ON CONFLICT (signal_id) DO NOTHING",
            (
                signal.signal_id,
                signal.user_id,
                signal.source_id,
                signal.title,
                signal.url,
                signal.published_at,
                signal.content_summary,
                json.dumps(signal.entities),
                json.dumps(signal.topics),
                signal.importance_score,
                signal.relevance_score,
                signal.notified,
                signal.created_at,
                scope,
                signal.source_id,
                signal.source_id,
            ),
        )
    except Exception as exc:
        _log.error("Postgres persist error for signal %s: %s", signal.signal_id, exc)

    # Embed for semantic dedup (Qdrant signals collection).
    try:
        if signal.content_summary:
            embedder = config.get_embedder()
            vs = config.get_vector_store()
            vector = embedder.embed(signal.content_summary)
            point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"signal::{signal.signal_id}"))
            vs.upsert(
                collection="signals",
                id=point_id,
                vector=vector,
                payload={
                    "signal_id": signal.signal_id,
                    "source_id": signal.source_id,
                    "title": signal.title,
                    "url": signal.url,
                    "importance_score": signal.importance_score,
                    "relevance_score": signal.relevance_score,
                    "user_id": signal.user_id,
                    "scope": scope,
                },
            )
    except Exception as exc:
        _log.warning("Qdrant embed error for signal %s: %s", signal.signal_id, exc)
