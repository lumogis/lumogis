# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Port: graph store protocol.

Implemented by graph plugins (e.g. a FalkorDB adapter in plugins/graph/).
Core never imports a concrete graph store — it fires hooks and the plugin writes.

How it fits together
--------------------
- Core fires Event.ENTITY_CREATED and Event.DOCUMENT_INGESTED via hooks.py.
- A graph plugin subscribes to those events and calls create_node / create_edge.
- Core fires no read queries against the graph; query() is for plugin-internal
  traversal or for routes the plugin registers.

Node identity
-------------
create_node() returns a node ID — this is the string passed back as from_id /
to_id when creating edges. Implementations may use any stable internal ID
(e.g. a FalkorDB internal ID string). The lumogis_id property on the node
(the UUID from entities.entity_id or the file_path for documents) is stored
as a node property and is the cross-store link to Postgres/Qdrant.

Query language
--------------
Cypher is the expected query language. Graph plugins define their own
node types, edge types, and required properties. See the plugin's own
schema documentation for details.

Reference backend
-----------------
FalkorDB — MIT-licensed, Redis-protocol, Cypher-compatible.
Start it with: docker compose -f docker-compose.falkordb.yml up -d
Set FALKORDB_URL=redis://falkordb:6379 in .env.

Implementing a different backend (Neo4j, Memgraph, etc.) is fully supported —
implement this Protocol and register your adapter in config.py.
"""

from typing import Protocol


class GraphStore(Protocol):
    def ping(self) -> bool:
        """Return True if the graph store is reachable. Does not raise."""
        ...

    def create_node(self, labels: list[str], properties: dict) -> str:
        """Create a node and return its node ID.

        labels: one or more node type labels, e.g. ["Person"]
        properties: key/value properties to set on the node. Must include
            lumogis_id (UUID for entities, file_path for documents) and user_id.

        Returns a stable node ID string used as from_id / to_id in create_edge.
        """
        ...

    def create_edge(
        self,
        from_id: str,
        to_id: str,
        rel_type: str,
        properties: dict,
    ) -> None:
        """Create a directed edge between two nodes.

        from_id: node ID returned by create_node (source)
        to_id: node ID returned by create_node (target)
        rel_type: edge type string, e.g. "MENTIONS", "RELATES_TO", "WORKED_ON"
        properties: edge properties. Must include timestamp (ISO 8601) and user_id.
        """
        ...

    def query(self, cypher: str, params: dict | None = None) -> list[dict]:
        """Execute a Cypher query and return results as a list of dicts.

        Each dict maps result variable names to their values, e.g.:
            query("MATCH (p:Person {user_id: $uid}) RETURN p.name AS name", {"uid": "default"})
            → [{"name": "Ada Lovelace"}, ...]

        params: named parameters referenced in the Cypher query ($param_name).
        Returns an empty list if no results. Raises on syntax errors.
        """
        ...
