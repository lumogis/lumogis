# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Graph schema constants: node labels and edge types for Phase 3.

These constants are the single source of truth for the graph schema.
The writer, reconciliation job, and query paths all import from here.

Node labels
-----------
Entity nodes use the same label as their entity_type value in Postgres:
  Person, Organisation, Project, Concept

Information-object nodes anchor provenance edges:
  Document, Session, Note, AudioMemo

Edge types
----------
MENTIONS      — information-object → entity (provenance)
RELATES_TO    — entity → entity (co-occurrence, canonical lower→higher lumogis_id)
DISCUSSED_IN  — entity → session
DERIVED_FROM  — audio memo → transcript document (conditional, see plan §4)
LINKS_TO      — document → document (internal link, from vault adapter only)
TAGGED_WITH   — document → concept (tag materialization, from vault adapter only)

Co-occurrence threshold
-----------------------
RELATES_TO edges are stored from the first co-occurrence but are only
surfaced in queries/visualization when co_occurrence_count >= COOCCURRENCE_THRESHOLD.
"""

import config


class NodeLabel:
    # Entity nodes
    PERSON = "Person"
    ORGANISATION = "Organisation"
    PROJECT = "Project"
    CONCEPT = "Concept"

    # Information-object nodes
    DOCUMENT = "Document"
    SESSION = "Session"
    NOTE = "Note"
    AUDIO_MEMO = "AudioMemo"

    # Mapping from Postgres entity_type to graph label
    ENTITY_TYPE_MAP: dict[str, str] = {
        "PERSON": PERSON,
        "ORG": ORGANISATION,
        "PROJECT": PROJECT,
        "CONCEPT": CONCEPT,
    }

    @classmethod
    def for_entity_type(cls, entity_type: str) -> str:
        """Return the graph node label for a Postgres entity_type string.

        Falls back to Concept for unknown types.
        """
        return cls.ENTITY_TYPE_MAP.get(entity_type.upper(), cls.CONCEPT)


class EdgeType:
    MENTIONS = "MENTIONS"
    RELATES_TO = "RELATES_TO"
    DISCUSSED_IN = "DISCUSSED_IN"
    DERIVED_FROM = "DERIVED_FROM"
    LINKS_TO = "LINKS_TO"
    TAGGED_WITH = "TAGGED_WITH"


def _min_mention_count() -> int:
    return config.get_graph_min_mention_count()


def _cooccurrence_threshold() -> int:
    return config.get_cooccurrence_threshold()


def _max_cooccurrence_pairs() -> int:
    return config.get_graph_max_cooccurrence_pairs()


# These module-level names are read at import time by query.py and viz_routes.py.
# They keep their values from the first import; callers that need hot-reload
# should call the config.get_*() functions directly.  The writer and reconciler
# call the schema functions (not these constants) so they already hot-reload.
MIN_MENTION_COUNT: int = _min_mention_count()
COOCCURRENCE_THRESHOLD: int = _cooccurrence_threshold()
MAX_COOCCURRENCE_PAIRS: int = _max_cooccurrence_pairs()

# Maximum characters stored in any text property on a graph node (no content store)
MAX_TEXT_LENGTH: int = 500
