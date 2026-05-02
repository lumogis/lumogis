# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""In-memory MetadataStore for Phase-``5`` capture tests (**5B–5G**).

Understands SQL emitted by ``services.captures`` for **5B**–**5G**.
"""

from __future__ import annotations

import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CapturesMemoryMetadataStore:
    """Minimal fake: ``captures`` + optional ``capture_attachments`` / ``capture_transcripts``."""

    def __init__(self) -> None:
        self.captures: dict[str, dict[str, Any]] = {}
        self._by_client: dict[tuple[str, str], str] = {}
        self.attachments: dict[str, dict[str, Any]] = {}
        self.transcripts: dict[str, dict[str, Any]] = {}
        self.notes: dict[str, dict[str, Any]] = {}

    def ping(self) -> bool:
        return True

    def execute(self, query: str, params: tuple | None = None) -> None:
        q = " ".join(query.split())
        p = params or ()
        if "DELETE FROM captures" in q:
            cap_id, user_id = str(p[0]), str(p[1])
            if cap_id in self.captures and self.captures[cap_id]["user_id"] == user_id:
                self._purge_capture(cap_id)
            return
        if "DELETE FROM capture_attachments" in q:
            att_id, cap_id, user_id = str(p[0]), str(p[1]), str(p[2])
            row = self.attachments.get(att_id)
            if row and str(row["capture_id"]) == cap_id and str(row["user_id"]) == user_id:
                del self.attachments[att_id]
            return
        if q.startswith("UPDATE captures SET capture_type ="):
            cap_type, cap_id, user_id = p
            cap_id, user_id = str(cap_id), str(user_id)
            row = self.captures.get(cap_id)
            if row and row["user_id"] == user_id:
                row["capture_type"] = cap_type
                row["updated_at"] = _now()
            return
        if q.startswith("UPDATE capture_transcripts SET"):
            (
                provider,
                model,
                transcript_text,
                transcript_status,
                language,
                confidence,
                error,
                tr_id,
                uid,
            ) = p
            tr_id, uid = str(tr_id), str(uid)
            row = self.transcripts.get(tr_id)
            if row and str(row["user_id"]) == uid:
                row["provider"] = provider
                row["model"] = model
                row["transcript_text"] = transcript_text
                row["transcript_status"] = transcript_status
                row["language"] = language
                row["confidence"] = confidence
                row["error"] = error
                row["updated_at"] = _now()
            return
        if "DELETE FROM notes WHERE" in q:
            nid = str(p[0])
            self.notes.pop(nid, None)
            return
        if "UPDATE captures SET status = 'failed'" in q:
            last_err, cap_id, user_id = str(p[0]), str(p[1]), str(p[2])
            row = self.captures.get(cap_id)
            if row and row["user_id"] == user_id:
                row["status"] = "failed"
                row["last_error"] = last_err
                row["updated_at"] = _now()
            return
        if "UPDATE captures SET status = 'indexed'" in q:
            note_id, indexed_at, cap_id, user_id = p
            cap_id, user_id = str(cap_id), str(user_id)
            nid = str(note_id)
            row = self.captures.get(cap_id)
            if row and row["user_id"] == user_id:
                row["status"] = "indexed"
                row["note_id"] = uuid.UUID(nid)
                row["indexed_at"] = indexed_at
                row["last_error"] = None
                row["updated_at"] = _now()
            return
        if q.startswith("UPDATE captures SET text ="):
            (
                text,
                title,
                url,
                tags,
                capture_type,
                cap_id,
                user_id,
            ) = p
            cap_id = str(cap_id)
            user_id = str(user_id)
            row = self.captures.get(cap_id)
            if row and row["user_id"] == user_id:
                row["text"] = text
                row["title"] = title
                row["url"] = url
                row["tags"] = tags
                row["capture_type"] = capture_type
                row["updated_at"] = _now()
            return
        raise NotImplementedError(f"CapturesMemoryMetadataStore.execute not implemented: {q[:80]}")

    def _purge_capture(self, cap_id: str) -> None:
        self.captures.pop(cap_id, None)
        for k, v in list(self._by_client.items()):
            if v == cap_id:
                del self._by_client[k]
        for aid, row in list(self.attachments.items()):
            if str(row["capture_id"]) == cap_id:
                del self.attachments[aid]
        for tid, row in list(self.transcripts.items()):
            if str(row["capture_id"]) == cap_id:
                del self.transcripts[tid]

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        q = " ".join(query.split())
        p = params or ()
        qn = re.sub(r"%s::uuid", "%s", q, flags=re.IGNORECASE)

        if "INSERT INTO notes" in q and "RETURNING" in q:
            text, uid, source, scope = p
            nid = str(uuid.uuid4())
            now = _now()
            self.notes[nid] = {
                "note_id": uuid.UUID(nid),
                "text": text,
                "user_id": str(uid),
                "source": source,
                "scope": scope,
                "created_at": now,
                "updated_at": now,
            }
            return {"note_id": self.notes[nid]["note_id"]}

        if "INSERT INTO captures" in q and "RETURNING" in q:
            user_id, cap_type, title, text, url, client_id, tags = (
                str(p[0]),
                str(p[1]),
                p[2],
                p[3],
                p[4],
                p[5],
                p[6],
            )
            cid = str(uuid.uuid4())
            row = {
                "id": uuid.UUID(cid),
                "user_id": user_id,
                "status": "pending",
                "capture_type": cap_type,
                "title": title,
                "text": text,
                "url": url,
                "local_client_id": client_id,
                "note_id": None,
                "source_channel": "lumogis_web",
                "tags": tags,
                "last_error": None,
                "captured_at": None,
                "synced_at": None,
                "indexed_at": None,
                "created_at": _now(),
                "updated_at": _now(),
            }
            self.captures[cid] = row
            if client_id:
                self._by_client[(user_id, str(client_id))] = cid
            return {"id": row["id"], "status": "pending"}

        if "FROM captures WHERE user_id = %s AND local_client_id = %s" in q:
            user_id, lc = str(p[0]), str(p[1])
            cap_id = self._by_client.get((user_id, lc))
            if cap_id is None:
                return None
            r = self.captures[cap_id]
            return dict(r)

        if "SELECT * FROM captures WHERE id = %s AND user_id = %s" in qn:
            cap_id, user_id = str(p[0]), str(p[1])
            r = self.captures.get(cap_id)
            if r is None or r["user_id"] != user_id:
                return None
            return dict(r)

        if "SELECT status FROM captures WHERE id = %s AND user_id = %s" in qn:
            cap_id, user_id = str(p[0]), str(p[1])
            r = self.captures.get(cap_id)
            if r is None or r["user_id"] != user_id:
                return None
            return {"status": r["status"]}

        if "SELECT COUNT(*) AS c FROM captures WHERE user_id = %s" in q:
            uid = str(p[0])
            n = sum(1 for r in self.captures.values() if r["user_id"] == uid)
            return {"c": n}

        if "INSERT INTO capture_attachments" in q and "RETURNING" in q:
            (
                att_id,
                cap_id,
                uid,
                att_type,
                storage_key,
                orig_fn,
                mime,
                size_b,
                _sha,
                lc_att,
            ) = p
            att_id, cap_id, uid = str(att_id), str(cap_id), str(uid)
            created = _now()
            row = {
                "id": uuid.UUID(att_id),
                "capture_id": uuid.UUID(cap_id),
                "user_id": uid,
                "attachment_type": att_type,
                "storage_key": storage_key,
                "original_filename": orig_fn,
                "mime_type": mime,
                "size_bytes": int(size_b),
                "sha256": _sha,
                "processing_status": "stored",
                "client_attachment_id": lc_att,
                "created_at": created,
            }
            self.attachments[att_id] = row
            return {
                "id": row["id"],
                "attachment_type": att_type,
                "mime_type": mime,
                "size_bytes": int(size_b),
                "original_filename": orig_fn,
                "processing_status": "stored",
                "created_at": created,
            }

        if (
            "processing_status, created_at FROM capture_attachments" in q
            and "client_attachment_id = %s" in qn
        ):
            user_id, cap_id, lc = str(p[0]), str(p[1]), str(p[2])
            for a in self.attachments.values():
                if (
                    str(a["user_id"]) == user_id
                    and str(a["capture_id"]) == cap_id
                    and (a.get("client_attachment_id") or "") == lc
                ):
                    return dict(a)
            return None

        if "SELECT storage_key, mime_type, original_filename FROM capture_attachments" in q:
            att_id, cap_id, user_id = str(p[0]), str(p[1]), str(p[2])
            a = self.attachments.get(att_id)
            if a is None or str(a["capture_id"]) != cap_id or str(a["user_id"]) != user_id:
                return None
            return {
                "storage_key": a["storage_key"],
                "mime_type": a["mime_type"],
                "original_filename": a.get("original_filename"),
            }

        if "SELECT storage_key FROM capture_attachments" in q and "WHERE id = %s" in qn:
            att_id, cap_id, user_id = str(p[0]), str(p[1]), str(p[2])
            a = self.attachments.get(att_id)
            if a is None or str(a["capture_id"]) != cap_id or str(a["user_id"]) != user_id:
                return None
            return {"storage_key": a["storage_key"]}

        if (
            "processing_status, size_bytes, created_at FROM capture_attachments "
            "WHERE id = %s AND capture_id = %s AND user_id = %s" in qn
        ):
            att_id, cap_id, user_id = str(p[0]), str(p[1]), str(p[2])
            a = self.attachments.get(att_id)
            if a is None or str(a["capture_id"]) != cap_id or str(a["user_id"]) != user_id:
                return None
            return dict(a)

        if (
            "FROM capture_transcripts WHERE attachment_id = %s AND capture_id = %s "
            "AND user_id = %s ORDER BY created_at DESC LIMIT 1" in qn
        ):
            aid, cid, uid = str(p[0]), str(p[1]), str(p[2])
            rows = [
                dict(t)
                for t in self.transcripts.values()
                if str(t["attachment_id"]) == aid
                and str(t["capture_id"]) == cid
                and str(t["user_id"]) == uid
            ]
            if not rows:
                return None
            rows.sort(key=lambda r: r["created_at"], reverse=True)
            return dict(rows[0])

        if "INSERT INTO capture_transcripts" in q and "RETURNING" in q:
            (
                cap_id,
                att_id,
                uid,
                provider,
                model,
                text,
                status_t,
                language,
                conf,
                err,
            ) = p
            cap_id, att_id, uid = str(cap_id), str(att_id), str(uid)
            tid = str(uuid.uuid4())
            now = _now()
            row = {
                "id": uuid.UUID(tid),
                "capture_id": uuid.UUID(cap_id),
                "attachment_id": uuid.UUID(att_id),
                "user_id": uid,
                "provider": provider,
                "model": model,
                "transcript_text": text,
                "transcript_status": status_t,
                "transcript_provenance": "server_stt",
                "language": language,
                "confidence": conf,
                "error": err,
                "created_at": now,
                "updated_at": now,
            }
            self.transcripts[tid] = row
            return {
                "id": row["id"],
                "attachment_id": row["attachment_id"],
                "transcript_status": status_t,
                "transcript_text": text,
                "transcript_provenance": "server_stt",
                "language": language,
                "confidence": conf,
                "created_at": now,
                "updated_at": now,
            }

        if (
            "language, confidence, created_at, updated_at FROM capture_transcripts "
            "WHERE id = %s AND user_id = %s" in qn
        ):
            tr_id, uid = str(p[0]), str(p[1])
            t = self.transcripts.get(tr_id)
            if t is None or str(t["user_id"]) != uid:
                return None
            return {
                "id": t["id"],
                "attachment_id": t["attachment_id"],
                "transcript_status": t["transcript_status"],
                "transcript_text": t.get("transcript_text"),
                "transcript_provenance": t["transcript_provenance"],
                "language": t.get("language"),
                "confidence": t.get("confidence"),
                "created_at": t["created_at"],
                "updated_at": t["updated_at"],
            }

        if "SELECT 1 AS x FROM capture_attachments" in q and "LIMIT 1" in q:
            cap_id, user_id = str(p[0]), str(p[1])
            for a in self.attachments.values():
                if str(a["capture_id"]) == cap_id and str(a["user_id"]) == user_id:
                    return {"x": 1}
            return None

        raise NotImplementedError(f"CapturesMemoryMetadataStore.fetch_one not implemented: {q[:120]}")

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        q = " ".join(query.split())
        p = params or ()
        qn = re.sub(r"%s::uuid", "%s", q, flags=re.IGNORECASE)

        if "SELECT storage_key FROM capture_attachments WHERE capture_id = %s AND user_id = %s" in qn:
            cap_id, user_id = str(p[0]), str(p[1])
            return [
                {"storage_key": a["storage_key"]}
                for a in self.attachments.values()
                if str(a["capture_id"]) == cap_id and str(a["user_id"]) == user_id
            ]

        if (
            "FROM capture_attachments" in q
            and "capture_id = %s AND user_id = %s" in qn
            and "ORDER BY created_at" in qn
        ):
            cap_id, user_id = str(p[0]), str(p[1])
            rows = [
                dict(a)
                for a in self.attachments.values()
                if str(a["capture_id"]) == cap_id and str(a["user_id"]) == user_id
            ]
            rows.sort(key=lambda r: r["created_at"])
            if "attachment_type = 'audio'" in q:
                rows = [r for r in rows if r.get("attachment_type") == "audio"]
            return rows

        if "FROM capture_transcripts WHERE capture_id = %s AND user_id = %s" in qn:
            cap_id, user_id = str(p[0]), str(p[1])
            rows = [
                dict(t)
                for t in self.transcripts.values()
                if str(t["capture_id"]) == cap_id and str(t["user_id"]) == user_id
            ]
            rows.sort(key=lambda r: r["created_at"])
            return rows

        if "FROM captures c WHERE user_id = %s" in q and "attachment_count" in q:
            uid = str(p[0])
            limit, offset = int(p[1]), int(p[2])
            rows = [dict(r) for r in self.captures.values() if r["user_id"] == uid]
            rows.sort(key=lambda r: r["updated_at"], reverse=True)
            sliced = rows[offset : offset + limit]
            out = []
            for r in sliced:
                cid = str(r["id"])
                ac = sum(1 for a in self.attachments.values() if str(a["capture_id"]) == cid)
                tc = sum(1 for t in self.transcripts.values() if str(t["capture_id"]) == cid)
                d = dict(r)
                d["attachment_count"] = ac
                d["transcript_count"] = tc
                out.append(d)
            return out

        raise NotImplementedError(f"CapturesMemoryMetadataStore.fetch_all not implemented: {q[:120]}")

    def close(self) -> None:
        pass

    @contextmanager
    def transaction(self):
        yield
