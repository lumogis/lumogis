# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Fixture-backed composite metadata store for LUM-299 cursor integration."""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime
from datetime import timezone

from tests.cursor_integration.fixture_loader import CodingBankFixture
from tests.test_mcp_tokens_routes import _RoutesFakeStore

_AS_OF_DEFAULT = datetime(2026, 6, 25, tzinfo=timezone.utc)


def _norm(query: str) -> str:
    return " ".join(query.split()).lower()


class _CursorIntegrationFakeStore(_RoutesFakeStore):
    """Composite store: mcp_tokens + fixture-backed reads + mutable writes."""

    def __init__(self, fixture: CodingBankFixture, *, user_id: str) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._user_id = user_id
        self._fixture = fixture
        self.calls: list[tuple[str, tuple | None]] = []
        self._memories: dict[str, dict] = {}
        self._entities: dict[str, dict] = {}
        self._edges: list[dict] = []
        self._written_memories: dict[str, dict] = {}
        self._written_entities: dict[str, dict] = {}
        self._written_edges: list[dict] = []
        self._seed_from_fixture()

    def _seed_from_fixture(self) -> None:
        for bank_name, bank in self._fixture.raw["banks"].items():
            for mem in bank["memories"]:
                mid = mem["memory_id"]
                self._memories[mid] = {
                    "id": mid,
                    "user_id": self._user_id,
                    "bank": bank_name,
                    "content": mem["content"],
                    "tags": mem.get("tags") or [],
                    "metadata": {},
                    "valid_from": _AS_OF_DEFAULT,
                    "valid_until": None,
                    "created_at": _AS_OF_DEFAULT,
                }
            for ent in bank.get("entities") or []:
                eid = ent["entity_id"]
                self._entities[eid] = {
                    "entity_id": eid,
                    "name": ent["name"],
                    "entity_type": ent["entity_type"],
                    "mention_count": ent.get("mention_count", 1),
                    "aliases": ent.get("aliases") or [],
                    "context_tags": ent.get("context_tags") or [],
                    "scope": ent.get("scope", "personal"),
                    "user_id": self._user_id,
                    "is_staged": False,
                    "published_from": None,
                }
            for edge in bank.get("edges") or []:
                self._edges.append(
                    {
                        **edge,
                        "user_id": self._user_id,
                        "bank": bank_name,
                        "valid_until": None,
                    }
                )

    def _all_memories(self) -> dict[str, dict]:
        return {**self._memories, **self._written_memories}

    def _all_entities_by_name(self) -> dict[str, dict]:
        by_name: dict[str, dict] = {}
        for ent in {**self._entities, **self._written_entities}.values():
            by_name[ent["name"].lower()] = ent
        return by_name

    def _record(self, method: str, query: str, params: tuple | None) -> None:
        with self._lock:
            self.calls.append((f"{method}:{_norm(query)}", params))

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        pass

    def execute(self, query: str, params: tuple | None = None) -> None:
        self._record("execute", query, params)
        q = _norm(query)
        p = params or ()

        if q.startswith("insert into memories"):
            memory_id, user_id, bank, content, tags, metadata_json = p[:6]
            self._written_memories[memory_id] = {
                "id": memory_id,
                "user_id": user_id,
                "bank": bank,
                "content": content,
                "tags": list(tags) if tags else [],
                "metadata": json.loads(metadata_json)
                if isinstance(metadata_json, str)
                else (metadata_json or {}),
                "valid_from": datetime.now(timezone.utc),
                "valid_until": None,
                "created_at": datetime.now(timezone.utc),
            }
            return

        if q.startswith("update memories set valid_until"):
            memory_id, user_id = p[0], p[1]
            for store in (self._memories, self._written_memories):
                if memory_id in store and store[memory_id]["user_id"] == user_id:
                    if store[memory_id]["valid_until"] is None:
                        store[memory_id]["valid_until"] = datetime.now(timezone.utc)
            return

        if q.startswith("insert into entities"):
            entity_id = str(p[0])
            self._written_entities[entity_id] = {
                "entity_id": entity_id,
                "name": p[1],
                "entity_type": p[2],
                "aliases": list(p[3]) if p[3] else [],
                "context_tags": list(p[4]) if p[4] else [],
                "mention_count": 1,
                "user_id": p[5],
                "is_staged": p[7] if len(p) > 7 else False,
                "scope": "personal",
                "published_from": None,
            }
            return

        if q.startswith("update entities"):
            return

        if q.startswith("insert into entity_edges"):
            edge_id = str(p[0])
            self._written_edges.append(
                {
                    "id": edge_id,
                    "user_id": p[1],
                    "bank": p[2],
                    "src_entity_id": p[3],
                    "dst_entity_id": p[4],
                    "relation_type": p[5],
                    "evidence_id": p[6],
                    "valid_until": None,
                }
            )
            return

        if q.startswith("update entity_edges set valid_until"):
            return

        return super().execute(query, params)

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        self._record("fetch_one", query, params)
        q = _norm(query)
        p = params or ()

        if "count" in q and "memories" in q:
            return {"n": 1}

        if q.startswith("select id, user_id, bank, content, tags, metadata"):
            memory_id, user_id = p[0], p[1]
            mem = self._all_memories().get(str(memory_id))
            if mem and mem["user_id"] == user_id:
                return dict(mem)
            return None

        if q.startswith("select valid_until from memories"):
            memory_id, user_id = p[0], p[1]
            mem = self._all_memories().get(str(memory_id))
            if mem and mem["user_id"] == user_id:
                return {"valid_until": mem.get("valid_until")}
            return None

        if "select entity_id from entities" in q and "lower(name)" in q:
            user_id, name = p[0], p[1]
            for ent in {**self._entities, **self._written_entities}.values():
                if ent["user_id"] == user_id and ent["name"].lower() == str(name).lower():
                    return {"entity_id": ent["entity_id"]}
            return None

        if "select entity_id, name, aliases" in q and "scope = 'personal'" in q:
            user_id, name = p[0], p[1]
            for ent in {**self._entities, **self._written_entities}.values():
                if (
                    ent["user_id"] == user_id
                    and ent.get("scope") == "personal"
                    and ent["name"].lower() == str(name).lower()
                ):
                    return {
                        "entity_id": ent["entity_id"],
                        "name": ent["name"],
                        "aliases": ent.get("aliases") or [],
                        "context_tags": ent.get("context_tags") or [],
                        "mention_count": ent.get("mention_count", 1),
                        "is_staged": ent.get("is_staged", False),
                    }
            return None

        if "select name, entity_type, mention_count" in q and "lower(name) = lower" in q:
            name = p[-1]
            for ent in {**self._entities, **self._written_entities}.values():
                if ent["name"].lower() == str(name).lower():
                    return {
                        "name": ent["name"],
                        "entity_type": ent["entity_type"],
                        "mention_count": ent.get("mention_count", 1),
                        "aliases": ent.get("aliases") or [],
                        "context_tags": ent.get("context_tags") or [],
                        "scope": ent.get("scope", "personal"),
                    }
            return None

        if q.startswith("select id from entity_edges where user_id"):
            user_id, bank, src, dst, rel = p
            for edge in self._edges + self._written_edges:
                if (
                    edge["user_id"] == user_id
                    and edge["bank"] == bank
                    and edge["src_entity_id"] == src
                    and edge["dst_entity_id"] == dst
                    and edge["relation_type"] == rel
                ):
                    return {"id": edge.get("id", uuid.uuid4().hex)}
            return None

        return super().fetch_one(query, params)

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        self._record("fetch_all", query, params)
        q = _norm(query)
        p = params or ()

        if "select id from memories" in q and "content_tsv" in q:
            user_id, bank, as_of, query_text = p[0], p[1], p[2], p[3]
            terms = [t.lower() for t in re.findall(r"\w+", str(query_text)) if len(t) > 2]
            scored: list[tuple[int, str]] = []
            for mem in self._all_memories().values():
                if mem["user_id"] != user_id or mem["bank"] != bank:
                    continue
                if mem.get("valid_until") is not None and mem["valid_until"] < as_of:
                    continue
                content = mem["content"].lower()
                score = sum(1 for t in terms if t in content)
                if score > 0:
                    scored.append((score, mem["id"]))
            scored.sort(key=lambda x: (-x[0], x[1]))
            return [{"id": mid} for _, mid in scored[:20]]

        if (
            "select id from memories" in q
            and "order by valid_from desc" in q
            and "content_tsv" not in q
        ):
            user_id, bank, as_of = p[0], p[1], p[2]
            hits = []
            for mem in sorted(
                self._all_memories().values(),
                key=lambda m: m.get("valid_from") or _AS_OF_DEFAULT,
                reverse=True,
            ):
                if mem["user_id"] != user_id or mem["bank"] != bank:
                    continue
                if mem.get("valid_until") is not None and mem["valid_until"] < as_of:
                    continue
                hits.append({"id": mem["id"]})
            return hits[:20]

        if "select id, content" in q and "valid_from, valid_until" in q and "id = any" in q:
            ids, user_id, as_of = p[0], p[1], p[2]
            id_set = {str(i) for i in ids}
            rows = []
            for mem in self._all_memories().values():
                if mem["id"] not in id_set or mem["user_id"] != user_id:
                    continue
                if mem.get("valid_until") is not None and mem["valid_until"] < as_of:
                    continue
                row = {
                    "id": mem["id"],
                    "content": mem["content"],
                    "valid_from": mem.get("valid_from"),
                    "valid_until": mem.get("valid_until"),
                }
                if "bank" in q:
                    row["bank"] = mem.get("bank", "coding")
                rows.append(row)
            return rows

        if "select evidence_id" in q and "entity_edges" in q:
            evidence_ids, user_id, as_of = p[0], p[1], p[2]
            ev_set = {str(e) for e in evidence_ids}
            grouped: dict[str, set[str]] = {}
            for edge in self._edges + self._written_edges:
                if edge["user_id"] != user_id:
                    continue
                eid = str(edge.get("evidence_id", ""))
                if eid not in ev_set:
                    continue
                if edge.get("valid_until") is not None and edge["valid_until"] < as_of:
                    continue
                grouped.setdefault(eid, set()).update(
                    [edge["src_entity_id"], edge["dst_entity_id"]]
                )
            return [{"evidence_id": eid, "entity_ids": list(eids)} for eid, eids in grouped.items()]

        if "select entity_id from entities" in q and "name ilike any" in q:
            user_id, patterns, limit = p[0], p[1], p[2]
            pats = [str(x).strip("%").lower() for x in patterns]
            hits = []
            for ent in {**self._entities, **self._written_entities}.values():
                if ent["user_id"] != user_id:
                    continue
                name_l = ent["name"].lower()
                if any(pat in name_l for pat in pats):
                    hits.append(
                        {
                            "entity_id": ent["entity_id"],
                            "mention_count": ent.get("mention_count", 1),
                        }
                    )
            hits.sort(key=lambda r: r.get("mention_count", 0), reverse=True)
            return hits[: int(limit)]

        if "select name, entity_type, mention_count" in q and "name ilike" in q:
            pattern = str(p[-2]).strip("%").lower()
            limit = int(p[-1])
            hits = []
            for ent in {**self._entities, **self._written_entities}.values():
                if pattern in ent["name"].lower():
                    hits.append(
                        {
                            "name": ent["name"],
                            "entity_type": ent["entity_type"],
                            "mention_count": ent.get("mention_count", 1),
                            "aliases": ent.get("aliases") or [],
                            "context_tags": ent.get("context_tags") or [],
                            "scope": ent.get("scope", "personal"),
                        }
                    )
            hits.sort(key=lambda r: r["mention_count"], reverse=True)
            return hits[:limit]

        if "select evidence_id" in q and "from entity_edges" in q and "unnest" not in q:
            entity_ids, user_id, bank, as_of = p[0], p[1], p[2], p[3]
            seed = {str(e) for e in entity_ids}
            out: list[str] = []
            for edge in self._edges + self._written_edges:
                if edge["user_id"] != user_id or edge["bank"] != bank:
                    continue
                if edge.get("valid_until") is not None and edge["valid_until"] < as_of:
                    continue
                if edge["src_entity_id"] in seed or edge["dst_entity_id"] in seed:
                    ev = edge.get("evidence_id")
                    if ev:
                        out.append(str(ev))
            return [{"evidence_id": eid} for eid in dict.fromkeys(out)]

        return super().fetch_all(query, params)


