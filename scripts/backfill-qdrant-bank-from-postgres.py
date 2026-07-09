#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Sync Qdrant ``memories`` payload ``bank`` from Postgres SoR (LUM-293 operator script)."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

_log = logging.getLogger(__name__)

COLLECTION = "memories"


def _conn_kwargs() -> dict:
    return {
        "host": os.environ.get("POSTGRES_HOST", "postgres"),
        "port": int(os.environ.get("POSTGRES_PORT", "5432")),
        "user": os.environ.get("POSTGRES_USER", "lumogis"),
        "password": os.environ.get("POSTGRES_PASSWORD", "lumogis-dev"),
        "dbname": os.environ.get("POSTGRES_DB", "lumogis"),
    }


def backfill(*, qdrant_url: str, dry_run: bool = False) -> tuple[int, int]:
    import psycopg2
    from qdrant_client import QdrantClient
    from qdrant_client.models import PointIdsList

    client = QdrantClient(url=qdrant_url)
    conn = psycopg2.connect(**_conn_kwargs())
    patched = 0
    scanned = 0
    try:
        offset = None
        while True:
            points, offset = client.scroll(
                collection_name=COLLECTION,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            if not points:
                break
            for point in points:
                scanned += 1
                payload = point.payload or {}
                memory_id = payload.get("memory_id")
                user_id = payload.get("user_id")
                if not memory_id or not user_id:
                    continue
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT bank FROM memories WHERE id = %s AND user_id = %s",
                        (str(memory_id), str(user_id)),
                    )
                    row = cur.fetchone()
                if row is None:
                    continue
                pg_bank = row[0]
                if payload.get("bank") == pg_bank:
                    continue
                patched += 1
                if dry_run:
                    _log.info(
                        "would patch point %s memory_id=%s bank %r → %r",
                        point.id,
                        memory_id,
                        payload.get("bank"),
                        pg_bank,
                    )
                else:
                    client.set_payload(
                        collection_name=COLLECTION,
                        payload={"bank": pg_bank},
                        points=PointIdsList(points=[point.id]),
                    )
            if offset is None:
                break
    finally:
        conn.close()
    return scanned, patched


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--qdrant-url",
        default=os.environ.get("QDRANT_URL", "http://qdrant:6333"),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        scanned, patched = backfill(qdrant_url=args.qdrant_url, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001
        _log.error("backfill failed: %s", exc)
        return 1
    _log.info(
        "scanned %d points; %s %d payload bank field(s)",
        scanned,
        "would patch" if args.dry_run else "patched",
        patched,
    )
    print(json.dumps({"scanned": scanned, "patched": patched}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
