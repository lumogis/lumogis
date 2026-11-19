# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Event name constants for hook dispatch.

All hooks.fire(), hooks.register() calls use these constants
instead of raw strings to prevent silent typo bugs.
"""


class Event:
    DOCUMENT_INGESTED = "on_document_ingested"
    ENTITY_CREATED = "on_entity_created"
    SESSION_ENDED = "on_session_ended"
    TOOL_REGISTERED = "on_tool_registered"
    # CONTEXT_BUILDING — synchronous hook from chat `_inject_context`.
    # Kwargs contract (normative):
    #   query: str — user question
    #   context_fragments: list[str] — mutated in place; subscribers append plaintext
    #   user_id: str — visibility scope for graph/entity paths (default "default")
    #
    # LUM-308: when auto-RAG is enabled, Core appends document-derived plaintext
    # lines before this hook fires; in-process graph subscribers may then append
    # `[Graph]` lines. Service-mode graph fragments are appended after the hook in
    # `routes/chat.py`.
    #
    # LUM-124: v1 surfaces ``memory_type``, scalar ``confidence``, and staleness /
    # ``last_verified_at`` only inside the frozen ``[Graph] … (hint: type=…; …)``
    # plaintext suffix built in ``services/lumogis-graph/graph/query.py`` —
    # do not add parallel structured kwargs to ``hooks.fire`` in v1.
    CONTEXT_BUILDING = "on_context_building"

    NOTE_CAPTURED = "on_note_captured"
    AUDIO_TRANSCRIBED = "on_audio_transcribed"
    ENTITY_MERGED = "on_entity_merged"
    # LUM-966: fired (fire_background) after a member's delete of an entry commits,
    # and again by the retry sweep until both arms are cleared. Kwargs: entity_id,
    # user_id, projection_ids (ids only, never names). Subscribers: the Core
    # Qdrant listener (every graph mode) and the graph writer (inprocess) or the
    # webhook dispatcher (service).
    ENTITY_DELETED = "on_entity_deleted"

    # LUM-586: fired by the document-share entity cascade (off the request path,
    # from the share_document background job) once the Postgres+Qdrant shared
    # entity projections are committed. Carries the set of (source, projection)
    # entity ids so the KG service can MERGE the shared nodes and sweep incident
    # RELATES_TO edges. Only consumed when GRAPH_MODE=service (the outbound
    # webhook callback is wired by services/graph_webhook_dispatcher); with no
    # registered listener the fire is a harmless no-op (in-process parity is a
    # deferred follow-up).
    DOCUMENT_SHARED = "on_document_shared"

    SIGNAL_RECEIVED = "on_signal_received"
    FEEDBACK_RECEIVED = "on_feedback_received"
    # LUM-645: fired post-persist for every LIVE-POLL classified email so the
    # opt-in mailbox-organization hook can label/sort it (backfill is owned by
    # the standing reconcile sweep, not this event).
    EMAIL_SIGNAL_CLASSIFIED = "on_email_signal_classified"

    ACTION_EXECUTED = "on_action_executed"
    ACTION_REGISTERED = "on_action_registered"
    ROUTINE_ELEVATION_READY = "on_routine_elevation_ready"

    # Injection / retrieval hardening — see services/injection_sanitiser (LUM-127).
    # INGEST_CHUNK_READY is deferred to LUM-132 PreIngest hooks.
    INJECTION_FLAGGED = "on_injection_flagged"
    TOOL_CHAIN_CAP_TRIPPED = "on_tool_chain_cap_tripped"
