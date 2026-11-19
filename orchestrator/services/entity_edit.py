# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""The one write path for correcting an entry: rename + other names (LUM-161), kind (LUM-967).

``rename_entity`` is Postgres-first. Inside one transaction (serialised per
member by an advisory lock) it re-reads the row ``FOR UPDATE``, checks the
version, applies the member's **alias deltas** to the locked row, refuses a new
name that another of the member's entries already uses, and writes with a
version bump and ``graph_projected_at = NULL``. Only after the commit does it
refresh the personal Qdrant point and any shared projections, both best-effort
and never raising: the graph follows at the next reconcile pass (ADR 263, the
daily ``run_reconciliation``).

``change_entity_kind`` (LUM-967) is the second write path: the same lock,
``FOR UPDATE`` re-read and version check, a refusal when another of the member's
entries of the **target** kind already uses the name or an other name, and — in
the same transaction — a per-member name rule
(:mod:`services.entity_name_rules`) so a re-read cannot bring the wrong kind
back. The graph follows through the stale rule: the node is relabelled in place
the next time it is projected.

LUM-965 (merge, undo, move mentions) extends this module rather than adding a
second write path: :func:`merge_entries`, :func:`undo_merge`,
:func:`list_merge_history` and :func:`move_mentions` run the engine in
:mod:`services.entity_merge` inside the same transaction shape (head checks,
the per-member advisory lock, ``FOR UPDATE`` re-read of **both** rows, version
check on both) and refresh the search points and shared copies after the commit.
:class:`EntityNameInUse` is where a merge is offered.

LUM-966 adds **delete, hide and unhide** here, and the member's list of names
Lumogis won't learn. :func:`delete_entry` removes the entry, its mentions, typed
links, merge records and household copy in one transaction and puts its names on
the member's won't-learn list (``entity_name_rules`` blocks), so a re-read cannot
bring it back; Qdrant and the graph are cleared after the commit
(:mod:`services.entity_deletions`). A member who does not own a shared entry gets
:func:`hide_entry` instead: her view only, undone by :func:`unhide_entry`.
"""

from __future__ import annotations

import logging
import unicodedata
import uuid
from dataclasses import dataclass
from dataclasses import field
from typing import Callable
from typing import Literal
from typing import Optional

import psycopg2
from auth import UserContext
from ports.metadata_store import PoolExhaustedError
from services.entity_browser import EntityStoreUnavailable  # noqa: F401 — re-export
from visibility import visible_filter

import config
from services import entity_browser
from services import entity_constraints
from services import entity_deletions
from services import entity_merge
from services import entity_name_rules

_log = logging.getLogger(__name__)

# Stable salt; grep repo for ``pg_advisory`` before changing.
ENTITY_NAMES_ADVISORY_KEY1 = 8421161

MAX_ALIASES = 50

# The kinds a member can change an entry to. A Core-side tuple on purpose (the UI
# offers the base four even after LUM-533 widens the graph map); a lockstep test
# keeps it inside the graph's ENTITY_TYPE_MAP and the MCP ENTITY_TYPES.
ALLOWED_KINDS: tuple[str, ...] = ("PERSON", "ORG", "PROJECT", "CONCEPT")

# Test seam for the live concurrency test: called (when set) after the UPDATE
# and before the transaction commits. Production never sets it.
_before_commit: Callable[[], None] | None = None

_BIDI_CONTROLS = frozenset(
    {chr(c) for c in range(0x202A, 0x202F)} | {chr(c) for c in range(0x2066, 0x206A)}
)


class EntityNotFound(Exception):
    """No entry with this id is visible to the caller (route → 404)."""

    def __init__(self, entity_id: str = "", which: str = "this"):
        super().__init__(entity_id)
        self.which = which


class EntityNotOwner(Exception):
    """The entry is visible but belongs to another member (route → 403)."""

    def __init__(self, entity_id: str = "", which: str = "this"):
        super().__init__(entity_id)
        self.which = which


class EntityNotEditable(Exception):
    """A shared/system projection row; edit its personal source (route → 403)."""

    def __init__(self, source_entity_id: Optional[str] = None, which: str = "this"):
        super().__init__("not_editable")
        self.source_entity_id = source_entity_id
        self.which = which


class EntityVersionConflict(Exception):
    """The row changed since the caller read it (route → 409)."""

    def __init__(self, current_version: int, which: str = "this"):
        super().__init__(f"version_conflict current={current_version}")
        self.current_version = current_version
        self.which = which


class EntitySameEntry(Exception):
    """Both ids name the same entry (route → 422 same_entry)."""


class EntityStaged(Exception):
    """An entry still being checked cannot be merged yet (route → 422 staged_entry)."""

    def __init__(self, which: str = "this"):
        super().__init__("staged_entry")
        self.which = which


class MergeNotFound(Exception):
    """Unknown, malformed or another member's merge record (route → 404)."""


class MergeAlreadyUndone(Exception):
    """The merge was undone already (route → 409 already_undone)."""


class MergeNotNewest(Exception):
    """A newer open merge into the same survivor must be undone first (route → 409)."""

    def __init__(self, newer_merge_id: str, newer_other_name: str):
        super().__init__("undo_newer_first")
        self.newer_merge_id = newer_merge_id
        self.newer_other_name = newer_other_name


class MergeLoserIdTaken(Exception):
    """An entry with the recorded id exists already (route → 409 loser_id_taken)."""


class MentionsNotFound(Exception):
    """A requested mention is not a row of the source entry (route → 422 relation_not_found)."""


class EntityNameInUse(Exception):
    """Another of the caller's entries already uses a new surface form (route → 409)."""

    def __init__(
        self,
        conflicting_entity_id: str,
        conflicting_name: str,
        surface_form: str,
        conflicting_is_staged: bool = False,
        *,
        conflicting_entity_type: Optional[str] = None,
    ):
        super().__init__(f"name_in_use {surface_form!r}")
        self.conflicting_entity_id = conflicting_entity_id
        self.conflicting_name = conflicting_name
        self.surface_form = surface_form
        self.conflicting_is_staged = conflicting_is_staged
        # Set only by a kind change (the clash is with an entry of the target kind).
        self.conflicting_entity_type = conflicting_entity_type


class EntityHideOwn(Exception):
    """Hide is for another member's entry; delete your own (route → 422 own_entry)."""


class NameRuleNotFound(Exception):
    """The caller has no such rule (route → 404 rule_not_found)."""


