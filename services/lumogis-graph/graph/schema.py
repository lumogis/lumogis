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

    # Code-structure nodes (LUM-301). Written with an explicit label by the
    # code-structure projector — not via ENTITY_TYPE_MAP/for_entity_type.
    COMPONENT = "Component"
    LIBRARY = "Library"

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

    # Code-structure edges (LUM-301).
    CALLS = "CALLS"
    DEPENDS_ON = "DEPENDS_ON"
    IMPLEMENTS = "IMPLEMENTS"

    # Contradiction history (LUM-104): superseding evidence node → superseded
    # fact's evidence node. Edge-to-edge references are impossible in a
    # property graph, so supersession is recorded between evidence nodes with
    # the invalidated edge's edge_uid carried as a property.
    SUPERSEDES = "SUPERSEDES"


# --------------------------------------------------------------------------- #
# Temporal validity (LUM-104)
# --------------------------------------------------------------------------- #

# Edge types that carry the temporal property set. DERIVED_FROM is excluded
# (deterministic transcript lineage has no validity window); LINKS_TO,
# TAGGED_WITH, and the code-structure edges are untouched.
TEMPORAL_EDGE_TYPES: tuple[str, ...] = (
    EdgeType.RELATES_TO,
    EdgeType.MENTIONS,
    EdgeType.DISCUSSED_IN,
)

# Temporal property names — the stable contract consumed by LUM-528 / LUM-369.
# All timestamp values use the fixed UTC format in graph/temporal.py
# (GRAPH_TS_FORMAT); "NULL" is property ABSENCE (FalkorDB cannot store a
# literal NULL) — never write a sentinel string.
TEMPORAL_PROP_VALID_AT = "valid_at"
TEMPORAL_PROP_INVALID_AT = "invalid_at"
TEMPORAL_PROP_EXPIRED_AT = "expired_at"
TEMPORAL_PROP_CREATED_AT = "created_at"
TEMPORAL_PROP_EDGE_UID = "edge_uid"
TEMPORAL_PROP_CONTRADICTION_COUNT = "contradiction_count"

# Evidence-node (information-object) label map, keyed by Postgres
# evidence_type. Mirrors the writer's `_ensure_source_node` label map so the
# temporal module can merge evidence nodes without importing the writer.
EVIDENCE_TYPE_LABEL_MAP: dict[str, str] = {
    "DOCUMENT": NodeLabel.DOCUMENT,
    "SESSION": NodeLabel.SESSION,
    "NOTE": NodeLabel.NOTE,
    "AUDIO": NodeLabel.AUDIO_MEMO,
}


# ---------------------------------------------------------------------------
# Node scope (LUM-961, ADR 015)
# ---------------------------------------------------------------------------
#
# Every graph node carries a ``scope`` property. ``visible_cypher_fragment``
# admits a member's own ``scope = 'personal'`` nodes and the household's
# ``shared`` / ``system`` nodes; a node without ``scope`` is invisible to every
# filtered read. Scope is a *writer* decision: never default it in the store
# adapter. Edges are outside this contract (personal edges carry no scope).

PERSONAL_SCOPE = "personal"
_PROJECTION_SCOPES = frozenset({"shared", "system"})


def node_scope(scope: str, published_from: str | None) -> str:
    """Validate a node's ``scope`` against its ``published_from`` and return it.

    Invariant: a node has ``scope = 'personal'`` iff it has no
    ``published_from``. Raises ``ValueError`` (naming the bad value; the row is
    not projected) when ``scope`` is not one of ``personal`` / ``shared`` /
    ``system`` (``None`` and ``""`` included), when a personal node carries
    ``published_from``, or when a shared/system node has none.

    Scope is a writer decision; never default it in the adapter (LUM-961).
    """
    if not isinstance(scope, str) or (scope != PERSONAL_SCOPE and scope not in _PROJECTION_SCOPES):
        raise ValueError(
            f"invalid node scope {scope!r}: must be 'personal', 'shared' or 'system'; "
            "row not projected"
        )
    if scope == PERSONAL_SCOPE and published_from is not None:
        raise ValueError(
            f"scope='personal' must not carry published_from={published_from!r}; row not projected"
        )
    if scope in _PROJECTION_SCOPES and not published_from:
        raise ValueError(f"scope={scope!r} requires published_from; row not projected")
    return scope


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
