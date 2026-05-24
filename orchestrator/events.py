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
    CONTEXT_BUILDING = "on_context_building"

    NOTE_CAPTURED = "on_note_captured"
    AUDIO_TRANSCRIBED = "on_audio_transcribed"
    ENTITY_MERGED = "on_entity_merged"

    SIGNAL_RECEIVED = "on_signal_received"
    FEEDBACK_RECEIVED = "on_feedback_received"

    ACTION_EXECUTED = "on_action_executed"
    ACTION_REGISTERED = "on_action_registered"
    ROUTINE_ELEVATION_READY = "on_routine_elevation_ready"

    # Injection / retrieval hardening — see services/injection_sanitiser (LUM-127).
    # INGEST_CHUNK_READY is deferred to LUM-132 PreIngest hooks.
    INJECTION_FLAGGED = "on_injection_flagged"
    TOOL_CHAIN_CAP_TRIPPED = "on_tool_chain_cap_tripped"
