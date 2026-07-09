# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""The member's own household shared items (LUM-583).

Powers the "Settings → My shared items" page: everything the caller has shared
with the household, across all shareable types, so they can review and revoke
in one place.

Design (per the LUM-583 plan, R1 corrections):

* **Read the shared PROJECTION rows directly.** The projection row carries
  ``user_id = the sharer`` and ``published_from = the source pk`` (publish is
  owner-only), so "things I shared" is simply
  ``WHERE scope='shared' AND user_id = %s`` on each table — no self-join to the
  personal source, and type-agnostic (it shows whatever was shared regardless
  of which UI created it).
* **``resource_id = published_from``**, never the projection row's own pk. For
  UUID-keyed types the projection pk is a *different* uuid5-derived id, and the
  per-type ``DELETE /api/v1/{type}/{id}/publish`` guard matches the **source**
  pk — using the projection pk would 404 every unshare.
* **N per-arm queries, each isolated.** One resource type's query failing must
  not blank the whole list (it degrades to the arms that succeeded), so this is
  N small indexed queries rather than one atomic UNION ALL.
* **Owner-scope is structural.** Every arm binds ``user_id = %s`` to the
  caller, so the endpoint cannot return another member's shared items even
  though those projections are household-visible — stronger than a client-side
  ``is_owner`` filter.

Unshare is NOT implemented here — the page reuses the existing owner-only
``DELETE /api/v1/{resource_type}/{id}/publish`` routes.
"""

from __future__ import annotations

import logging

from services.sharing_registry import SHAREABLE_RESOURCES
from services.sharing_registry import short_label

import config

_log = logging.getLogger(__name__)


def list_my_shared_items(user_id: str, *, limit: int = 200) -> list[dict]:
    """Return the caller's household shared items across all shareable types.

    Each item: ``{resource_type, resource_id, label, shared_at}`` where
    ``resource_type`` is the route segment for the unshare call and
    ``resource_id`` is the source publish pk (``published_from``).
    """
    ms = config.get_metadata_store()
    items: list[dict] = []
    failures = 0
    for rtype, cfg in SHAREABLE_RESOURCES.items():
        ts_col = cfg["shared_ts_col"]
        try:
            rows = ms.fetch_all(
                # Table / label / timestamp columns are fixed registry constants;
                # only user_id + limit are %s-bound (no injection surface).
                f"SELECT published_from, {cfg['label_col']} AS label, "  # noqa: S608
                f"{ts_col} AS shared_at "
                f"FROM {cfg['table']} "
                f"WHERE scope = 'shared' AND user_id = %s AND published_from IS NOT NULL "
                f"ORDER BY {ts_col} DESC "
                f"LIMIT %s",
                (user_id, limit),
            )
        except Exception:  # noqa: BLE001 — per-arm isolation; one type must not blank the list
            _log.exception("list_my_shared_items: query failed for %s", rtype)
            failures += 1
            continue
        for r in rows:
            items.append(
                {
                    "resource_type": rtype,
                    "resource_id": str(r["published_from"]),
                    "label": short_label(r.get("label")),
                    "shared_at": r.get("shared_at"),
                }
            )

    # Per-arm isolation degrades a *single* failing type gracefully — but if
    # EVERY arm failed (e.g. the metadata store is down), returning an empty
    # list would render "you haven't shared anything", indistinguishable from
    # genuinely-empty. Fail loud instead so the route surfaces an error.
    if failures == len(SHAREABLE_RESOURCES):
        raise RuntimeError("list_my_shared_items: all resource-type queries failed")

    # Global recency order (most recently shared first) across types — each arm
    # is ORDER BY ts DESC, but arms are concatenated in registry order, so
    # re-sort the union. Items without a timestamp (should not occur while every
    # table pins a shared_ts_col) sort last; only non-null timestamps are
    # compared, so no naive/aware datetime mixing.
    dated = [i for i in items if i["shared_at"] is not None]
    undated = [i for i in items if i["shared_at"] is None]
    dated.sort(key=lambda i: i["shared_at"], reverse=True)
    return dated + undated
