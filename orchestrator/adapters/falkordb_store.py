# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""GraphStore adapter for FalkorDB.

Implements the GraphStore protocol (ports/graph_store.py) using the
`falkordb` pip package (falkordb>=1.0.0).

Constructor API note (M1 verification finding)
-----------------------------------------------
The plan's arbitration rounds specified `FalkorDB.from_url(url)` as the
preferred constructor for per-call thread safety. During M1 implementation,
the pre-M1 gate confirmed that `from_url` does NOT exist in falkordb v1.6.x
(the method is absent from `dir(FalkorDB)`). The correct API is:

    db = FalkorDB(host=host, port=port)
    graph = db.select_graph(name)

Do not attempt to use `from_url` in M2, M3, or M4 without first verifying
it has been added in a newer version of the package.

Thread safety model
-------------------
`hooks.fire_background()` routes callbacks through a 4-worker ThreadPoolExecutor,
so `create_node`, `create_edge`, and `query` may be called concurrently.
A single shared `falkordb.Graph` handle is NOT thread-safe (per the plan risk
table). This adapter therefore uses a per-call handle: each method creates a
fresh `FalkorDB(host, port).select_graph(name)` handle before executing. The
Redis protocol makes connection creation cheap (< 1ms on localhost). This
approach avoids any locking overhead and is safe for any concurrency level.

Config env vars
---------------
FALKORDB_URL          redis URL, e.g. redis://falkordb:6379 (required when GRAPH_BACKEND=falkordb)
FALKORDB_GRAPH_NAME   graph name inside FalkorDB (default: lumogis). Use distinct names
                      when dev and prod share the same FalkorDB server.

MERGE semantics
---------------
Nodes are created with MERGE on (lumogis_id, user_id) — those two fields
form the stable external identity.

Edges are created with MERGE on (from_id, to_id, rel_type, evidence_id)
so that replaying the same hook event is idempotent. The SET clause
updates timestamp and user_id on every MERGE so properties stay current.
"""

import logging
import os

_log = logging.getLogger(__name__)

_DEFAULT_GRAPH_NAME = os.environ.get("FALKORDB_GRAPH_NAME", "lumogis")


def _parse_url(url: str) -> tuple[str, int]:
    """Parse redis://host:port into (host, port). Defaults: localhost:6379."""
    url = url.strip()
    if url.startswith("redis://"):
        url = url[len("redis://"):]
    if ":" in url:
        host, port_str = url.rsplit(":", 1)
        return host, int(port_str)
    return url, 6379


class FalkorDBStore:
    """GraphStore implementation backed by FalkorDB.

    Uses per-call connection handles for thread safety — see module docstring.
    """

    def __init__(self, url: str, graph_name: str = _DEFAULT_GRAPH_NAME) -> None:
        self._url = url
        self._graph_name = graph_name
        self._host, self._port = _parse_url(url)
        _log.info(
            "FalkorDBStore initialised: url=%s graph=%s",
            self._url,
            self._graph_name,
        )

    def _graph(self):
        """Return a fresh per-call graph handle (thread-safe)."""
        from falkordb import FalkorDB  # type: ignore[import]

        return FalkorDB(host=self._host, port=self._port).select_graph(self._graph_name)

    def ping(self) -> bool:
        try:
            self._graph().ro_query("RETURN 1")
            return True
        except Exception:
            return False

    def create_node(self, labels: list[str], properties: dict) -> str:
        """MERGE a node by (lumogis_id, user_id) and return its internal ID string."""
        label_str = ":".join(labels)
        lumogis_id = properties["lumogis_id"]

        set_pairs = ", ".join(
            f"n.{k} = ${k}"
            for k in properties
            if k not in ("lumogis_id", "user_id")
        )
        set_clause = f"SET {set_pairs}" if set_pairs else ""

        cypher = (
            f"MERGE (n:{label_str} {{lumogis_id: $lumogis_id, user_id: $user_id}}) "
            f"{set_clause} "
            f"RETURN id(n) AS node_id"
        )
        result = self._graph().query(cypher, properties)
        rows = result.result_set
        if not rows:
            raise RuntimeError(
                f"create_node: MERGE returned no rows for lumogis_id={lumogis_id}"
            )
        return str(rows[0][0])

    def create_edge(
        self,
        from_id: str,
        to_id: str,
        rel_type: str,
        properties: dict,
    ) -> None:
        """MERGE an edge between two nodes (matched by internal id())."""
        set_pairs = ", ".join(f"r.{k} = ${k}" for k in properties)
        set_clause = f"SET {set_pairs}" if set_pairs else ""

        cypher = (
            f"MATCH (a) WHERE id(a) = {from_id} "
            f"MATCH (b) WHERE id(b) = {to_id} "
            f"MERGE (a)-[r:{rel_type} {{evidence_id: $evidence_id}}]->(b) "
            f"{set_clause}"
        )
        self._graph().query(cypher, properties)

    def query(self, cypher: str, params: dict | None = None) -> list[dict]:
        """Execute a Cypher query and return rows as list of dicts."""
        result = self._graph().query(cypher, params or {})
        rows = result.result_set
        if not rows:
            return []
        header = result.header if hasattr(result, "header") else None
        if header:
            keys = [col[1] if isinstance(col, (list, tuple)) else col for col in header]
            return [dict(zip(keys, row)) for row in rows]
        return [{"_col" + str(i): v for i, v in enumerate(row)} for row in rows]