class FakeEmbedder:
    def __init__(self) -> None:
        self.last_query: str = ""

    @property
    def vector_size(self) -> int:
        return 3

    def ping(self) -> bool:
        return True

    def embed(self, text: str) -> list[float]:
        self.last_query = text
        return [0.1, 0.2, 0.3]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


class FakeVS:
    """Vector store fake with bank/user_id filter enforcement."""

    def __init__(self, fixture: CodingBankFixture, *, user_id: str, embedder: FakeEmbedder) -> None:
        self._user_id = user_id
        self._fixture = fixture
        self._embedder = embedder
        self.search_calls: list[tuple[dict | None, int]] = []
        self._content_by_id: dict[str, str] = {}
        for bank_name, bank in fixture.raw["banks"].items():
            for mem in bank["memories"]:
                self._content_by_id[mem["memory_id"]] = mem["content"]
        self._hits = self._build_hits()

    def _build_hits(self) -> list[dict]:
        hits: list[dict] = []
        for bank_name, bank in self._fixture.raw["banks"].items():
            for mem in bank["memories"]:
                hits.append(
                    {
                        "id": f"pt-{mem['memory_id']}",
                        "score": 0.9,
                        "payload": {
                            "memory_id": mem["memory_id"],
                            "user_id": self._user_id,
                            "bank": bank_name,
                        },
                    }
                )
        return hits

    def ping(self) -> bool:
        return True

    def create_collection(self, name: str, vector_size: int) -> None:
        pass

    def ensure_payload_index(self, collection: str, field: str) -> None:
        pass

    def ensure_tenant_payload_index(self, collection: str, field: str) -> None:
        pass

    def upsert(self, collection: str, id: str, vector: list, payload: dict) -> None:
        self._hits.append(
            {
                "id": id,
                "score": 0.85,
                "payload": payload,
            }
        )

    def search(
        self,
        collection: str,
        vector: list,
        limit: int,
        threshold: float,
        filter: dict | None = None,
        sparse_query: str | None = None,
    ) -> list[dict]:
        self.search_calls.append((filter, limit))
        must = (filter or {}).get("must") or []
        filt_user = None
        filt_bank = None
        for clause in must:
            key = clause.get("key")
            val = (clause.get("match") or {}).get("value")
            if key == "user_id":
                filt_user = val
            if key == "bank":
                filt_bank = val
        query_terms = [
            t.lower()
            for t in re.findall(r"\w+", self._embedder.last_query or sparse_query or "")
            if len(t) > 2
        ]
        out = []
        for h in self._hits:
            payload = h.get("payload") or {}
            if filt_user is not None and payload.get("user_id") != filt_user:
                continue
            if filt_bank is not None and payload.get("bank") != filt_bank:
                continue
            if query_terms:
                content = self._content_by_id.get(str(payload.get("memory_id")), "").lower()
                if not any(t in content for t in query_terms):
                    continue
            out.append(h)
        return out[:limit]


def build_fake_stores_from_fixture(
    fixture: CodingBankFixture,
    *,
    user_id: str,
) -> tuple[_CursorIntegrationFakeStore, FakeVS, FakeEmbedder]:
    store = _CursorIntegrationFakeStore(fixture, user_id=user_id)
    embedder = FakeEmbedder()
    vs = FakeVS(fixture, user_id=user_id, embedder=embedder)
    return store, vs, embedder
