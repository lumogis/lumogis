"""Event name constants for hook dispatch.

All hooks.fire(), hooks.register() calls use these constants
instead of raw strings to prevent silent typo bugs.
"""


class Event:
    DOCUMENT_INGESTED = "on_document_ingested"
    ENTITY_CREATED = "on_entity_created"
    SESSION_ENDED = "on_session_ended"
    TOOL_REGISTERED = "on_tool_registered"
    CONTEXT_BUILDING = "on_context_building"

    # Signal infrastructure (Chunk 12a)
    SIGNAL_RECEIVED = "on_signal_received"
    FEEDBACK_RECEIVED = "on_feedback_received"

    # Actions foundation (Chunk 12b)
    ACTION_EXECUTED = "on_action_executed"
    ACTION_REGISTERED = "on_action_registered"
    ROUTINE_ELEVATION_READY = "on_routine_elevation_ready"

    # APP-only: defined for type safety, never fired by core
    ENTITY_UPDATED = "on_entity_updated"
    CONTEXT_CHANGED = "on_context_changed"
    BRIEFING_READY = "on_briefing_ready"