class EntityKindInvalid(Exception):
    """The requested kind is not one of :data:`ALLOWED_KINDS` (route → 422 invalid_kind)."""


class EntityNameInvalid(Exception):
    """reason ∈ empty_name | invalid_characters | too_many_aliases (route → 422)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# ── normalisation (pure) ─────────────────────────────────────────────────────


def normalise_name(s: str) -> str:
    """Trim + collapse whitespace; refuse control and bidi-override characters.

    Whitespace controls (tab, newline, …) are collapsed like any other space;
    every other ``Cc`` character (incl. NUL) and the bidi embeddings / overrides /
    isolates are refused, so a household list cannot show a name that reorders
    the text around it.
    """
    for ch in s:
        if ch in _BIDI_CONTROLS or (unicodedata.category(ch) == "Cc" and not ch.isspace()):
            raise EntityNameInvalid("invalid_characters")
    return " ".join(s.split())


def normalise_names(name: str, aliases: list[str]) -> tuple[str, list[str]]:
    """Normalise a name and its other names.

    Aliases are trimmed, emptied ones and those equal to the name (under
    ``str.lower()``, which agrees with the SQL ``lower()`` extraction matches
    on) dropped, and the rest de-duplicated keeping the first occurrence and its
    casing. More than :data:`MAX_ALIASES` → ``too_many_aliases``.
    """
    final_name = normalise_name(name)
    if not final_name:
        raise EntityNameInvalid("empty_name")
    seen = {final_name.lower()}
    out: list[str] = []
    for raw in aliases:
        alias = normalise_name(raw)
        if not alias or alias.lower() in seen:
            continue
        seen.add(alias.lower())
        out.append(alias)
    if len(out) > MAX_ALIASES:
        raise EntityNameInvalid("too_many_aliases")
    return final_name, out


# ── post-commit refreshes (best-effort, never raise) ─────────────────────────


def _refresh_search_point(row: dict) -> bool:
    """Replace the entry's personal Qdrant point(s) with one carrying its name.

    Embeds **first**: if embedding fails the old points stay (the entry remains
    findable under its old name) and nothing is deleted. Then deletes by
    ``entity_id`` + ``user_id`` (no ``scope`` clause, so merge-written and older
    points without a ``scope`` payload go too; shared projection points carry
    their own ``entity_id`` and are untouched) and upserts one point.

    Residual: an upsert failing right after a successful delete leaves no
    personal point until the next rename or merge — there is no Qdrant
    reconcile for ``entities``. Logged at ERROR. Considered, not filed (LUM-161).
    """
    from services.entities import personal_entity_point

    entity_id = str(row["entity_id"])
    user_id = row["user_id"]
    try:
        point_id, embed_text, payload = personal_entity_point(
            entity_id=entity_id,
            point_name=row["name"],
            name=row["name"],
            entity_type=row["entity_type"],
            aliases=list(row.get("aliases") or []),
            context_tags=list(row.get("context_tags") or []),
            user_id=user_id,
        )
        vector = config.get_embedder().embed(embed_text)
        vs = config.get_vector_store()
        vs.delete_where(
            collection="entities",
            filter={
                "must": [
                    {"key": "entity_id", "match": {"value": entity_id}},
                    {"key": "user_id", "match": {"value": user_id}},
                ]
            },
        )
        vs.upsert(collection="entities", id=point_id, vector=vector, payload=payload)
        return True
    except Exception:  # noqa: BLE001
        _log.exception("entity_edit: search point refresh failed entity_id=%s", entity_id)
        return False


def _refresh_shared_projections(row: dict, user: UserContext) -> bool:
    """Re-project the edited entry into each of its shared/system projections.

    One ``try`` per projection so a failure on one does not skip the next.
    ``project_entity`` re-calls are the refresh (Postgres row + Qdrant point);
    ``graph_projected_at`` is then cleared so the shared reconcile arm re-projects
    the node on the same rule as the personal row. Staged entries have no
    projections.
    """
    if row.get("is_staged"):
        return True
    from services import projection

    ms = config.get_metadata_store()
    source_id = str(row["entity_id"])
    user_id = row["user_id"]
    try:
        projections = ms.fetch_all(
            # SCOPE-EXEMPT: caller's own shared/system projections of their personal row.
            "SELECT scope, share_origin FROM entities "
            "WHERE published_from = %s AND user_id = %s AND scope IN ('shared','system')",
            (source_id, user_id),
        )
    except Exception:  # noqa: BLE001
        _log.exception("entity_edit: shared refresh failed scope=? source=%s", source_id)
        return False

    ok = True
    for proj in projections:
        scope = proj["scope"]
        try:
            origin = proj.get("share_origin")
            projection.project_entity(
                row,
                target_scope=scope,
                actor=UserContext(user_id=user_id, is_authenticated=True),
                share_origin=origin if origin in ("document", "user") else "user",
            )
            ms.execute(
                # SCOPE-EXEMPT: caller's own projection row of their personal entry.
                "UPDATE entities SET graph_projected_at = NULL "
                "WHERE published_from = %s AND user_id = %s AND scope = %s",
                (source_id, user_id, scope),
            )
        except Exception:  # noqa: BLE001
            ok = False
            _log.exception(
                "entity_edit: shared refresh failed scope=%s source=%s", scope, source_id
            )
    return ok


# ── rename ───────────────────────────────────────────────────────────────────

# Serialises renames per member so two concurrent renames cannot both pass the
# collision check; released at commit/rollback. A module constant so the live
# concurrency test can swap it for a no-op as its negative control.
_LOCK_SQL = "SELECT pg_advisory_xact_lock(%s::integer, hashtext(%s))"

_LOCKED_ROW_SQL = (
    "SELECT entity_id, name, entity_type, aliases, context_tags, mention_count, "
    "       extraction_quality, version, user_id, scope, published_from, is_staged, summary "
    "FROM entities "
    # SCOPE-EXEMPT: caller's own personal source row, re-read FOR UPDATE.
    "WHERE entity_id = %s AND user_id = %s AND scope = 'personal' "
    "  AND published_from IS NULL FOR UPDATE"
)

# One template, two renderings. ``{cols}`` adds the kind to the selected columns
# and ``{kind}`` restricts the check to entries of the target kind (extra ``%s``
# directly after ``entity_id <> %s``, so the parameter order is
# ``(user_id, pk, target, forms, forms)`` for a kind change).
_COLLISION_TEMPLATE = (
    "SELECT {cols} FROM entities "
    # SCOPE-EXEMPT: caller's own personal rows (collision check).
    "WHERE user_id = %s AND scope = 'personal' AND published_from IS NULL "
    "  AND entity_id <> %s "
    "{kind}"
    "  AND (lower(name) = ANY(SELECT lower(x) FROM unnest(%s::text[]) x) "
    "       OR EXISTS (SELECT 1 FROM unnest(aliases) a "
    "                  WHERE lower(a) = ANY(SELECT lower(x) FROM unnest(%s::text[]) x))) "
    "ORDER BY mention_count DESC, entity_id LIMIT 1"
)
_COLLISION_SQL = _COLLISION_TEMPLATE.format(cols="entity_id, name, aliases, is_staged", kind="")
_KIND_COLLISION_SQL = _COLLISION_TEMPLATE.format(
    cols="entity_id, name, aliases, is_staged, entity_type",
    kind="  AND upper(entity_type) = %s ",
)

# Which of these forms does ANOTHER live personal entry of kind K use as a name or
# other name? (parameters: ``(forms, user_id, pk, K)``). A sibling of
# ``_COLLISION_TEMPLATE`` with the same predicates, but it answers *which forms*,
# not one hit. Staged entries count as live. The kind expression mirrors
# ``entity_name_rules.kind_key`` so a blank-kind entry counts as CONCEPT.
# NOTE (residual 7, LUM-966): ``lower()`` here vs ``name_key`` (NFC + collapse) for
# the rules: they agree for every name the UI can create and differ only for
# decomposed Unicode forms.
_KIND_FORMS_IN_USE_SQL = (
    "SELECT f AS form FROM unnest(%s::text[]) f "
    "WHERE EXISTS ("
    "  SELECT 1 FROM entities e "
    # SCOPE-EXEMPT: caller's own personal rows (delete skip check).
    "  WHERE e.user_id = %s AND e.scope = 'personal' AND e.published_from IS NULL "
    "    AND e.entity_id <> %s "
    "    AND upper(coalesce(nullif(btrim(e.entity_type), ''), 'CONCEPT')) = %s "
    "    AND (lower(e.name) = lower(f) "
    "         OR EXISTS (SELECT 1 FROM unnest(e.aliases) a WHERE lower(a) = lower(f))))"
)

_UPDATE_SQL = (
    "UPDATE entities SET name = %s, aliases = %s, version = version + 1, "
    "       updated_at = NOW(), graph_projected_at = NULL "
    # SCOPE-EXEMPT: caller's own personal source row, already locked.
    "WHERE entity_id = %s AND user_id = %s AND version = %s RETURNING *"
)

_KIND_UPDATE_SQL = (
    "UPDATE entities SET entity_type = %s, version = version + 1, "
    "       updated_at = NOW(), graph_projected_at = NULL "
    # SCOPE-EXEMPT: caller's own personal source row, already locked.
    "WHERE entity_id = %s AND user_id = %s AND version = %s RETURNING *"
)


def _resolve_own_personal_row(user: UserContext, entity_id: str) -> dict:
    """Head checks shared by every edit: visible → owned → a personal source row.

    Raises :class:`EntityNotFound` (not visible; another member's personal entry
    is invisible, so no existence oracle), :class:`EntityNotOwner` (a shared
    projection of another member's entry), :class:`EntityNotEditable` (the
    caller's own shared/system projection; carries the source id) or
    :class:`EntityStoreUnavailable` (lookup outage).
    """
    try:
        row = entity_browser.resolve_viewable_entity(user, entity_id)
    except Exception as exc:  # noqa: BLE001
        _log.warning("entity_edit: head lookup failed entity_id=%s", entity_id, exc_info=True)
        raise EntityStoreUnavailable("entity lookup unavailable") from exc
    if row is None:
        raise EntityNotFound(entity_id)
    if row["user_id"] != user.user_id:
        raise EntityNotOwner(entity_id)
    if row.get("scope") != "personal" or row.get("published_from") is not None:
        src = row.get("published_from")
        raise EntityNotEditable(str(src) if src is not None else None)
    return row


def rename_entity(
    user: UserContext,
    entity_id: str,
    *,
    name: Optional[str],
    aliases_add: list[str],
    aliases_remove: list[str],
    version: int,
) -> dict:
    """Rename an entry and/or change its other names. Returns the personal row.

    Alias changes are deltas applied to the locked row (an alias extraction
    added while the dialog was open survives). The old name is kept as an
    alias unless it is in ``aliases_remove``. Only **new** surface forms are
    checked for collisions: existing data already holds duplicates (the
    ``ambiguous`` extraction path), and checking the kept old name would block
    every rename of such an entry.
    """
    row = _resolve_own_personal_row(user, entity_id)

    pk = str(row["entity_id"])  # a real UUID, never the raw path string
    user_id = user.user_id
    ms = config.get_metadata_store()
    updated: dict
    changed = True
    try:
        with ms.transaction():
            ms.execute("SET LOCAL lock_timeout = '5s'")
            # Serialises renames per member so two concurrent renames cannot both
            # pass the collision check; released at commit/rollback.
            ms.fetch_one(_LOCK_SQL, (ENTITY_NAMES_ADVISORY_KEY1, user_id))
            cur = ms.fetch_one(_LOCKED_ROW_SQL, (pk, user_id))
            if cur is None:
                raise EntityNotFound(entity_id)
            if int(cur["version"]) != int(version):
                raise EntityVersionConflict(int(cur["version"]))

            cur_name = cur["name"]
            cur_aliases = list(cur.get("aliases") or [])
            remove = {" ".join(a.split()).lower() for a in aliases_remove}
            final_name = normalise_name(name) if name is not None else cur_name
            final_aliases = [a for a in cur_aliases if a.lower() not in remove]
            final_aliases.extend(aliases_add)
            if final_name.lower() != cur_name.lower() and cur_name.lower() not in remove:
                final_aliases.append(cur_name)
            final_name, final_aliases = normalise_names(final_name, final_aliases)

            if final_name == cur_name and final_aliases == cur_aliases:
                updated = cur
                changed = False
            else:
                known = {cur_name.lower(), *(a.lower() for a in cur_aliases)}
                new_forms = [f for f in (final_name, *final_aliases) if f.lower() not in known]
                if new_forms:
                    hit = ms.fetch_one(_COLLISION_SQL, (user_id, pk, new_forms, new_forms))
                    if hit is not None:
                        hit_forms = {
                            hit["name"].lower(),
                            *(a.lower() for a in hit["aliases"] or []),
                        }
                        surface = next(
                            (f for f in new_forms if f.lower() in hit_forms), new_forms[0]
                        )
                        raise EntityNameInUse(
                            str(hit["entity_id"]),
                            hit["name"],
                            surface,
                            bool(hit.get("is_staged")),
                        )
                new_row = ms.fetch_one(
                    _UPDATE_SQL, (final_name, final_aliases, pk, user_id, version)
                )
                if new_row is None:
                    raise EntityVersionConflict(int(cur["version"]))
                updated = new_row
                if new_forms:
                    # The member explicitly gives this entry a name: a block on that
                    # name at this entry's kind (left by a delete) no longer applies.
                    entity_name_rules.lift_blocks(
                        ms,
                        user_id=user_id,
                        keys=[entity_name_rules.name_key(f) for f in new_forms],
                        entity_type=entity_name_rules.kind_key(cur.get("entity_type")),
                    )
                if _before_commit is not None:
                    _before_commit()
    except (psycopg2.Error, PoolExhaustedError) as exc:
        _log.warning("entity_edit: rename transaction failed entity_id=%s", pk, exc_info=True)
        raise EntityStoreUnavailable("rename unavailable") from exc

    if changed:
        _refresh_search_point(updated)
        _refresh_shared_projections(updated, user)
    return updated


# ── change kind (LUM-967) ────────────────────────────────────────────────────


def change_entity_kind(
    user: UserContext, entity_id: str, *, entity_type: str, version: int
) -> dict:
    """Change what kind an entry is. Returns the personal row.

    The entry keeps its id, name, other names, documents and links. In one
    transaction (the same per-member lock as a rename, so the two serialise
    against each other: both read the collision set) it re-reads the row
    ``FOR UPDATE``, checks the version, refuses the change when another of the
    member's entries **of the target kind** already uses the name or an other
    name (``EntityNameInUse`` with ``conflicting_entity_type``; merging is
    LUM-965), writes the new kind with a version bump and
    ``graph_projected_at = NULL``, and records the per-member name rule. The rule
    is atomic with the change on purpose: a kind change without its rule would
    be undone by the next re-read.

    Choosing the kind the entry already has is a no-op. After the commit the
    search point and shared projections are refreshed (best-effort, never
    raising) and stale PERSON constraint violations are re-checked; the graph
    node is relabelled in place the next time it is projected.
    """
    target = (entity_type or "").strip().upper()
    if target not in ALLOWED_KINDS:
        raise EntityKindInvalid("invalid_kind")

    row = _resolve_own_personal_row(user, entity_id)

    pk = str(row["entity_id"])
    user_id = user.user_id
    ms = config.get_metadata_store()
    updated: dict
    changed = True
    try:
        with ms.transaction():
            ms.execute("SET LOCAL lock_timeout = '5s'")
            ms.fetch_one(_LOCK_SQL, (ENTITY_NAMES_ADVISORY_KEY1, user_id))
            cur = ms.fetch_one(_LOCKED_ROW_SQL, (pk, user_id))
            if cur is None:
                raise EntityNotFound(entity_id)
            if int(cur["version"]) != int(version):
                raise EntityVersionConflict(int(cur["version"]))

            # An empty stored kind is CONCEPT, which is how the graph labels it; treating
            # it so up front makes "blank -> CONCEPT" a no-op (a rule CONCEPT -> CONCEPT
            # would violate the table's no-self CHECK and 503 forever).
            current = entity_name_rules.kind_key(cur.get("entity_type"))
            if current == target:
                updated = cur
                changed = False
            else:
                forms = [cur["name"], *(cur.get("aliases") or [])]
                hit = ms.fetch_one(_KIND_COLLISION_SQL, (user_id, pk, target, forms, forms))
                if hit is not None:
                    hit_forms = {
                        hit["name"].lower(),
                        *(a.lower() for a in hit["aliases"] or []),
                    }
                    surface = next((f for f in forms if f.lower() in hit_forms), forms[0])
                    raise EntityNameInUse(
                        str(hit["entity_id"]),
                        hit["name"],
                        surface,
                        bool(hit.get("is_staged")),
                        conflicting_entity_type=hit.get("entity_type"),
                    )
                new_row = ms.fetch_one(_KIND_UPDATE_SQL, (target, pk, user_id, version))
                if new_row is None:
                    raise EntityVersionConflict(int(cur["version"]))
                updated = new_row
                if entity_name_rules.name_key(cur["name"]):
                    entity_name_rules.record_retype(
                        ms,
                        user_id=user_id,
                        name=cur["name"],
                        from_type=current,
                        to_type=target,
                        source_entity_id=pk,
                    )
                else:
                    # Legacy blank name: the table refuses an empty key.
                    _log.debug(
                        "entity_edit: kind change without rule (blank name) entity_id=%s", pk
                    )
                # The canonical name's block at the new kind went with record_retype's
                # step 1b; the other names are lifted here (an explicit kind assertion).
                entity_name_rules.lift_blocks(
                    ms,
                    user_id=user_id,
                    keys=[entity_name_rules.name_key(a) for a in (cur.get("aliases") or [])],
                    entity_type=target,
                )
                if _before_commit is not None:
                    _before_commit()
    except (psycopg2.Error, PoolExhaustedError) as exc:
        _log.warning("entity_edit: kind change transaction failed entity_id=%s", pk, exc_info=True)
        raise EntityStoreUnavailable("kind change unavailable") from exc

    if changed:
        _refresh_search_point(updated)
        _refresh_shared_projections(updated, user)
        try:
            entity_constraints.run_batch_constraints([pk], user_id)
        except Exception:  # noqa: BLE001 - the change has committed; never a 500
            _log.exception("entity_edit: constraint re-check failed entity_id=%s", pk)
    return updated


# ── delete, hide, won't-learn list (LUM-966) ─────────────────────────────────


@dataclass
class DeleteOutcome:
    entity_id: str
    blocked_names: list[str] = field(default_factory=list)
    not_blocked: list[dict] = field(default_factory=list)
    household_copy_removed: bool = False
    merges_removed: int = 0


def _dedupe_forms(cur: dict) -> list[tuple[str, str]]:
    """``[(rule key, display form)]`` for the canonical name then each other name.

    Deduped by :func:`entity_name_rules.name_key`, first spelling wins, empty keys
    skipped. That exact display string is both the response's ``blocked_names``
    entry and the stored ``display_name``, so the notice and the settings page agree.
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in [cur.get("name") or "", *(cur.get("aliases") or [])]:
        key = entity_name_rules.name_key(raw)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append((key, " ".join(raw.split())))
    return out


def delete_entry(user: UserContext, entity_id: str, *, version: int) -> DeleteOutcome:
    """Delete the caller's own entry and keep Lumogis from learning its names again.

    One transaction, under the same per-member lock as every other edit, with a
    ``FOR UPDATE`` re-read and the version check: the entry, its mentions, typed
    links, merge records, household copy and ``sessions`` references go, and each
    of its names (canonical name and other names, at its kind) becomes a **block**
    on the member's won't-learn list. A name another live entry of the same kind
    uses is *not* blocked (that entry keeps learning it), nor one where a kind
    correction already sends the name elsewhere; both come back in
    ``not_blocked``. Qdrant and the graph are cleared after the commit
    (:mod:`services.entity_deletions`); the response never waits on them.

    NOTE (residual 5, LUM-966): extraction takes no member lock. A batch whose
    rules were read before this delete committed can recreate the entry once;
    deleting it again holds, because the block now exists.

    Raises :class:`EntityNotFound`, :class:`EntityNotOwner`,
    :class:`EntityNotEditable`, :class:`EntityVersionConflict` and
    :class:`EntityStoreUnavailable` (store outage, including a lock wait over 5 s).
    """
    row = _resolve_own_personal_row(user, entity_id)
    pk = str(row["entity_id"])
    user_id = user.user_id
    kind = entity_name_rules.kind_key(row.get("entity_type"))
    ms = config.get_metadata_store()
    graph_enabled = config.get_graph_mode() != "disabled"
    try:
        with ms.transaction():
            ms.execute("SET LOCAL lock_timeout = '5s'")
            ms.fetch_one(_LOCK_SQL, (ENTITY_NAMES_ADVISORY_KEY1, user_id))
            cur = ms.fetch_one(_LOCKED_ROW_SQL, (pk, user_id))
            if cur is None:
                raise EntityNotFound(entity_id)
            if int(cur["version"]) != int(version):
                raise EntityVersionConflict(int(cur["version"]))
            kind = entity_name_rules.kind_key(cur.get("entity_type"))

            forms = _dedupe_forms(cur)
            evidence_rows = ms.fetch_all(
                "SELECT evidence_id FROM entity_relations "
                # SCOPE-EXEMPT: the caller's own entry's mentions, bound by user_id.
                "WHERE source_id = %s AND user_id = %s AND upper(evidence_type) = 'DOCUMENT' "
                "GROUP BY evidence_id ORDER BY max(created_at) DESC LIMIT 500",
                (pk, user_id),
            )
            document_evidence = [r["evidence_id"] for r in evidence_rows]
            projection_ids = [
                str(r["entity_id"])
                for r in ms.fetch_all(
                    "SELECT entity_id::text AS entity_id FROM entities "
                    # SCOPE-EXEMPT: the caller's own household copies of this entry.
                    "WHERE published_from = %s AND user_id = %s",
                    (pk, user_id),
                )
            ]
            merges = int(
                (
                    ms.fetch_one(
                        "SELECT count(*) AS n FROM entity_merges "
                        # SCOPE-EXEMPT: entity_merges is per-member (no scope column).
                        "WHERE user_id = %s AND (winner_id = %s OR loser_id = %s)",
                        (user_id, pk, pk),
                    )
                    or {}
                ).get("n")
                or 0
            )

            # Which names are NOT blocked, and why.
            not_blocked: list[dict] = []
            skip: set[str] = set()
            if forms:
                names = [display for _key, display in forms]
                in_use = {
                    " ".join(r["form"].split()).lower()
                    for r in ms.fetch_all(_KIND_FORMS_IN_USE_SQL, (names, user_id, pk, kind))
                }
                corrected = {
                    r["name_norm"]
                    for r in ms.fetch_all(
                        "SELECT name_norm FROM entity_name_rules "
                        # SCOPE-EXEMPT: entity_name_rules is per-member (no scope column).
                        "WHERE user_id = %s AND action = 'retype' AND from_type = %s "
                        "AND name_norm = ANY(%s)",
                        (user_id, kind, [k for k, _ in forms]),
                    )
                }
                for key, display in forms:
                    if display.lower() in in_use:
                        not_blocked.append({"name": display, "reason": "used_by_other_entry"})
                        skip.add(key)
                    elif key in corrected:
                        not_blocked.append({"name": display, "reason": "already_corrected"})
                        skip.add(key)
            to_block = [(k, d) for k, d in forms if k not in skip]
            if to_block:
                entity_name_rules.record_block(
                    ms,
                    user_id=user_id,
                    keys_and_names=to_block,
                    entity_type=kind,
                    source_entity_id=pk,
                    document_evidence=document_evidence,
                )

            # No FK from these: entity_edges (TEXT ids), sessions.entity_ids,
            # entity_merges.loser_id. Everything else cascades from the entities row.
            ms.execute(
                "DELETE FROM entity_edges "
                # SCOPE-EXEMPT: entity_edges is per-member; user_id-bound.
                "WHERE user_id = %s AND (src_entity_id = %s OR dst_entity_id = %s)",
                (user_id, pk, pk),
            )
            ms.execute(
                "UPDATE sessions SET entity_ids = array_remove(entity_ids, %s) "
                # SCOPE-EXEMPT: sessions are per-member; user_id-bound.
                "WHERE user_id = %s AND %s = ANY(entity_ids)",
                (pk, user_id, pk),
            )
            ms.execute(
                "DELETE FROM entity_merges "
                # SCOPE-EXEMPT: entity_merges is per-member (no scope column).
                "WHERE user_id = %s AND loser_id = %s",
                (user_id, pk),
            )
            ms.execute(
                "DELETE FROM entities "
                # SCOPE-EXEMPT: caller's own personal source row, already locked.
                "WHERE entity_id = %s AND user_id = %s",
                (pk, user_id),
            )
            left = ms.fetch_one(
                "SELECT 1 AS x FROM entities "
                # SCOPE-EXEMPT: postcondition: neither the row nor a copy may remain.
                "WHERE entity_id = %s OR published_from = %s LIMIT 1",
                (pk, pk),
            )
            if left:
                raise RuntimeError("entity delete postcondition failed: a row remains")
            tombstone = entity_deletions.Tombstone(
                entity_id=pk, user_id=user_id, projection_ids=projection_ids
            )
            entity_deletions.insert_tombstone(ms, tombstone, graph_enabled=graph_enabled)
            if _before_commit is not None:
                _before_commit()
    except (psycopg2.Error, PoolExhaustedError) as exc:
        _log.warning("entity_edit: delete transaction failed entity_id=%s", pk, exc_info=True)
        raise EntityStoreUnavailable("delete unavailable") from exc

    try:
        entity_deletions.after_delete_commit(tombstone)
    except Exception:
        # The delete is committed. A refused background submit (executor shut down)
        # must not turn it into a 500: the tombstone is unstamped, so the sweep
        # clears the points and the graph node.
        _log.warning(
            "entity_edit: after-delete work not started entity_id=%s; the sweep will retry",
            pk,
            exc_info=True,
        )
    _log.info(
        "entity_edit: deleted entity_id=%s user_id=%s blocked=%d not_blocked=%d "
        "household_copies=%d merges=%d",
        pk,
        user_id,
        len(to_block),
        len(not_blocked),
        len(projection_ids),
        merges,
    )
    return DeleteOutcome(
        entity_id=pk,
        blocked_names=[d for _k, d in to_block],
        not_blocked=not_blocked,
        household_copy_removed=bool(projection_ids),
        merges_removed=merges,
    )


def hide_entry(user: UserContext, entity_id: str) -> str:
    """Hide another member's shared entry from the caller's own view. Idempotent.

    Returns the key stored (the owner's source row). Raises
    :class:`EntityNotFound` (not visible) and :class:`EntityHideOwn` (her own
    entry: delete it instead).
    """
    key = _hide_key(user, entity_id)
    ms = config.get_metadata_store()
    try:
        ms.execute(
            "INSERT INTO entity_hidden (user_id, entity_key) VALUES (%s, %s::uuid) "
            # SCOPE-EXEMPT: entity_hidden is a per-member view table (no scope column).
            "ON CONFLICT DO NOTHING",
            (user.user_id, key),
        )
    except (psycopg2.Error, PoolExhaustedError) as exc:
        _log.warning("entity_edit: hide failed entity_id=%s", key, exc_info=True)
        raise EntityStoreUnavailable("hide unavailable") from exc
    return key


def unhide_entry(user: UserContext, entity_id: str) -> bool:
    """Undo :func:`hide_entry`. Idempotent: no row is still success."""
    key = _hide_key(user, entity_id)
    ms = config.get_metadata_store()
    try:
        ms.execute(
            "DELETE FROM entity_hidden "
            # SCOPE-EXEMPT: entity_hidden is a per-member view table (no scope column).
            "WHERE user_id = %s AND entity_key = %s::uuid",
            (user.user_id, key),
        )
    except (psycopg2.Error, PoolExhaustedError) as exc:
        _log.warning("entity_edit: unhide failed entity_id=%s", key, exc_info=True)
        raise EntityStoreUnavailable("unhide unavailable") from exc
    return True


def _hide_key(user: UserContext, entity_id: str) -> str:
    try:
        row = entity_browser.resolve_viewable_entity(user, entity_id)
    except Exception as exc:  # noqa: BLE001
        _log.warning("entity_edit: hide lookup failed entity_id=%s", entity_id, exc_info=True)
        raise EntityStoreUnavailable("entity lookup unavailable") from exc
    if row is None:
        raise EntityNotFound(entity_id)
    if row["user_id"] == user.user_id:
        raise EntityHideOwn(entity_id)
    return str(row.get("published_from") or row["entity_id"])


MAX_REREAD_DOCUMENTS = 50
LIST_CAP = 500


def list_name_rules(user: UserContext) -> dict:
    """The caller's blocks, kind corrections and hidden entries (each newest first, capped)."""
    user_id = user.user_id
    ms = config.get_metadata_store()
    try:
        rules = entity_name_rules.list_rules(ms, user_id, limit=LIST_CAP)
        vis, vis_params = visible_filter(user, scope_filter=None, qualifier="e")
        join = (
            "FROM entity_hidden h JOIN entities e "
            "  ON coalesce(e.published_from, e.entity_id) = h.entity_key "
            # SCOPE-EXEMPT: entity_hidden is per-member; visibility computed below.
            "WHERE h.user_id = %s AND " + vis
        )
        hidden = ms.fetch_all(
            "SELECT e.entity_id::text AS entity_id, e.name, e.entity_type, h.hidden_at "
            + join
            + " ORDER BY h.hidden_at DESC LIMIT %s",
            (user_id, *vis_params, LIST_CAP),
        )
        hidden_total = int(
            (ms.fetch_one("SELECT count(*) AS n " + join, (user_id, *vis_params)) or {}).get("n")
            or 0
        )
    except (psycopg2.Error, PoolExhaustedError) as exc:
        _log.warning("entity_edit: name rules lookup failed", exc_info=True)
        raise EntityStoreUnavailable("name rules unavailable") from exc
    return {
        "blocked": [
            {
                "name_norm": r["name_norm"],
                "display_name": r.get("display_name") or r["name_norm"],
                "entity_type": r["from_type"],
                "document_count": int(r.get("document_count") or 0),
                "created_at": r["created_at"],
            }
            for r in rules["blocks"]
        ],
        "corrections": [
            {
                "name_norm": r["name_norm"],
                "display_name": r.get("display_name") or r["name_norm"],
                "from_type": r["from_type"],
                "to_type": r["to_type"],
            }
            for r in rules["corrections"]
        ],
        "hidden": [
            {
                "entity_id": r["entity_id"],
                "name": r["name"],
                "type": r.get("entity_type"),
                "hidden_at": r["hidden_at"],
            }
            for r in hidden
        ],
        "blocked_total": rules["blocked_total"],
        "corrections_total": rules["corrections_total"],
        "hidden_total": hidden_total,
    }


def remove_name_rule(
    user: UserContext,
    *,
    name_norm: str,
    entity_type: str,
    kind: Literal["block", "retype"],
) -> dict:
    """Remove one of the caller's rules: the undo of a delete (block) or of a kind correction.

    Inputs are normalised first, so the display form or a lower-case kind from a
    client still finds the rule. Removing a block gives back every kind correction
    that a delete had converted, and offers a re-read of the documents that
    mentioned the entry (resolved to the caller's **current** documents; an
    imported path that matches none is dropped). Raises :class:`NameRuleNotFound`.
    """
    key = entity_name_rules.name_key(name_norm)
    from_type = (entity_type or "").strip().upper()
    user_id = user.user_id
    ms = config.get_metadata_store()
    restored: list[dict] = []
    evidence: list[str] = []
    try:
        with ms.transaction():
            ms.execute("SET LOCAL lock_timeout = '5s'")
            ms.fetch_one(_LOCK_SQL, (ENTITY_NAMES_ADVISORY_KEY1, user_id))
            if kind == "block":
                removed = entity_name_rules.remove_block(
                    ms, user_id=user_id, name_norm=key, entity_type=from_type
                )
                if removed is None:
                    raise NameRuleNotFound()  # no key: a key is the normalised name
                evidence = removed.document_evidence
                restored = [
                    {
                        "name_norm": key,
                        "display_name": removed.display_name,
                        "from_type": f,
                        "to_type": t,
                    }
                    for f, t in removed.restored
                ]
            else:
                if not entity_name_rules.remove_retype(
                    ms, user_id=user_id, name_norm=key, from_type=from_type
                ):
                    raise NameRuleNotFound()  # no key: a key is the normalised name
            documents: list[dict] = []
            if evidence:
                rows = ms.fetch_all(
                    "SELECT id, file_path FROM file_index "
                    # SCOPE-EXEMPT: the caller's own source documents only.
                    "WHERE user_id = %s AND published_from IS NULL AND file_path = ANY(%s)",
                    (user_id, evidence),
                )
                by_path = {r["file_path"]: r["id"] for r in rows}
                documents = [
                    {
                        "document_id": int(by_path[p]),
                        "title": p.replace("\\", "/").rsplit("/", 1)[-1] or p,
                    }
                    for p in evidence
                    if p in by_path
                ]
    except (psycopg2.Error, PoolExhaustedError) as exc:
        _log.warning("entity_edit: remove rule failed", exc_info=True)
        raise EntityStoreUnavailable("rule removal unavailable") from exc
    return {
        "removed": True,
        "restored_corrections": restored,
        "reread_documents": documents[:MAX_REREAD_DOCUMENTS],
        "reread_more": max(0, len(documents) - MAX_REREAD_DOCUMENTS),
    }


# ── merge, undo, move mentions (LUM-965) ─────────────────────────────────────


def _resolve_own(user: UserContext, entity_id: str, which: str) -> dict:
    """:func:`_resolve_own_personal_row`, tagging a refusal with ``which`` entry."""
    try:
        return _resolve_own_personal_row(user, entity_id)
    except (EntityNotFound, EntityNotOwner, EntityNotEditable) as exc:
        exc.which = which
        raise


def _lock_pair(ms, a_id: str, b_id: str, user_id: str, a_version: int, b_version: int):
    """Take the member lock, re-read both rows ``FOR UPDATE`` and check both versions.

    Returns ``(row_a, row_b)``. Rows are read in id order (a consistent order),
    though the member lock already serialises every writer of this member.
    """
    ms.execute("SET LOCAL lock_timeout = '5s'")
    ms.fetch_one(_LOCK_SQL, (ENTITY_NAMES_ADVISORY_KEY1, user_id))
    rows = {}
    for pk in sorted((a_id, b_id)):
        row = ms.fetch_one(_LOCKED_ROW_SQL, (pk, user_id))
        if row is None:
            raise EntityNotFound(pk, "this" if pk == a_id else "other")
        rows[pk] = row
    row_a, row_b = rows[a_id], rows[b_id]
    for row, version, which in ((row_a, a_version, "this"), (row_b, b_version, "other")):
        if row.get("is_staged"):
            raise EntityStaged(which)
        if int(row["version"]) != int(version):
            raise EntityVersionConflict(int(row["version"]), which)
    return row_a, row_b


def merge_entries(
    user: UserContext,
    keep_id: str,
    other_id: str,
    *,
    version: int,
    other_version: int,
    keep_name: str = "this",
) -> tuple[dict, str]:
    """Merge ``other_id`` into ``keep_id`` (both the caller's own personal entries).

    The entry whose page the member is on (``keep_id``) survives. ``keep_name``
    is ``"this"`` (default: it keeps its name, and the other name becomes one of
    its other names) or ``"other"`` (it takes the other entry's name). Returns
    ``(survivor_row, merge_id)``. One transaction, serialised per member; the
    engine writes the undo record in the same transaction (``origin='member'``).
    Both versions are checked, so an open edit dialog on either entry is not
    silently overwritten. After the commit the search points and shared copies
    follow (best-effort, never raising); the graph follows at the next reconcile.
    """
    if keep_name not in ("this", "other"):
        raise ValueError(f"invalid keep_name {keep_name!r}")
    keep = _resolve_own(user, keep_id, "this")
    other = _resolve_own(user, other_id, "other")
    keep_pk, other_pk = str(keep["entity_id"]), str(other["entity_id"])
    if keep_pk == other_pk:
        raise EntitySameEntry()
    if keep.get("is_staged"):
        raise EntityStaged("this")
    if other.get("is_staged"):
        raise EntityStaged("other")

    user_id = user.user_id
    ms = config.get_metadata_store()
    kind_changed = False
    try:
        with ms.transaction():
            row_keep, row_other = _lock_pair(ms, keep_pk, other_pk, user_id, version, other_version)
            kind_changed = entity_merge._kind(row_keep.get("entity_type")) != entity_merge._kind(
                row_other.get("entity_type")
            )
            outcome = entity_merge.merge_in_transaction(
                ms,
                winner_id=keep_pk,
                loser_id=other_pk,
                user_id=user_id,
                origin="member",
                acted_by=user_id,
                keep_name="winner" if keep_name == "this" else "loser",
            )
            if _before_commit is not None:
                _before_commit()
    except entity_merge.MergeNotPersonal as exc:
        raise EntityNotEditable(None, "other") from exc
    except (psycopg2.Error, PoolExhaustedError) as exc:
        _log.warning("entity_edit: merge transaction failed keep=%s", keep_pk, exc_info=True)
        raise EntityStoreUnavailable("merge unavailable") from exc

    entity_merge.after_merge_commit(outcome)
    if kind_changed:
        try:
            entity_constraints.run_batch_constraints([keep_pk], user_id)
        except Exception:  # noqa: BLE001 - the merge has committed; never a 500
            _log.exception("entity_edit: constraint re-check failed entity_id=%s", keep_pk)
    return outcome.winner_row, outcome.merge_id


def undo_merge(user: UserContext, merge_id: str):
    """Undo one of the caller's merges. Returns ``(restored, survivor, mentions_since, skipped)``.

    Newest first per survivor: an older merge is refused with
    :class:`MergeNotNewest` naming the newer one. ``mentions_since`` lists the
    survivor's mentions added after the merge (not from it), so the member can
    move any that belong to the restored entry.
    """
    try:
        pk = str(uuid.UUID(str(merge_id)))
    except (ValueError, AttributeError) as exc:
        raise MergeNotFound(str(merge_id)) from exc
    user_id = user.user_id
    ms = config.get_metadata_store()
    try:
        with ms.transaction():
            outcome = entity_merge.undo_in_transaction(
                ms, merge_id=pk, user_id=user_id, acted_by=user_id
            )
            if _before_commit is not None:
                _before_commit()
    except (psycopg2.Error, PoolExhaustedError) as exc:
        _log.warning("entity_edit: undo transaction failed merge_id=%s", pk, exc_info=True)
        raise EntityStoreUnavailable("undo unavailable") from exc

    entity_merge.after_undo_commit(outcome)
    try:
        since = entity_browser.list_mentions_since(
            user,
            outcome.winner_id,
            after=outcome.merged_at,
            exclude_relation_ids=outcome.moved_relation_ids,
        )
    except Exception:  # noqa: BLE001 - the undo has committed; the list is a convenience
        _log.warning("entity_edit: mentions-since lookup failed merge_id=%s", pk, exc_info=True)
        since = []
    return outcome.restored_row, outcome.survivor_row, since, outcome.skipped_rows


_HISTORY_SQL = (
    "SELECT m.merge_id::text AS merge_id, m.winner_id::text AS winner_id, "
    "       m.loser_id::text AS loser_id, m.origin, m.merged_at, m.undone_at, "
    "       m.loser_row->>'name' AS loser_name, w.name AS winner_name, "
    "       (cardinality(m.projections_moved) > 0 "
    "         OR jsonb_array_length(m.projections_dropped) > 0) AS restores_sharing "
    # SCOPE-EXEMPT: entity_merges is per-member (no scope column); user_id-bound.
    "FROM entity_merges m "
    "LEFT JOIN entities w ON w.entity_id = m.winner_id AND w.user_id = m.user_id "
    "WHERE m.user_id = %s "
    "  AND (m.winner_id = %s::uuid OR (m.loser_id = %s::uuid AND m.undone_at IS NOT NULL)) "
    "ORDER BY m.merged_at DESC, m.merge_id DESC LIMIT 50"
)


def list_merge_history(user: UserContext, entity_id: str) -> list[dict]:
    """Merges into (and, once undone, out of) one of the caller's entries, newest first.

    Only the newest open ``merged_in`` row can be undone (``can_undo``); every
    older open one carries ``newer_merge_id`` (the one to undo first). Because
    the undoable row sorts first, the 50-row cap never hides it.
    """
    row = _resolve_own(user, entity_id, "this")
    pk = str(row["entity_id"])
    ms = config.get_metadata_store()
    try:
        rows = ms.fetch_all(_HISTORY_SQL, (user.user_id, pk, pk))
    except (psycopg2.Error, PoolExhaustedError) as exc:
        _log.warning("entity_edit: history lookup failed entity_id=%s", pk, exc_info=True)
        raise EntityStoreUnavailable("history unavailable") from exc

    items: list[dict] = []
    newest_open: Optional[str] = None
    names = {r["merge_id"]: r["loser_name"] or "" for r in rows}
    for r in rows:
        merged_in = r["winner_id"] == pk
        open_in = merged_in and r["undone_at"] is None
        can_undo = False
        newer = None
        if open_in:
            if newest_open is None:
                newest_open = r["merge_id"]
                can_undo = True
            else:
                newer = newest_open
        items.append(
            {
                "merge_id": r["merge_id"],
                "direction": "merged_in" if merged_in else "merged_out",
                "other_entity_id": r["loser_id"] if merged_in else r["winner_id"],
                "other_name": (r["loser_name"] if merged_in else r["winner_name"]) or "",
                "origin": r["origin"],
                "merged_at": r["merged_at"],
                "undone_at": r["undone_at"],
                "can_undo": can_undo,
                "newer_merge_id": newer,
                "newer_other_name": names.get(newer, "") if newer else None,
                "restores_sharing": bool(r["restores_sharing"]),
            }
        )
    return items


def move_mentions(
    user: UserContext,
    from_id: str,
    to_id: str,
    *,
    relation_ids: list[int],
    version: int,
    to_version: int,
) -> tuple[dict, dict, int]:
    """Move mentions (``entity_relations`` rows, by id) between two of the caller's entries.

    Returns ``(source_row, target_row, moved)``. A mention the target already
    has (same evidence and relation type) is dropped from the source rather than
    duplicated. Any id that is not a row of the source for this member refuses
    the whole move with :class:`MentionsNotFound`, writing nothing.
    """
    src = _resolve_own(user, from_id, "this")
    dst = _resolve_own(user, to_id, "other")
    src_pk, dst_pk = str(src["entity_id"]), str(dst["entity_id"])
    if src_pk == dst_pk:
        raise EntitySameEntry()
    user_id = user.user_id
    ms = config.get_metadata_store()
    try:
        with ms.transaction():
            _lock_pair(ms, src_pk, dst_pk, user_id, version, to_version)
            moved = entity_merge.move_mentions_in_transaction(
                ms,
                from_id=src_pk,
                to_id=dst_pk,
                relation_ids=relation_ids,
                user_id=user_id,
            )
            src_row = ms.fetch_one(_ROW_AFTER_SQL, (src_pk, user_id))
            dst_row = ms.fetch_one(_ROW_AFTER_SQL, (dst_pk, user_id))
            if _before_commit is not None:
                _before_commit()
    except (psycopg2.Error, PoolExhaustedError) as exc:
        _log.warning("entity_edit: move transaction failed from=%s", src_pk, exc_info=True)
        raise EntityStoreUnavailable("move unavailable") from exc
    for row in (src_row, dst_row):
        _refresh_shared_projections(row, user)
    return src_row, dst_row, moved


_ROW_AFTER_SQL = (
    "SELECT * FROM entities "
    # SCOPE-EXEMPT: caller's own personal source row.
    "WHERE entity_id = %s AND user_id = %s"
)
