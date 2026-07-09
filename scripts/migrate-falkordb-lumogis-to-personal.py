#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""One-shot FalkorDB migration: legacy ``lumogis`` graph → ``personal`` bank graph (LUM-293)."""

from __future__ import annotations

import argparse
import logging
import os
import sys

_log = logging.getLogger(__name__)


def _parse_url(url: str) -> tuple[str, int]:
    url = url.strip()
    if url.startswith("redis://"):
        url = url[len("redis://") :]
    if ":" in url:
        host, port_str = url.rsplit(":", 1)
        return host, int(port_str)
    return url, 6379


def _graph(url: str, name: str):
    from falkordb import FalkorDB

    host, port = _parse_url(url)
    return FalkorDB(host=host, port=port).select_graph(name)


def _node_count(graph) -> int:
    result = graph.query("MATCH (n) RETURN count(n) AS c")
    if not result.result_set:
        return 0
    return int(result.result_set[0][0])


def migrate(*, url: str, source: str, target: str) -> tuple[int, int]:
    """Copy all nodes and relationships from ``source`` graph to ``target``. Idempotent MERGE."""
    if source == target:
        _log.info("source == target (%s); nothing to do", source)
        return 0, 0

    src = _graph(url, source)
    tgt = _graph(url, target)

    if _node_count(src) == 0:
        _log.info("source graph %r is empty; nothing to copy", source)
        return 0, 0

    nodes = src.query("MATCH (n) RETURN labels(n) AS labels, properties(n) AS props")
    node_rows = nodes.result_set or []
    copied_nodes = 0
    for labels, props in node_rows:
        label_list = list(labels or ["Node"])
        label_str = ":".join(label_list)
        props = dict(props or {})
        if "lumogis_id" in props and "user_id" in props:
            set_pairs = ", ".join(
                f"n.{k} = ${k}" for k in props if k not in ("lumogis_id", "user_id")
            )
            set_clause = f"SET {set_pairs}" if set_pairs else ""
            tgt.query(
                f"MERGE (n:{label_str} {{lumogis_id: $lumogis_id, user_id: $user_id}}) "
                f"{set_clause}",
                props,
            )
        elif "entity_id" in props:
            tgt.query(
                f"MERGE (n:{label_str} {{entity_id: $entity_id, user_id: $user_id}}) "
                "SET n += $props",
                {
                    "entity_id": props["entity_id"],
                    "user_id": props.get("user_id", "default"),
                    "props": props,
                },
            )
        else:
            tgt.query(f"CREATE (n:{label_str}) SET n = $props", {"props": props})
        copied_nodes += 1

    rels = src.query(
        "MATCH (a)-[r]->(b) "
        "RETURN type(r) AS rel, properties(r) AS rprops, "
        "labels(a) AS alabels, properties(a) AS aprops, "
        "labels(b) AS blabels, properties(b) AS bprops"
    )
    copied_edges = 0
    for rel, rprops, alabels, aprops, blabels, bprops in rels.result_set or []:
        aprops = dict(aprops or {})
        bprops = dict(bprops or {})
        rprops = dict(rprops or {})
        if "entity_id" in aprops and "entity_id" in bprops:
            tgt.query(
                f"MATCH (a {{entity_id: $asrc, user_id: $uid}}), "
                f"(b {{entity_id: $bdst, user_id: $uid}}) "
                f"MERGE (a)-[r:{rel}]->(b) SET r += $rprops",
                {
                    "asrc": aprops["entity_id"],
                    "bdst": bprops["entity_id"],
                    "uid": aprops.get("user_id", bprops.get("user_id", "default")),
                    "rprops": rprops,
                },
            )
        elif "lumogis_id" in aprops and "lumogis_id" in bprops:
            tgt.query(
                f"MATCH (a {{lumogis_id: $alid, user_id: $uid}}), "
                f"(b {{lumogis_id: $blid, user_id: $uid}}) "
                f"MERGE (a)-[r:{rel}]->(b) SET r += $rprops",
                {
                    "alid": aprops["lumogis_id"],
                    "blid": bprops["lumogis_id"],
                    "uid": aprops.get("user_id", bprops.get("user_id", "default")),
                    "rprops": rprops,
                },
            )
        copied_edges += 1

    _log.info(
        "Copied %d nodes and %d relationships from %r → %r",
        copied_nodes,
        copied_edges,
        source,
        target,
    )
    return copied_nodes, copied_edges


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("FALKORDB_URL", "redis://falkordb:6379"))
    parser.add_argument(
        "--source",
        default=os.environ.get("FALKORDB_GRAPH_NAME", "lumogis"),
        help="Legacy graph name (default: FALKORDB_GRAPH_NAME or lumogis)",
    )
    parser.add_argument("--target", default="personal", help="Target bank graph (default: personal)")
    args = parser.parse_args(argv)
    try:
        migrate(url=args.url, source=args.source, target=args.target)
    except Exception as exc:  # noqa: BLE001 — operator script surfaces failure.
        _log.error("migration failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
