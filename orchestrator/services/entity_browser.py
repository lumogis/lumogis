# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Read side of "What Lumogis knows": the entry list and "Mentioned in" (LUM-161).

Postgres only, identical in every ``GRAPH_MODE`` (ADR 263). Two member-facing
questions live here:

* :func:`list_entities` — the entries the viewer may see, grouped by kind, with
  counts and (for entries the viewer owns) how many items still mention them.
* :func:`list_mentions` — the documents, chats and AI-tool additions that
  mention one entry, **limited to items the viewer can open**. That filter is
  the privacy boundary: Bob viewing Alice's shared entry never sees Alice's
  personal documents or chats, only what she shared with the household.

The write side (rename, other names) is :mod:`services.entity_edit`.
"""

from __future__ import annotations

import logging
from typing import Literal
from typing import Optional

from auth import UserContext
from models.api_v1 import EntityKindCounts
from models.api_v1 import EntityListItem
from models.api_v1 import EntityListResponse
from models.api_v1 import EntityMention
from models.api_v1 import EntityMentionCounts
from models.api_v1 import EntityMentionsResponse
from models.api_v1 import EntityMovableMention
from visibility import not_hidden_sql
from visibility import visible_filter

import config

_log = logging.getLogger(__name__)

TITLE_MAX = 120

# Raw ``entity_type`` → one of five member-facing kinds. Everything that is not
# one of the four named types (FILE, the LUM-294 coding vocabulary, unknown,
# mixed-case variants once ``upper()`` has normalised them) is "other".
KIND_SQL = (
    "CASE upper(e.entity_type) "
    "WHEN 'PERSON' THEN 'person' "
    "WHEN 'ORG' THEN 'org' "
    "WHEN 'PROJECT' THEN 'project' "
    "WHEN 'CONCEPT' THEN 'concept' "
    "ELSE 'other' END"
)

# The number of items that mention an entry and still exist — the same set the
# owner's "Mentioned in" lists, NOT the ``entities.mention_count`` column (which
# counts extraction passes and grows on every re-ingest of the same file). The
# owner condition sits inside the lateral so other members' rows skip the scan
# (their counts are not exposed). ``sessions`` is compared as text: the legacy
# ``POST /entities/extract`` allows non-UUID SESSION evidence ids.
# Params: (caller_user_id,)
# SCOPE-EXEMPT: owner-only count of the caller's own personal rows (LUM-161).
OWN_MENTIONS_SQL = (
    "SELECT count(*)::int AS n, max(er.created_at) AS last_at "
    "FROM entity_relations er "
    "WHERE e.user_id = %s AND e.scope = 'personal' "
    "  AND er.source_id = e.entity_id AND er.user_id = e.user_id "
    "  AND ("
    "       er.evidence_type = 'MEMORY' "
    "    OR (er.evidence_type = 'DOCUMENT' AND EXISTS (SELECT 1 FROM file_index f "
    "          WHERE f.user_id = er.user_id AND f.file_path = er.evidence_id "
    "            AND f.published_from IS NULL)) "
    "    OR (er.evidence_type = 'SESSION' AND EXISTS (SELECT 1 FROM sessions s "
    "          WHERE s.user_id = er.user_id AND s.session_id::text = er.evidence_id "
    "            AND s.published_from IS NULL))"
    "  )"
)

# `e` is shared with the viewer when it is a household/system row, or when it is
# a personal source that has a shared projection.
_IS_SHARED_SQL = (
    "(e.scope IN ('shared','system') OR EXISTS ("
    "SELECT 1 FROM entities p WHERE p.published_from = e.entity_id AND p.scope = 'shared'))"
)

# Fixed SQL per allowed value; never interpolated from input.
_SORT_SQL = {
    "mentions": "COALESCE(r.n, 0) DESC, lower(e.name), e.entity_id",
    "recent": "r.last_at DESC NULLS LAST, lower(e.name), e.entity_id",
    "name": "lower(e.name), e.entity_id",
}

_KINDS = ("person", "org", "project", "concept", "other")


class EntityStoreUnavailable(Exception):
    """The Postgres read/write behind a KG browser route failed (route → 503)."""


# ── share helpers (moved from routes/api_v1/kg.py; one copy) ──────────────────


def _fetch_shared_entity_source_ids(user_id: str, *, raise_on_error: bool = False) -> set[str]:
    """Source entity ids the caller currently has a shared projection for (LUM-581).

    Mirrors :func:`services.documents._fetch_shared_source_ids`. A shared
    projection row carries the publisher's ``user_id`` and a ``published_from``
    pointing at the personal source; this returns the set of those source ids so
    an owner's personal entity can be marked ``share_status='shared'``.

    By default a DB error is swallowed (returns ``set()``). The entry list passes
    ``raise_on_error=True``: an outage must be a 503, not every shared entry
    shown as personal (LUM-161).
    """
    ms = config.get_metadata_store()
    try:
        rows = ms.fetch_all(
            # SCOPE-EXEMPT: caller-owned shared entity projection sources.
            "SELECT DISTINCT published_from FROM entities "
            "WHERE user_id = %s AND scope = 'shared' AND published_from IS NOT NULL",
            (user_id,),
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "kg._fetch_shared_entity_source_ids: query failed user_id=%s",
            user_id,
            exc_info=True,
        )
        if raise_on_error:
            raise EntityStoreUnavailable("shared entity ids unavailable") from exc
        return set()
    return {str(r["published_from"]) for r in rows if r.get("published_from") is not None}


def _derive_entity_share_fields(
    row: dict,
    *,
    caller_user_id: str,
    shared_source_ids: set[str],
) -> tuple[str, bool]:
    """Return ``(share_status, is_owner)`` for an entity row (LUM-581).

    Synchronous analogue of :func:`services.documents._derive_share_fields`:
    entity publish has no background job, so only ``personal``/``shared`` occur.

    * A shared projection carries the *publisher's* ``user_id``. A member viewing
      another owner's projection (``scope='shared'`` and not the caller) →
      ``('shared', False)``.
    * The owner's personal source is ``shared`` when a projection exists for it
      (its id is in ``shared_source_ids``), else ``personal``.
    """
    is_owner = row.get("user_id") == caller_user_id
    scope = row.get("scope") or "personal"
    if scope == "shared" and not is_owner:
        return "shared", False
    if str(row.get("entity_id")) in shared_source_ids:
        return "shared", is_owner
    if scope == "shared" and is_owner:
        # A projection row the caller owns fetched directly by id (the list path
        # collapses these away; the single-fetch path may still hit one).
        return "shared", True
    return "personal", is_owner


# ── single visible entry ─────────────────────────────────────────────────────


def resolve_viewable_entity(user: UserContext, entity_id: str) -> Optional[dict]:
    """The entry row ``user`` may see, or ``None``. Raises on a DB error.

    Compares ``entity_id::text = %s`` exactly as the card route does:
    ``entities.entity_id`` is ``UUID`` and a non-UUID path segment compared as
    ``entity_id = %s`` raises ``22P02`` (a 503 on PATCH instead of a 404).
    """
    ms = config.get_metadata_store()
    where_clause, where_params = visible_filter(user, scope_filter=None)
    return ms.fetch_one(
        "SELECT entity_id, name, entity_type, aliases, context_tags, scope, user_id, "
        "       published_from, is_staged, version, summary "
        "FROM entities WHERE " + where_clause + " AND entity_id::text = %s LIMIT 1",
        (*where_params, str(entity_id)),
    )


# ── list ─────────────────────────────────────────────────────────────────────


def list_entities(
    user: UserContext,
    *,
    kind: Optional[str] = None,
    shared: Optional[Literal["only", "exclude"]] = None,
    sort: Literal["mentions", "recent", "name"] = "mentions",
    limit: int = 50,
    offset: int = 0,
) -> EntityListResponse:
    """The entries ``user`` may see, with counts per kind. Raises on DB errors."""
    if sort not in _SORT_SQL:
        raise ValueError(f"invalid sort={sort!r}")
    if kind is not None and kind not in _KINDS:
        raise ValueError(f"invalid kind={kind!r}")
    if shared not in (None, "only", "exclude"):
        raise ValueError(f"invalid shared={shared!r}")

    caller = user.user_id
    vis_clause, vis_params = visible_filter(user, scope_filter=None, qualifier="e")
    # LUM-966: an entry the caller hid is out of the list and out of the kind counts.
    hidden_clause, hidden_params = not_hidden_sql(caller, qualifier="e")

    base = (
        "FROM entities e "
        "{lateral}"
        "WHERE " + vis_clause + " AND " + hidden_clause + " AND (e.is_staged IS NOT TRUE) "
        # LUM-581 collapse: hide the caller's OWN shared projection rows so they
        # see one entry (the personal source, marked share_status 'shared').
        "AND NOT (e.published_from IS NOT NULL AND e.user_id = %s) "
    )
    if shared == "only":
        base += "AND " + _IS_SHARED_SQL + " "
    elif shared == "exclude":
        base += "AND NOT " + _IS_SHARED_SQL + " "

    items_sql = (
        "SELECT e.entity_id, e.name, e.entity_type, e.aliases[1:5] AS aliases, "
        "       coalesce(cardinality(e.aliases), 0) AS alias_count, e.scope, e.user_id, "
        "       e.published_from, " + KIND_SQL + " AS kind, "
        "       r.n AS own_mentions, r.last_at AS own_last_mentioned_at "
        + base.format(lateral="LEFT JOIN LATERAL (" + OWN_MENTIONS_SQL + ") r ON TRUE ")
    )
    # Params in SQL text order: lateral caller, visibility, collapse caller.
    items_params: list = [caller, *vis_params, *hidden_params, caller]
    if kind is not None:
        items_sql += "AND " + KIND_SQL + " = %s "
        items_params.append(kind)
    items_sql += "ORDER BY " + _SORT_SQL[sort] + " LIMIT %s OFFSET %s"
    items_params.extend([limit, offset])

    counts_sql = (
        "SELECT "
        + KIND_SQL
        + " AS kind, count(*)::int AS n "
        + base.format(lateral="")
        + "GROUP BY 1"
    )
    counts_params = (*vis_params, *hidden_params, caller)

    ms = config.get_metadata_store()
    try:
        rows = ms.fetch_all(items_sql, tuple(items_params))
        count_rows = ms.fetch_all(counts_sql, counts_params)
    except Exception as exc:  # noqa: BLE001
        _log.warning("kg.list_entities: query failed user_id=%s", caller, exc_info=True)
        raise EntityStoreUnavailable("entity list unavailable") from exc

    shared_source_ids = _fetch_shared_entity_source_ids(caller, raise_on_error=True)

    counts = {k: 0 for k in _KINDS}
    for c in count_rows:
        if c["kind"] in counts:
            counts[c["kind"]] = int(c["n"])
    total = counts[kind] if kind is not None else sum(counts.values())

    items: list[EntityListItem] = []
    for r in rows:
        share_status, is_owner = _derive_entity_share_fields(
            r, caller_user_id=caller, shared_source_ids=shared_source_ids
        )
        scope = r.get("scope") or "personal"
        owned_personal = is_owner and scope == "personal"
        # count(*) over an all-NULL lateral row is 0, not NULL: other members'
        # rows must read None (their counts and dates are not exposed).
        items.append(
            EntityListItem(
                entity_id=str(r["entity_id"]),
                name=r["name"],
                type=r.get("entity_type"),
                kind=r["kind"],
                aliases=list(r.get("aliases") or []),
                alias_count=int(r.get("alias_count") or 0),
                scope=scope,
                share_status=share_status,  # type: ignore[arg-type]
                is_owner=is_owner,
                mention_count=int(r["own_mentions"] or 0) if owned_personal else None,
                last_mentioned_at=r.get("own_last_mentioned_at") if owned_personal else None,
            )
        )
    return EntityListResponse(entities=items, counts=EntityKindCounts(**counts), total=total)


# ── mentions ─────────────────────────────────────────────────────────────────


def _truncate(text: str) -> str:
    return text[:TITLE_MAX]


def _mention_title(kind: str, row: dict) -> str:
    if kind == "document":
        from services.documents import display_name_for

        return _truncate(
            display_name_for(
                {"display_name": row.get("display_name"), "file_path": row.get("file_path") or ""}
            )
        )
    if kind == "chat":
        return _truncate((row.get("summary") or "").strip() or "Chat")
    return "Added by an AI tool"


def _mention_arms(
    user: UserContext, *, source_id: str, owner: str, is_owner_view: bool
) -> tuple[str, tuple]:
    """The ``UNION ALL`` of one arm per evidence kind, with its params.

    Every arm joins to the item the **viewer** can open (``visible_filter`` on
    that item); a relation whose item no longer exists or is not visible
    produces no row. The AI-tool arm has no item to join and is owner-only.
    """
    fi_clause, fi_params = visible_filter(user, scope_filter=None, qualifier="fi")
    sv_clause, sv_params = visible_filter(user, scope_filter=None, qualifier="sv")
    arms = [
        # document: the viewer's own personal copy, else the shared copy.
        # SCOPE-EXEMPT: relations are read by (source entry, owner); the item
        # joins re-apply visible_filter for the viewer.
        "(SELECT DISTINCT ON (er.id) 'document'::text AS kind, fi.id::text AS ref_id, "
        "fi.display_name AS display_name, fi.file_path AS file_path, "
        "NULL::text AS summary, er.created_at AS mentioned_at, er.id AS relation_id "
        "FROM entity_relations er "
        "JOIN file_index src ON src.user_id = er.user_id AND src.file_path = er.evidence_id "
        "  AND src.published_from IS NULL "
        "JOIN file_index fi ON (fi.id = src.id OR fi.published_from = src.id) AND "
        + fi_clause
        + " WHERE er.evidence_type = 'DOCUMENT' AND er.source_id = %s AND er.user_id = %s "
        "ORDER BY er.id, (fi.scope = 'personal') DESC)",
        # chat
        "(SELECT DISTINCT ON (er.id) 'chat'::text AS kind, sv.session_id::text AS ref_id, "
        "NULL::text AS display_name, NULL::text AS file_path, "
        "sv.summary AS summary, er.created_at AS mentioned_at, er.id AS relation_id "
        "FROM entity_relations er "
        "JOIN sessions src ON src.session_id::text = er.evidence_id "
        "  AND src.user_id = er.user_id AND src.published_from IS NULL "
        "JOIN sessions sv ON (sv.session_id = src.session_id "
        "  OR sv.published_from = src.session_id) "
        "  AND " + sv_clause + " WHERE er.evidence_type = 'SESSION' "
        "AND er.source_id = %s AND er.user_id = %s "
        "ORDER BY er.id, (sv.scope = 'personal') DESC)",
    ]
    params: list = [
        *fi_params,
        source_id,
        owner,
        *sv_params,
        source_id,
        owner,
    ]
    if is_owner_view:
        arms.append(
            "(SELECT 'ai_tool'::text AS kind, er.evidence_id AS ref_id, "
            "NULL::text AS display_name, NULL::text AS file_path, NULL::text AS summary, "
            "er.created_at AS mentioned_at, er.id AS relation_id "
            "FROM entity_relations er "
            "WHERE er.evidence_type = 'MEMORY' AND er.source_id = %s AND er.user_id = %s)"
        )
        params.extend([source_id, owner])
    return " UNION ALL ".join(arms), tuple(params)


def list_mentions(
    user: UserContext,
    entity_id: str,
    *,
    limit: int = 50,
    offset: int = 0,
) -> Optional[EntityMentionsResponse]:
    """Items that mention an entry and that ``user`` can open; ``None`` → 404.

    ``user`` should be the route's viewer context (``allows_shared`` already
    resolved) so the several ``visible_filter`` calls below read ``users`` once.
    """
    try:
        row = resolve_viewable_entity(user, entity_id)
    except Exception:  # noqa: BLE001 — head lookup failure answers 404, as get_entity
        _log.warning("kg.list_mentions: head lookup failed entity_id=%s", entity_id, exc_info=True)
        return None
    if row is None:
        return None

    source_id = str(row.get("published_from") or row["entity_id"])
    owner = row["user_id"]
    is_owner_view = owner == user.user_id
    arms_sql, arms_params = _mention_arms(
        user, source_id=source_id, owner=owner, is_owner_view=is_owner_view
    )

    ms = config.get_metadata_store()
    try:
        count_rows = ms.fetch_all(
            "WITH m AS (" + arms_sql + ") SELECT kind, count(*)::int AS n FROM m GROUP BY kind",
            arms_params,
        )
        rows = ms.fetch_all(
            "WITH m AS (" + arms_sql + ") "
            "SELECT kind, ref_id, display_name, file_path, summary, mentioned_at FROM m "
            "ORDER BY mentioned_at DESC, kind, ref_id LIMIT %s OFFSET %s",
            (*arms_params, limit, offset),
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning("kg.list_mentions: query failed entity_id=%s", entity_id, exc_info=True)
        raise EntityStoreUnavailable("mentions unavailable") from exc

    counts = {"document": 0, "chat": 0, "ai_tool": 0}
    for c in count_rows:
        if c["kind"] in counts:
            counts[c["kind"]] = int(c["n"])
    mentions = [
        EntityMention(
            kind=r["kind"],
            ref_id=str(r["ref_id"]),
            title=_mention_title(r["kind"], r),
            mentioned_at=r["mentioned_at"],
        )
        for r in rows
    ]
    return EntityMentionsResponse(
        mentions=mentions,
        counts=EntityMentionCounts(**counts),
        total=sum(counts.values()),
    )


def list_mentions_since(
    user: UserContext,
    entity_id: str,
    *,
    after,
    exclude_relation_ids: list[int],
    limit: int = 200,
) -> list[EntityMovableMention]:
    """The owner's mentions of an entry recorded after ``after``, minus given rows.

    After an undo these are the mentions the survivor gained since the merge
    (``exclude_relation_ids`` holds the rows the merge itself moved), so the
    member can move any that belong to the restored entry. Owner-only: another
    member's view gets nothing. Never raises for a missing entry (returns
    ``[]``); a store failure raises :class:`EntityStoreUnavailable`.

    Known residual: an extraction already running when the merge started can
    write a mention whose ``created_at`` is earlier than the merge, which this
    list then misses. The mention stays on the survivor (no data loss); an id
    cut-off would not close the race, because sequence values are allocated
    before the foreign-key lock wait.
    """
    try:
        row = resolve_viewable_entity(user, entity_id)
    except Exception:  # noqa: BLE001
        _log.warning("kg.list_mentions_since: head lookup failed entity_id=%s", entity_id)
        return []
    if row is None or row["user_id"] != user.user_id:
        return []
    source_id = str(row.get("published_from") or row["entity_id"])
    arms_sql, arms_params = _mention_arms(
        user, source_id=source_id, owner=row["user_id"], is_owner_view=True
    )
    ms = config.get_metadata_store()
    try:
        rows = ms.fetch_all(
            "WITH m AS (" + arms_sql + ") "
            "SELECT kind, ref_id, display_name, file_path, summary, mentioned_at, relation_id "
            "FROM m WHERE mentioned_at > %s AND relation_id <> ALL(%s) "
            "ORDER BY mentioned_at DESC, relation_id DESC LIMIT %s",
            (*arms_params, after, list(exclude_relation_ids or []), limit),
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning("kg.list_mentions_since: query failed entity_id=%s", entity_id, exc_info=True)
        raise EntityStoreUnavailable("mentions unavailable") from exc
    return [
        EntityMovableMention(
            kind=r["kind"],
            ref_id=str(r["ref_id"]),
            title=_mention_title(r["kind"], r),
            mentioned_at=r["mentioned_at"],
            relation_id=int(r["relation_id"]),
        )
        for r in rows
    ]
