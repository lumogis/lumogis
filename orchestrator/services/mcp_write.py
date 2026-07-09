# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""MCP memory write surface orchestrator (LUM-291).

Composes the existing entity write path with the new `memories` / `entity_edges`
writers into the three MVP write tools: ``add_memory``, ``add_entity``,
``add_relation``. Bank-aware for memories and edges; entities are user-scoped
(shared across banks) by design.

Quality-gate policy is asymmetric and deliberate:

* ``add_memory`` entities are LLM-extracted → go through the quality gate
  (``skip_quality_gate=False``) so hallucinations are filtered.
* ``add_entity`` / ``add_relation`` endpoints are explicit, user-asserted →
  ``skip_quality_gate=True`` so they are never silently discarded.
"""

from __future__ import annotations

import json
import logging
import os
import uuid

import config
from models.entities import ExtractedEntity
from models.mcp_write import RELATION_TYPES
from models.mcp_write import ExtractedRelation
from models.mcp_write import _MAX_METADATA_BYTES
from models.mcp_write import _MAX_NAME
from services import entity_edges
from services import memories
from services.banks import MCP_MEMORY_SOURCE
from services.entities import extract_entities
from services.entities import store_entities

_log = logging.getLogger(__name__)

_EVIDENCE_TYPE = "MEMORY"

_EXTRACT_RELATIONS_PROMPT = (
    "Extract directed relations between the given entities from the text. "
    "Respond ONLY with a JSON array of objects "
    '{"src_name": str, "dst_name": str, "relation_type": str}. '
    "relation_type MUST be one of: " + ", ".join(sorted(RELATION_TYPES)) + ". "
    "Use only the provided entity names verbatim. Entities: "
)


def default_bank() -> str:
    return os.environ.get("LUMOGIS_MCP_DEFAULT_BANK", "coding").strip() or "coding"


def _mcp_memory_metadata(metadata: dict | None) -> dict:
    merged = dict(metadata or {})
    merged["source"] = MCP_MEMORY_SOURCE
    return merged


def add_memory(
    *,
    user_id: str,
    bank: str,
    content: str,
    tags: list[str] | None = None,
    metadata: dict | None = None,
) -> dict:
    """Store a memory, extract + persist entities (gated) and relations."""
    memory_id = memories.store_memory(
        user_id=user_id,
        bank=bank,
        content=content,
        tags=tags,
        metadata=_mcp_memory_metadata(metadata),
    )

    # Entity half — LLM-extracted, so the quality gate stays ON.
    extracted = extract_entities(content, user_id=user_id)
    entity_ids = store_entities(
        extracted, evidence_id=memory_id, evidence_type=_EVIDENCE_TYPE, user_id=user_id
    )

    # Relation half — NEW lightweight prompt; fail-soft.
    relation_ids: list[str] = []
    relations = extract_relations(content, extracted, user_id=user_id)
    for rel in relations:
        try:
            src_id = _resolve_or_create_entity(rel.src_name, user_id=user_id, evidence_id=memory_id)
            dst_id = _resolve_or_create_entity(rel.dst_name, user_id=user_id, evidence_id=memory_id)
            relation_ids.append(
                entity_edges.store_edge(
                    user_id=user_id,
                    bank=bank,
                    src_entity_id=src_id,
                    dst_entity_id=dst_id,
                    relation_type=rel.relation_type,
                    evidence_id=memory_id,
                )
            )
        except Exception as exc:  # noqa: BLE001 — best-effort enrichment of a stored memory.
            # Log only the relation_type (not src/dst names, which are user content).
            _log.warning("add_memory: skipping a %s relation: %s", rel.relation_type, exc)

    return {"memory_id": memory_id, "entity_ids": entity_ids, "relation_ids": relation_ids}


def add_entity(
    *,
    user_id: str,
    bank: str,
    name: str,
    entity_type: str,
    aliases: list[str] | None = None,
    context_tags: list[str] | None = None,
) -> dict:
    """Write a single explicit entity (quality gate bypassed)."""
    entity = ExtractedEntity(
        name=name,
        entity_type=entity_type,
        aliases=aliases or [],
        context_tags=context_tags or [],
    )
    ids = store_entities(
        [entity],
        evidence_id="mcp:add_entity:" + uuid.uuid4().hex,
        evidence_type=_EVIDENCE_TYPE,
        user_id=user_id,
        skip_quality_gate=True,
    )
    if not ids:
        raise RuntimeError("add_entity: entity write produced no id")
    return {"entity_id": ids[0]}


def add_relation(
    *,
    user_id: str,
    bank: str,
    src: str,
    dst: str,
    relation_type: str,
) -> dict:
    """Create a typed edge, resolving/creating both endpoints first."""
    evidence = "mcp:add_relation:" + uuid.uuid4().hex
    src_id = _resolve_or_create_entity(src, user_id=user_id, evidence_id=evidence)
    dst_id = _resolve_or_create_entity(dst, user_id=user_id, evidence_id=evidence)
    relation_id = entity_edges.store_edge(
        user_id=user_id,
        bank=bank,
        src_entity_id=src_id,
        dst_entity_id=dst_id,
        relation_type=relation_type,
        evidence_id=evidence,
    )
    return {"relation_id": relation_id}


def extract_relations(
    text: str, entities: list[ExtractedEntity], *, user_id: str | None = None
) -> list[ExtractedRelation]:
    """LLM-extract typed relations among ``entities``. Fail-soft → ``[]``."""
    if not text or not text.strip() or len(entities) < 2:
        return []
    try:
        from services.privacy_mode import resolve_job_model

        job_model = resolve_job_model("llama", user_id)
        if not job_model:
            return []
        provider = config.get_llm_provider(job_model, user_id=user_id)
        names = ", ".join(e.name for e in entities)
        response = provider.chat(
            messages=[{"role": "user", "content": _EXTRACT_RELATIONS_PROMPT + names + "\n\n" + text}],
            system="You are a precise relation extractor. Respond only with a valid JSON array.",
            max_tokens=512,
        )
        raw = response.text.strip()
        if raw.startswith("```"):
            raw = "\n".join(line for line in raw.splitlines() if not line.startswith("```"))
        data = json.loads(raw)
        if not isinstance(data, list):
            return []
        out: list[ExtractedRelation] = []
        for item in data:
            try:
                out.append(ExtractedRelation(**item))  # validates relation_type allowlist
            except Exception as exc:  # noqa: BLE001 — drop off-allowlist / malformed items
                _log.debug("extract_relations: dropping %r: %s", item, exc)
        return out
    except Exception as exc:  # noqa: BLE001
        _log.warning("extract_relations failed (degrading to []): %s", exc)
        return []


def _resolve_or_create_entity(name: str, *, user_id: str, evidence_id: str) -> str:
    """Return the user's entity id for ``name``, creating it (bypassing the
    quality gate) when absent so an edge endpoint is always real."""
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "SELECT entity_id FROM entities "
        # SCOPE-EXEMPT: write-side personal-scope entity lookup (mirrors
        # entities._upsert_entity); endpoints resolve within the caller's user.
        "WHERE user_id = %s AND scope = 'personal' AND published_from IS NULL "
        "  AND lower(name) = lower(%s) LIMIT 1",
        (user_id, name),
    )
    if row and row.get("entity_id"):
        return str(row["entity_id"])
    ids = store_entities(
        [ExtractedEntity(name=name, entity_type="CONCEPT")],
        evidence_id=evidence_id,
        evidence_type=_EVIDENCE_TYPE,
        user_id=user_id,
        skip_quality_gate=True,
    )
    if not ids:
        raise RuntimeError(f"could not resolve or create entity {name!r}")
    return ids[0]


def forget(*, user_id: str, memory_id: str) -> dict:
    """Soft-archive a memory + its typed edges (LUM-526). Reversible; no delete."""
    if memories.get_memory(memory_id, user_id=user_id) is None:
        raise ValueError("memory not found")
    active_edges = entity_edges.fetch_active_edges_for_memory(memory_id, user_id=user_id)
    memories.archive_memory(memory_id, user_id=user_id)
    entity_edges.archive_edges_for_memory(memory_id, user_id=user_id)
    entity_edges.purge_graph_projections_for_edges(active_edges, user_id=user_id)
    # The memory IS archived after this call (newly or already) — report True;
    # archive_memory returning False just means it was already archived.
    return {"memory_id": memory_id, "archived": True}


def update_observation(
    *,
    user_id: str,
    memory_id: str,
    content: str,
    tags: list[str] | None = None,
    metadata: dict | None = None,
) -> dict:
    """Supersede a memory: add the new observation, then archive the old (LUM-526).

    Add-before-archive is deliberate (fail-safe): the two writes are not in one
    transaction, so a partial failure leaves both active (recoverable) rather
    than the old archived with no replacement. The new memory carries
    ``metadata.supersedes = <old id>`` so the link survives a partial failure.
    If the memory archive succeeds but the edge archive raises, the old memory
    is archived while its edges stay active (harmless — recall filters by the
    memory's validity; re-``forget`` reconciles); this is the same best-effort
    posture as ``forget``.
    """
    old = memories.get_memory(memory_id, user_id=user_id)
    if old is None:
        raise ValueError("memory not found")
    if old.valid_until is not None:
        raise ValueError(
            "cannot supersede an already-superseded memory; update the current one"
        )
    new_metadata = dict(metadata or {})
    new_metadata["supersedes"] = memory_id
    # The input model bounds caller metadata at _MAX_METADATA_BYTES *before* the
    # supersedes pointer is added; re-check so the injected pointer can't push a
    # near-limit payload over the cap (Postgres jsonb would store it silently).
    if len(json.dumps(new_metadata)) > _MAX_METADATA_BYTES:
        raise ValueError(
            f"metadata + supersedes pointer exceeds {_MAX_METADATA_BYTES}-byte limit"
        )
    new = add_memory(
        user_id=user_id, bank=old.bank, content=content, tags=tags, metadata=new_metadata
    )
    active_edges = entity_edges.fetch_active_edges_for_memory(memory_id, user_id=user_id)
    memories.archive_memory(memory_id, user_id=user_id)
    entity_edges.archive_edges_for_memory(memory_id, user_id=user_id)
    entity_edges.purge_graph_projections_for_edges(active_edges, user_id=user_id)
    return {
        "old_memory_id": memory_id,
        "new_memory_id": new["memory_id"],
        "entity_ids": new["entity_ids"],
        "relation_ids": new["relation_ids"],
    }


def _session_label(summary: str, memory_id: str) -> str:
    """Unique, readable name for a checkpoint's SESSION entity (LUM-294).

    ``_upsert_entity`` merges entities by ``lower(name)`` with no entity_type
    constraint, so the name MUST be unique per checkpoint — otherwise two
    checkpoints with similar summaries silently collapse into one Session. We
    take the first line / first ~80 chars of the summary and append the
    ``memory_id`` prefix (a uuid4 hex → collision-free); an empty summary falls
    back to ``"Session [<id8>]"``. Bounded to ``_MAX_NAME``.
    """
    suffix = f" [{memory_id[:8]}]"
    stripped = (summary or "").strip()
    head = stripped.splitlines()[0].strip()[:80].strip() if stripped else ""
    base = head or "Session"
    return (base + suffix)[:_MAX_NAME]


def checkpoint(*, user_id: str, bank: str, summary: str) -> dict:
    """Write a session-boundary marker (LUM-526) + auto-create a SESSION entity (LUM-294).

    The marker is identified by the well-known ``metadata.kind == "checkpoint"``
    key (ADR 129), NOT by a ``tags`` entry; recall/UX special-case on metadata.
    A ``SESSION`` entity (LUM-294) anchored to the checkpoint memory is created
    best-effort: a failure logs a WARNING and is swallowed so the checkpoint
    memory is never lost (degrade-don't-fail, mirrors ``add_memory``).
    """
    memory_id = memories.store_memory(
        user_id=user_id,
        bank=bank,
        content=summary,
        metadata=_mcp_memory_metadata({"kind": "checkpoint"}),
    )
    result: dict = {"memory_id": memory_id}
    try:
        session = ExtractedEntity(
            name=_session_label(summary, memory_id),
            entity_type="SESSION",
            aliases=[],
            context_tags=[],
        )
        ids = store_entities(
            [session],
            evidence_id=memory_id,
            evidence_type=_EVIDENCE_TYPE,
            user_id=user_id,
            skip_quality_gate=True,
        )
        if ids:
            result["entity_id"] = ids[0]
    except Exception as exc:  # noqa: BLE001 — never lose the checkpoint memory.
        _log.warning("checkpoint: SESSION entity write failed (memory kept): %s", exc)
    return result
