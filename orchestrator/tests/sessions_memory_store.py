# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""In-memory MetadataStore for conversation / session tests (LUM-162)."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime
from datetime import timezone
from typing import Any


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SessionsMemoryMetadataStore:
    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}
        self.web_conversations: dict[str, dict[str, Any]] = {}
        self.web_messages: dict[str, dict[str, Any]] = {}
        self.action_proposals: dict[int, dict[str, Any]] = {}
        self.purged_conversations: set[tuple[str, str]] = set()
        # Richer tombstone state used by the reconciliation sweeper (LUM-416).
        self.purge_tombstone_data: dict[tuple[str, str], dict[str, Any]] = {}
        self._fail_next_postgres = False

    def ping(self) -> bool:
        return True

    def execute(self, query: str, params: tuple | None = None) -> None:
        if self._fail_next_postgres:
            self._fail_next_postgres = False
            raise RuntimeError("simulated postgres failure")
        q = " ".join(query.split())
        p = params or ()

        if "DELETE FROM web_messages" in q:
            cid, uid = str(p[0]), str(p[1])
            for mid in list(self.web_messages):
                row = self.web_messages[mid]
                if str(row["conversation_id"]) == cid and row["user_id"] == uid:
                    del self.web_messages[mid]
            return

        if "DELETE FROM web_conversations" in q:
            cid, uid = str(p[0]), str(p[1])
            key = f"{cid}:{uid}"
            self.web_conversations.pop(key, None)
            return

        if "DELETE FROM sessions WHERE published_from" in q:
            src = str(p[0])
            for sid in list(self.sessions):
                if str(self.sessions[sid].get("published_from")) == src:
                    del self.sessions[sid]
            return

        if "DELETE FROM sessions WHERE session_id" in q:
            sid, uid = str(p[0]), str(p[1])
            row = self.sessions.get(sid)
            if row and row["user_id"] == uid and row.get("scope") == "personal":
                del self.sessions[sid]
            return

        if q.startswith("INSERT INTO purged_conversations"):
            uid, cid = str(p[0]), str(p[1])
            self.purged_conversations.add((uid, cid))
            if (uid, cid) not in self.purge_tombstone_data:
                self.purge_tombstone_data[(uid, cid)] = {
                    "user_id": uid,
                    "session_id": cid,
                    "qdrant_deleted": False,
                    "graph_deleted": False,
                    "errors": [],
                    "sweep_attempts": 0,
                    "resolved_at": None,
                }
            return

        if "UPDATE purged_conversations" in q and "qdrant_deleted" in q:
            qdrant_ok, graph_ok, errors_json = bool(p[0]), bool(p[1]), p[2]
            uid, cid = str(p[-2]), str(p[-1])
            entry = self.purge_tombstone_data.get((uid, cid))
            if entry is not None:
                entry["qdrant_deleted"] = qdrant_ok
                entry["graph_deleted"] = graph_ok
                import json as _json

                entry["errors"] = (
                    _json.loads(errors_json) if isinstance(errors_json, str) else errors_json
                )
                if "sweep_attempts = sweep_attempts + 1" in q:
                    entry["sweep_attempts"] = entry.get("sweep_attempts", 0) + 1
                if "resolved_at = NOW()" in q:
                    entry["resolved_at"] = _now()
            return

        if q.startswith("INSERT INTO web_conversations"):
            cid, uid, title, model = p[:4]
            cid_str = str(cid)
            existing_key = next(
                (
                    k
                    for k, row in self.web_conversations.items()
                    if str(row["conversation_id"]) == cid_str
                ),
                None,
            )
            if existing_key is not None:
                existing = self.web_conversations[existing_key]
                if existing["user_id"] != uid:
                    return
                if title:
                    existing["title"] = title
                if model:
                    existing["model"] = model
                existing["updated_at"] = _now()
                return
            key = f"{cid}:{uid}"
            self.web_conversations[key] = {
                "conversation_id": uuid.UUID(cid_str),
                "user_id": uid,
                "title": title or "",
                "model": model or "",
                "message_count": 0,
                "updated_at": _now(),
            }
            return

        if q.startswith("INSERT INTO web_messages"):
            if len(p) >= 8:
                mid, cid, uid, role, content, model, source_refs, action_proposal_id = p[:8]
            else:
                mid, cid, uid, role, content, model = p
                source_refs, action_proposal_id = None, None
            if "ON CONFLICT (message_id) DO NOTHING" in q and str(mid) in self.web_messages:
                return
            refs_value = source_refs
            if isinstance(source_refs, str):
                import json

                refs_value = json.loads(source_refs)
            self.web_messages[str(mid)] = {
                "message_id": uuid.UUID(str(mid)),
                "conversation_id": uuid.UUID(str(cid)),
                "user_id": uid,
                "role": role,
                "content": content,
                "model": model,
                "source_refs": refs_value,
                "action_proposal_id": action_proposal_id,
                "created_at": _now(),
            }
            key = f"{cid}:{uid}"
            wc = self.web_conversations.get(key)
            if wc:
                wc["message_count"] = int(wc.get("message_count", 0)) + 1
            return

        if "UPDATE web_conversations SET title" in q:
            title, cid, uid = p
            key = f"{cid}:{uid}"
            row = self.web_conversations.get(key)
            if row:
                row["title"] = title
                row["updated_at"] = _now()
            return

        if "UPDATE web_conversations SET model" in q:
            model, cid, uid = p
            key = f"{cid}:{uid}"
            row = self.web_conversations.get(key)
            if row:
                row["model"] = model
                row["updated_at"] = _now()
            return

        if q.startswith("INSERT INTO sessions"):
            # LUM-582 — project_session inserts an 8th param (published_from); keep
            # tolerant so both the pre-existing (7-arg) and projection (8-arg)
            # shapes work.
            sid, summary, topics, entities, entity_ids, uid, scope = p[:7]
            published_from = p[7] if len(p) > 7 else None
            self.sessions[str(sid)] = {
                "session_id": uuid.UUID(str(sid)),
                "summary": summary,
                "topics": topics,
                "entities": entities,
                "entity_ids": entity_ids,
                "user_id": uid,
                "scope": scope,
                "published_from": published_from,
                "updated_at": _now(),
            }
            return

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        q = " ".join(query.split())
        p = params or ()

        # LUM-582 — project_session INSERT ... RETURNING * (projection row). The
        # deterministic projection pk makes re-publish overwrite the same slot.
        if q.startswith("INSERT INTO sessions") and "RETURNING" in q:
            sid, summary, topics, entities, entity_ids, uid, scope = p[:7]
            published_from = p[7] if len(p) > 7 else None
            row = {
                "session_id": uuid.UUID(str(sid)),
                "summary": summary,
                "topics": topics,
                "entities": entities,
                "entity_ids": entity_ids,
                "user_id": uid,
                "scope": scope,
                "published_from": published_from,
                "updated_at": _now(),
            }
            self.sessions[str(sid)] = row
            return dict(row)

        # LUM-582 — project_session preserve-on-omit lookup (by published_from).
        if "SELECT summary FROM sessions WHERE published_from" in q:
            src, scope = str(p[0]), str(p[1])
            for row in self.sessions.values():
                if str(row.get("published_from")) == src and row.get("scope") == scope:
                    return {"summary": row.get("summary", "")}
            return None

        # LUM-582 — get_conversation household-union detail query (has proj_summary).
        if "proj_summary" in q:
            cid, caller = str(p[0]), str(p[1])
            row = self.sessions.get(cid)
            if not row:
                return None
            scope = row.get("scope") or "personal"
            uid = row["user_id"]
            include_shared = "IN ('shared'" in q  # allows_shared gate (LUM-577)
            allowed_scopes = ("shared", "system") if include_shared else ("system",)
            visible = (scope == "personal" and uid == caller) or scope in allowed_scopes
            if not visible:
                return None
            out = dict(row)
            key = f"{cid}:{uid}"
            wc = self.web_conversations.get(key)
            if wc:
                out["wc_title"] = wc.get("title")
                out["message_count"] = wc.get("message_count")
            proj = next(
                (
                    r.get("summary")
                    for r in self.sessions.values()
                    if str(r.get("published_from")) == cid and r.get("scope") == "shared"
                ),
                None,
            )
            out["proj_summary"] = proj
            return out

        if "FROM sessions" in q and "SELECT summary" in q:
            sid, uid = str(p[0]), str(p[1])
            row = self.sessions.get(sid)
            if row and row["user_id"] == uid:
                return {"summary": row.get("summary", "")}
            return None

        if "FROM sessions" in q and "session_id = %s" in q and "scope = 'personal'" in q:
            sid, uid = str(p[0]), str(p[1])
            row = self.sessions.get(sid)
            if row and row["user_id"] == uid and row.get("scope") == "personal":
                return {"session_id": row["session_id"]}
            return None

        if "FROM purged_conversations" in q:
            uid, cid = str(p[0]), str(p[1])
            if (uid, cid) not in self.purged_conversations:
                return None
            if "qdrant_deleted" in q:
                # _tombstone_fetch — return richer state for sweeper
                entry = self.purge_tombstone_data.get((uid, cid), {})
                return {
                    "qdrant_deleted": entry.get("qdrant_deleted", False),
                    "graph_deleted": entry.get("graph_deleted", False),
                    "sweep_attempts": entry.get("sweep_attempts", 0),
                }
            return {"1": 1}

        if "FROM sessions s" in q and "WHERE s.session_id" in q:
            sid, uid = str(p[0]), str(p[1])
            row = self.sessions.get(sid)
            if row and row["user_id"] == uid:
                out = dict(row)
                key = f"{sid}:{uid}"
                wc = self.web_conversations.get(key)
                if wc:
                    out["wc_title"] = wc.get("title")
                    out["message_count"] = wc.get("message_count")
                return out
            return None

        if "FROM web_conversations" in q and "SELECT user_id" in q:
            cid = str(p[0])
            for key, row in self.web_conversations.items():
                if str(row["conversation_id"]) == cid:
                    return {"user_id": row["user_id"]}
            return None

        if "FROM web_conversations" in q:
            if "SELECT user_id FROM web_conversations" in q:
                cid = str(p[0])
                for row in self.web_conversations.values():
                    if str(row["conversation_id"]) == cid:
                        return {"user_id": row["user_id"]}
                return None
            cid, uid = str(p[0]), str(p[1])
            key = f"{cid}:{uid}"
            row = self.web_conversations.get(key)
            if row:
                if "title" in q or "message_count" in q:
                    return dict(row)
                return {"conversation_id": row["conversation_id"]}
            return None

        if "FROM action_proposals" in q and "WHERE id = %s" in q:
            pid = int(p[0])
            row = self.action_proposals.get(pid)
            if not row:
                return None
            if "SELECT user_id" in q:
                return {"user_id": row["user_id"]}
            return dict(row)

        if "FROM web_messages" in q and "message_id = %s" in q:
            if len(p) == 3:
                mid, cid, uid = str(p[0]), str(p[1]), str(p[2])
                row = self.web_messages.get(mid)
                if row and str(row["conversation_id"]) == cid and row["user_id"] == uid:
                    return dict(row)
                return None
            mid = str(p[0])
            row = self.web_messages.get(mid)
            return dict(row) if row else None

        return None

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        q = " ".join(query.split())
        p = params or ()

        # LUM-582 — shared-source lookup (which of the caller's sessions are shared).
        if "DISTINCT published_from FROM sessions" in q:
            uid = str(p[0])
            return [
                {"published_from": r["published_from"]}
                for r in self.sessions.values()
                if r["user_id"] == uid
                and r.get("scope") == "shared"
                and r.get("published_from") is not None
            ]

        # LUM-582 — list_conversations household-union query. Params are the
        # visible_filter params (the caller for the personal arm) + the collapse
        # caller + limit. ``allows_shared=false`` members get a union WITHOUT the
        # shared arm (``IN ('shared'`` present only when shared is allowed; the
        # proj_summary subquery uses ``= 'shared'``, so this discriminates).
        if "ORDER BY s.updated_at DESC" in q:
            caller, collapse_caller, limit = str(p[0]), str(p[-2]), int(p[-1])
            include_shared = "IN ('shared'" in q
            out = []
            for r in self.sessions.values():
                scope = r.get("scope") or "personal"
                uid = r["user_id"]
                allowed_scopes = ("shared", "system") if include_shared else ("system",)
                visible = (scope == "personal" and uid == caller) or scope in allowed_scopes
                if not visible:
                    continue
                if r.get("published_from") is not None and uid == collapse_caller:
                    continue  # collapse the caller's own projection duplicate
                item = dict(r)
                key = f"{r['session_id']}:{uid}"
                wc = self.web_conversations.get(key)
                if wc:
                    item["wc_title"] = wc.get("title")
                    item["message_count"] = wc.get("message_count")
                out.append(item)
            out.sort(key=lambda r: r["updated_at"], reverse=True)
            return out[:limit]

        if "FROM purged_conversations" in q and "resolved_at IS NULL" in q:
            max_attempts = int(p[0]) if p else 20
            out = []
            for (uid, cid), entry in self.purge_tombstone_data.items():
                if (
                    entry.get("resolved_at") is None
                    and entry.get("sweep_attempts", 0) < max_attempts
                ):
                    out.append(
                        {
                            "user_id": uid,
                            "session_id": cid,
                            "qdrant_deleted": entry.get("qdrant_deleted", False),
                            "graph_deleted": entry.get("graph_deleted", False),
                        }
                    )
            return out

        if "FROM web_messages" in q and "ORDER BY created_at ASC" in q:
            cid, uid = str(p[0]), str(p[1])
            rows = [
                m
                for m in self.web_messages.values()
                if str(m["conversation_id"]) == cid and m["user_id"] == uid
            ]
            rows.sort(key=lambda m: m["created_at"])
            return rows

        return []

    def close(self) -> None:
        pass

    @contextmanager
    def transaction(self):
        yield
