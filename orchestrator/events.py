"""Event name constants for hook dispatch.

All hooks.fire(), hooks.register() calls use these constants
instead of raw strings to prevent silent typo bugs.
"""


class Event:
    DOCUMENT_INGESTED = "on_document_ingested"
    ENTITY_CREATED = "on_entity_created"
    SESSION_ENDED = "on_session_ended"
    TOOL_REGISTERED = "on_tool_registered"
