# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Per-member name rules: what a member's corrections teach extraction (LUM-967, LUM-966).

Two actions live in the one ``entity_name_rules`` table, and this is the **only**
module that reads or writes it:

* ``retype`` (LUM-967): the member changed what kind an entry is ("Meier
  Pharmacy" PERSON → ORG). :func:`record_retype` stores a rule keyed by the
  entry's canonical name and the kind extraction read it as;
  :func:`apply_name_rules` rewrites the kind of every later extraction.
* ``block`` (LUM-966): the member deleted an entry. :func:`record_block` stores a
  rule for each of its names at its kind; :func:`apply_name_rules` drops a
  matching extracted entity before it is stored, so a re-read cannot bring the
  entry back. :func:`remove_block` is the undo.

Invariants, for one ``(user_id, name_norm)`` — they let one lookup decide:

1. no rule's ``to_type`` is another rule's ``from_type`` (no chains);
2. no retype's ``to_type`` is a block's ``from_type`` (nothing is rewritten onto
   a blocked kind). When a delete would break this, each retype ``F → K`` is
   *converted* in place to a block on ``F`` with ``restore_to_type = K``, so the
   member's correction comes back if the block on ``K`` is removed;
3. every converted block ``(key, F, restore_to_type = R)`` has a main block
   ``(key, R, restore_to_type IS NULL)``.
"""

from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass
from dataclasses import field
from typing import Any

from models.entities import ExtractedEntity

import config

_log = logging.getLogger(__name__)

ALLOWED_ACTIONS = ("retype", "block")

#: A rule's evidence list is capped (the migration's CHECK enforces the same).
EVIDENCE_CAP = 500


class NameRulesUnavailable(Exception):
    """The rules could not be read: extraction must not store (fails closed).

    Reading no rules would re-learn every name the member deleted.
    """


@dataclass
class RemovedBlock:
    display_name: str
    document_evidence: list[str] = field(default_factory=list)
    restored: list[tuple[str, str]] = field(default_factory=list)


def name_key(name: str) -> str:
    """Normalise a name into a rule key. Pure; never raises.

    NFC first (so a name emitted decomposed, "Müller", keys the same as the
    stored composed one), then collapse whitespace and ``str.lower()`` (not
    ``casefold``) — the same collapse and lower-casing as
    ``entity_edit.normalise_name`` / ``normalise_names``.

    **Stable contract:** every stored rule depends on this function. Changing it
    orphans existing rules; golden-value tests pin it.
    """
    return " ".join(unicodedata.normalize("NFC", name).split()).lower()


def kind_key(entity_type: Any) -> str:
    """The kind a rule is keyed on: stripped, upper-cased, blank → ``CONCEPT``."""
    return (entity_type or "").strip().upper() or "CONCEPT"


def record_retype(
    ms: Any,
    *,
    user_id: str,
    name: str,
    from_type: str,
    to_type: str,
    source_entity_id: str,
) -> None:
    """Record "``name`` read as ``from_type`` is really ``to_type``".

    NOTE (LUM-966 closure): a kind correction records the entry's canonical name
    only; its other names (aliases) get no retype, so a re-read that meets an alias
    can still bring back the old kind. Add a rule per alias here if members report
    that. Blocks already cover aliases (``record_block``).

    Runs **inside the caller's transaction** (no ``ms.transaction()`` of its
    own) so the rule and the kind change commit or roll back together. Raises
    whatever ``ms`` raises.

    Five statements, in this order, with ``X = from_type`` and ``Y = to_type``
    (upper-cased):

    1a. converted blocks waiting for Y become the retype they were;
    1b. delete any rule that *starts* from Y (no rule may start from the member's
        latest choice) — **this lifts a block on ``(name, Y)``**: a correction to
        Y is an explicit statement that the name is learnable at Y;
    2a. redirect every retype that *points at* X to Y;
    2b. converted blocks waiting for X become retypes to Y (step 3 replaces the
        block on X they depended on);
    3.  upsert ``X → Y`` (it may replace a block at ``(name, X)``: the member's
        latest assertion wins).

    After a call, no rule starts from Y and none points at X, so the new row
    neither extends nor is extended by another; untouched rows had no chains
    before. It is **not** true that every rule points at Y: two entries with the
    same name and different stored kinds carry independent rules, each mapping
    a kind the model reads to the member's last choice *from that kind*.
    """
    key = name_key(name)
    x = from_type.upper()
    y = to_type.upper()
    ms.execute(
        "UPDATE entity_name_rules SET action = 'retype', to_type = %s, "
        "restore_to_type = NULL, updated_at = NOW(), document_evidence = '{}' "
        # SCOPE-EXEMPT: entity_name_rules is per-member (no scope column); user_id-only.
        "WHERE user_id = %s AND name_norm = %s AND action = 'block' AND restore_to_type = %s",
        (y, user_id, key, y),
    )
    ms.execute(
        # SCOPE-EXEMPT: entity_name_rules is per-member (no scope column); user_id-only.
        "DELETE FROM entity_name_rules WHERE user_id = %s AND name_norm = %s AND from_type = %s",
        (user_id, key, y),
    )
    ms.execute(
        "UPDATE entity_name_rules SET to_type = %s, source_entity_id = %s, updated_at = NOW() "
        # SCOPE-EXEMPT: entity_name_rules is per-member (no scope column); user_id-only.
        "WHERE user_id = %s AND name_norm = %s AND action = 'retype' AND to_type = %s",
        (y, source_entity_id, user_id, key, x),
    )
    ms.execute(
        "UPDATE entity_name_rules SET action = 'retype', to_type = %s, "
        "restore_to_type = NULL, updated_at = NOW(), document_evidence = '{}' "
        # SCOPE-EXEMPT: entity_name_rules is per-member (no scope column); user_id-only.
        "WHERE user_id = %s AND name_norm = %s AND action = 'block' AND restore_to_type = %s",
        (y, user_id, key, x),
    )
    ms.execute(
        "INSERT INTO entity_name_rules "
        "(user_id, name_norm, from_type, action, to_type, source_entity_id, display_name) "
        "VALUES (%s, %s, %s, 'retype', %s, %s, %s) "
        "ON CONFLICT (user_id, name_norm, from_type) DO UPDATE SET "
        "action = 'retype', to_type = EXCLUDED.to_type, restore_to_type = NULL, "
        "document_evidence = '{}', "
        "source_entity_id = EXCLUDED.source_entity_id, "
        "display_name = EXCLUDED.display_name, updated_at = NOW()",
        (user_id, key, x, y, source_entity_id, " ".join(name.split())),
    )


def record_block(
    ms: Any,
    *,
    user_id: str,
    keys_and_names: list[tuple[str, str]],
    entity_type: str,
    source_entity_id: str,
    document_evidence: list[str],
) -> list[str]:
    """Block each ``(key, display name)`` at ``entity_type`` (inside the caller's transaction).

    The caller has already skipped keys that another live entry of this kind
    uses, and keys with a retype *starting* at this kind (that retype already
    sends the name elsewhere; a block would overwrite it). For each key:

    * upsert ``block (key, K)``; if the row there is a converted block
      (``restore_to_type`` set — left by an earlier delete at another kind), it
      becomes a main block, so the settings list shows it and removing the other
      kind's block no longer unblocks this one; evidence is the union, capped;
    * convert every ``retype (key, F → K)`` in place to ``block (key, F)`` with
      ``restore_to_type = K`` (invariant 2).

    A block drops the extracted mention; it does not reroute it. Extraction
    matches existing entries by name regardless of kind, so after a delete a
    same-name entry of another kind no longer receives readings at the blocked
    kind (residual 8 — the guide says to merge instead of deleting in that case).

    Logs nothing: a rule key is the normalised name. Returns the keys blocked.
    """
    kind = kind_key(entity_type)
    evidence = list(dict.fromkeys(document_evidence))[:EVIDENCE_CAP]
    blocked: list[str] = []
    for key, display in keys_and_names:
        if not key:
            continue
        ms.execute(
            "INSERT INTO entity_name_rules "
            "(user_id, name_norm, from_type, action, to_type, source_entity_id, "
            "display_name, document_evidence) "
            "VALUES (%s, %s, %s, 'block', NULL, %s, %s, %s) "
            "ON CONFLICT (user_id, name_norm, from_type) DO UPDATE SET "
            "action = 'block', to_type = NULL, restore_to_type = NULL, "
            "display_name = EXCLUDED.display_name, "
            "source_entity_id = EXCLUDED.source_entity_id, "
            "document_evidence = ARRAY(SELECT x FROM unnest("
            "EXCLUDED.document_evidence || entity_name_rules.document_evidence) "
            "WITH ORDINALITY AS t(x, n) GROUP BY x ORDER BY min(n) LIMIT 500), "
            "updated_at = NOW()",
            (user_id, key, kind, source_entity_id, display, evidence),
        )
        ms.execute(
            "UPDATE entity_name_rules SET action = 'block', to_type = NULL, "
            "restore_to_type = %s, display_name = %s, document_evidence = %s, "
            "source_entity_id = %s, updated_at = NOW() "
            # SCOPE-EXEMPT: entity_name_rules is per-member (no scope column); user_id-only.
            "WHERE user_id = %s AND name_norm = %s AND action = 'retype' AND to_type = %s",
            (kind, display, evidence, source_entity_id, user_id, key, kind),
        )
        blocked.append(key)
    return blocked


def remove_block(ms: Any, *, user_id: str, name_norm: str, entity_type: str) -> RemovedBlock | None:
    """Remove the main block on ``(name_norm, entity_type)``; the member's undo.

    Inside the caller's transaction. ``None`` when there is no main block (a
    converted block is not addressable). Every converted block that waited for
    this kind becomes the retype it was.
    """
    kind = kind_key(entity_type)
    main = ms.fetch_one(
        "SELECT display_name, document_evidence FROM entity_name_rules "
        # SCOPE-EXEMPT: entity_name_rules is per-member (no scope column); user_id-only.
        "WHERE user_id = %s AND name_norm = %s AND from_type = %s AND action = 'block' "
        "AND restore_to_type IS NULL FOR UPDATE",
        (user_id, name_norm, kind),
    )
    if not main:
        return None
    converted = ms.fetch_all(
        "SELECT from_type, document_evidence FROM entity_name_rules "
        # SCOPE-EXEMPT: entity_name_rules is per-member (no scope column); user_id-only.
        "WHERE user_id = %s AND name_norm = %s AND action = 'block' AND restore_to_type = %s "
        "ORDER BY from_type FOR UPDATE",
        (user_id, name_norm, kind),
    )
    ms.execute(
        # SCOPE-EXEMPT: entity_name_rules is per-member (no scope column); user_id-only.
        "DELETE FROM entity_name_rules WHERE user_id = %s AND name_norm = %s AND from_type = %s",
        (user_id, name_norm, kind),
    )
    if converted:
        ms.execute(
            "UPDATE entity_name_rules SET action = 'retype', to_type = %s, "
            "restore_to_type = NULL, document_evidence = '{}', updated_at = NOW() "
            # SCOPE-EXEMPT: entity_name_rules is per-member (no scope column); user_id-only.
            "WHERE user_id = %s AND name_norm = %s AND action = 'block' AND restore_to_type = %s",
            (kind, user_id, name_norm, kind),
        )
    evidence: list[str] = list(main.get("document_evidence") or [])
    for row in converted:
        evidence.extend(row.get("document_evidence") or [])
    return RemovedBlock(
        display_name=main.get("display_name") or name_norm,
        document_evidence=list(dict.fromkeys(evidence))[:EVIDENCE_CAP],
        restored=[(str(r["from_type"]), kind) for r in converted],
    )


def remove_retype(ms: Any, *, user_id: str, name_norm: str, from_type: str) -> bool:
    """Delete one kind correction (never a block, converted or not)."""
    row = ms.fetch_one(
        "WITH d AS (DELETE FROM entity_name_rules "
        # SCOPE-EXEMPT: entity_name_rules is per-member (no scope column); user_id-only.
        "WHERE user_id = %s AND name_norm = %s AND from_type = %s AND action = 'retype' "
        "RETURNING 1) SELECT count(*) AS n FROM d",
        (user_id, name_norm, from_type),
    )
    return int((row or {}).get("n") or 0) > 0


def lift_blocks(ms: Any, *, user_id: str, keys: list[str], entity_type: str) -> list[str]:
    """Remove the block on each key at ``entity_type``; returns the keys lifted.

    Called when the member **explicitly** gives a live entry of that kind a name
    (rename, added alias, kind change). Reusing :func:`remove_block` keeps all
    three invariants with no new statement.
    """
    lifted: list[str] = []
    for key in dict.fromkeys(k for k in keys if k):
        if remove_block(ms, user_id=user_id, name_norm=key, entity_type=entity_type) is not None:
            lifted.append(key)
    return lifted


def load_rules(
    ms: Any, user_id: str, keys: list[str]
) -> dict[tuple[str, str], tuple[str, str | None]]:
    """One query: ``{(name_norm, from_type): (action, to_type)}`` for both actions."""
    if not keys:
        return {}
    rows = ms.fetch_all(
        "SELECT name_norm, from_type, action, to_type FROM entity_name_rules "
        # SCOPE-EXEMPT: entity_name_rules is per-member (no scope column); user_id-only.
        "WHERE user_id = %s AND name_norm = ANY(%s)",
        (user_id, keys),
    )
    return {(r["name_norm"], r["from_type"]): (r["action"], r.get("to_type")) for r in rows}


def apply_name_rules(entities: list[ExtractedEntity], user_id: str) -> list[ExtractedEntity]:
    """Apply the member's rules to extracted entities: drop blocked, retype the rest.

    Returns the **same list** (kinds rewritten in place) when nothing is
    dropped, else a new list without the blocked entities. A failed lookup
    (store outage, missing table) is retried once and then raises
    :class:`NameRulesUnavailable`: carrying on would re-learn every deleted name.
    Logs a count of drops only — a rule key is the normalised name.
    """
    keys = list(dict.fromkeys(k for k in (name_key(e.name) for e in entities) if k))
    if not keys:
        return entities
    rules: dict[tuple[str, str], tuple[str, str | None]] | None = None
    for attempt in (1, 2):
        try:
            ms = config.get_metadata_store()
            rules = load_rules(ms, user_id, keys)
            break
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "entity_name_rules: lookup failed user_id=%s attempt=%d error=%s",
                user_id,
                attempt,
                type(exc).__name__,
            )
            if attempt == 2:
                raise NameRulesUnavailable("entity name rules could not be read") from exc
    if not rules:
        return entities
    kept: list[ExtractedEntity] = []
    dropped = 0
    for entity in entities:
        rule = rules.get((name_key(entity.name), kind_key(entity.entity_type)))
        if rule is not None:
            action, to_type = rule
            if action == "block":
                dropped += 1
                continue
            if to_type is not None:
                entity.entity_type = to_type
        kept.append(entity)
    if dropped:
        _log.debug("entity_name_rules: dropped %d blocked entities user_id=%s", dropped, user_id)
        return kept
    return entities


def list_rules(ms: Any, user_id: str, *, limit: int = 500) -> dict[str, Any]:
    """The member's rules for the settings page: blocks (main rows only) and corrections.

    Each list is newest first, capped at ``limit``, with its uncapped ``*_total``.
    A converted block is never listed: it is not addressable (invariant 3).
    """
    blocks = ms.fetch_all(
        "SELECT r.name_norm, r.from_type, r.display_name, r.created_at, "
        "(SELECT count(DISTINCT x) FROM (SELECT unnest(r.document_evidence) AS x "
        "   UNION ALL SELECT unnest(c.document_evidence) FROM entity_name_rules c "
        "   WHERE c.user_id = r.user_id AND c.name_norm = r.name_norm "
        "     AND c.action = 'block' AND c.restore_to_type = r.from_type) u) "
        "AS document_count "
        # SCOPE-EXEMPT: entity_name_rules is per-member (no scope column); user_id-only.
        "FROM entity_name_rules r WHERE r.user_id = %s AND r.action = 'block' "
        "AND r.restore_to_type IS NULL ORDER BY r.updated_at DESC, r.name_norm LIMIT %s",
        (user_id, limit),
    )
    corrections = ms.fetch_all(
        "SELECT name_norm, from_type, to_type, display_name FROM entity_name_rules "
        # SCOPE-EXEMPT: entity_name_rules is per-member (no scope column); user_id-only.
        "WHERE user_id = %s AND action = 'retype' ORDER BY updated_at DESC, name_norm LIMIT %s",
        (user_id, limit),
    )
    totals = (
        ms.fetch_one(
            "SELECT "
            "count(*) FILTER (WHERE action = 'block' AND restore_to_type IS NULL) AS blocked, "
            "count(*) FILTER (WHERE action = 'retype') AS corrections "
            # SCOPE-EXEMPT: entity_name_rules is per-member (no scope column); user_id-only.
            "FROM entity_name_rules WHERE user_id = %s",
            (user_id,),
        )
        or {}
    )
    return {
        "blocks": [dict(r) for r in blocks],
        "corrections": [dict(r) for r in corrections],
        "blocked_total": int(totals.get("blocked") or 0),
        "corrections_total": int(totals.get("corrections") or 0),
    }
