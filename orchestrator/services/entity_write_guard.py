# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""OCC write guard for entity summaries (LUM-358).

Read-version / guarded-commit helpers for ``entities.summary`` and
``staged_summary``. Consumers: LUM-108 write-back, LUM-109 consolidation.

Do **not** wrap guard calls in ``PostgresStore.transaction()`` on the shared
``_conn`` — ``transaction()`` is non-reentrant on the singleton connection.
"""

from __future__ import annotations

import logging
from typing import Literal
from uuid import UUID

from auth import UserContext
from models.entity_write import EntitySummarySnapshot
from models.entity_write import GuardedWriteResult
from visibility import visible_filter

import config

_log = logging.getLogger(__name__)

_Scope = Literal["personal", "shared", "system"]

_READ_COLUMNS = "entity_id, user_id, scope, entity_type, summary, staged_summary, version"


def read_entity_summary(
    entity_id: UUID,
    *,
    caller: UserContext,
) -> EntitySummarySnapshot | None:
    """Return visible entity summary snapshot, or ``None`` when absent / not visible."""
    store = config.get_metadata_store()
    vis_clause, vis_params = visible_filter(caller)
    sql = f"""
        SELECT {_READ_COLUMNS}
        FROM entities
        WHERE entity_id = %s AND {vis_clause}
    """
    row = store.fetch_one(sql, (entity_id, *vis_params))
    if row is None:
        _log.debug(
            "read_entity_summary: no visible row entity_id=%s caller=%s",
            entity_id,
            caller.user_id,
        )
        return None
    return EntitySummarySnapshot.model_validate(row)


def commit_summary_update(
    entity_id: UUID,
    *,
    caller: UserContext,
    read_version: int,
    new_summary: str,
) -> GuardedWriteResult:
    """Live summary commit under OCC; clears orphaned ``staged_summary`` on success."""
    snapshot = read_entity_summary(entity_id, caller=caller)
    if snapshot is None:
        return GuardedWriteResult(ok=False, conflict=True)
    if snapshot.scope == "system":
        _log.info("commit_summary_update: system scope read-only entity_id=%s", entity_id)
        return GuardedWriteResult(ok=False, conflict=False)
    return _guarded_update(
        entity_id,
        caller=caller,
        scope=snapshot.scope,
        read_version=read_version,
        set_clause="summary = %s, staged_summary = NULL, version = version + 1, updated_at = NOW()",
        extra_params=(new_summary,),
    )


def stage_consolidation_summary(
    entity_id: UUID,
    *,
    caller: UserContext,
    read_version: int,
    staged_summary: str,
) -> GuardedWriteResult:
    """Write ``staged_summary`` only — no version bump (consolidation serialises per lock)."""
    snapshot = read_entity_summary(entity_id, caller=caller)
    if snapshot is None:
        return GuardedWriteResult(ok=False, conflict=True)
    if snapshot.scope == "system":
        _log.info("stage_consolidation_summary: system scope read-only entity_id=%s", entity_id)
        return GuardedWriteResult(ok=False, conflict=False)
    return _guarded_update(
        entity_id,
        caller=caller,
        scope=snapshot.scope,
        read_version=read_version,
        set_clause="staged_summary = %s, updated_at = NOW()",
        extra_params=(staged_summary,),
        bump_version=False,
    )


def promote_staged_summary(
    entity_id: UUID,
    *,
    caller: UserContext,
    read_version: int,
) -> GuardedWriteResult:
    """Atomic promote: ``summary <- staged_summary``, clear staging, bump version."""
    snapshot = read_entity_summary(entity_id, caller=caller)
    if snapshot is None:
        return GuardedWriteResult(ok=False, conflict=True)
    if snapshot.scope == "system":
        _log.info("promote_staged_summary: system scope read-only entity_id=%s", entity_id)
        return GuardedWriteResult(ok=False, conflict=False)
    return _guarded_update(
        entity_id,
        caller=caller,
        scope=snapshot.scope,
        read_version=read_version,
        set_clause=(
            "summary = staged_summary, staged_summary = NULL, "
            "version = version + 1, updated_at = NOW()"
        ),
        extra_params=(),
        require_staged=True,
    )


def _guarded_update(
    entity_id: UUID,
    *,
    caller: UserContext,
    scope: _Scope,
    read_version: int,
    set_clause: str,
    extra_params: tuple,
    bump_version: bool = True,
    require_staged: bool = False,
) -> GuardedWriteResult:
    store = config.get_metadata_store()
    where_scope, scope_params = _scope_where(scope, caller)
    staged_clause = " AND staged_summary IS NOT NULL" if require_staged else ""
    sql = f"""
        UPDATE entities
        SET {set_clause}
        WHERE entity_id = %s AND {where_scope} AND version = %s{staged_clause}
        RETURNING version
    """
    params = extra_params + (entity_id,) + scope_params + (read_version,)
    row = store.fetch_one(sql, params)
    if row is None:
        _log.info(
            "guarded update conflict entity_id=%s scope=%s read_version=%s",
            entity_id,
            scope,
            read_version,
        )
        return GuardedWriteResult(ok=False, conflict=True, new_version=None)
    new_version = int(row["version"])
    if not bump_version and new_version != read_version:
        pass  # staging path: version unchanged by design
    return GuardedWriteResult(ok=True, conflict=False, new_version=new_version)


def _scope_where(
    scope: _Scope,
    caller: UserContext,
) -> tuple[str, tuple]:
    if scope == "personal":
        return ("scope = 'personal' AND user_id = %s", (caller.user_id,))
    if scope == "shared":
        return ("scope = 'shared'", ())
    raise ValueError(f"unsupported write scope: {scope!r}")
