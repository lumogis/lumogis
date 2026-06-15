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
        self.purged_conversations: set[tuple[str, str]] = set()
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
            mid, cid, uid, role, content, model = p
            if "ON CONFLICT (message_id) DO NOTHING" in q and str(mid) in self.web_messages:
                return
            self.web_messages[str(mid)] = {
                "message_id": uuid.UUID(str(mid)),
                "conversation_id": uuid.UUID(str(cid)),
                "user_id": uid,
                "role": role,
                "content": content,
                "model": model,
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
            sid, summary, topics, entities, entity_ids, uid, scope = p
            self.sessions[str(sid)] = {
                "session_id": uuid.UUID(str(sid)),
                "summary": summary,
                "topics": topics,
                "entities": entities,
                "entity_ids": entity_ids,
                "user_id": uid,
                "scope": scope,
                "updated_at": _now(),
            }
            return

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        q = " ".join(query.split())
        p = params or ()

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
            if (uid, cid) in self.purged_conversations:
                return {"1": 1}
            return None

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

        if "FROM web_messages" in q and "message_id = %s" in q:
            if len(p) == 3:
                mid, cid, uid = str(p[0]), str(p[1]), str(p[2])
                row = self.web_messages.get(mid)
                if (
                    row
                    and str(row["conversation_id"]) == cid
                    and row["user_id"] == uid
                ):
                    return dict(row)
                return None
            mid = str(p[0])
            row = self.web_messages.get(mid)
            return dict(row) if row else None

        return None

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        q = " ".join(query.split())
        p = params or ()

        if "FROM sessions s" in q and "ORDER BY s.updated_at DESC" in q:
            uid, scope, limit = str(p[0]), str(p[1]), int(p[2])
            rows = [
                r for r in self.sessions.values() if r["user_id"] == uid and r.get("scope") == scope
            ]
            rows.sort(key=lambda r: r["updated_at"], reverse=True)
            out = []
            for r in rows[:limit]:
                item = dict(r)
                key = f"{r['session_id']}:{uid}"
                wc = self.web_conversations.get(key)
                if wc:
                    item["wc_title"] = wc.get("title")
                    item["message_count"] = wc.get("message_count")
                out.append(item)
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
