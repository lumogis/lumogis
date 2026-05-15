# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""In-memory MetadataStore shim for ``proposal_queue`` tests (LUM-123)."""

from __future__ import annotations

import contextlib
from datetime import datetime
from datetime import timedelta
from datetime import timezone


def _norm(query: str) -> str:
    return " ".join(query.split()).lower()


class FakeProposalQueueStore:
    """In-memory MetadataStore shim for migration-free proposal-queue tests."""

    def __init__(self) -> None:
        self.rows: dict[int, dict] = {}
        self._next_id = 1

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        pass

    @contextlib.contextmanager
    def transaction(self):
        yield

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        q = _norm(query)
        p = params or ()

        if "from action_proposals where id = %s and user_id = %s" in q:
            pid, uid = int(p[0]), str(p[1])
            r = self.rows.get(pid)
            if not r or r["user_id"] != uid:
                return None
            return {
                "id": r["id"],
                "status": r["status"],
                "action_name": r["action_name"],
                "payload": r["payload"],
                "claimed_at": r["claimed_at"],
                "claimed_by": r["claimed_by"],
                "run_after": r["run_after"],
            }

        if "with next_eligible as" in q and "for update skip locked" in q:
            (worker_id,) = p
            now = datetime.now(timezone.utc)
            candidates = sorted(
                (
                    r
                    for r in self.rows.values()
                    if r["status"] == "approved" and r["claimed_at"] is None
                ),
                key=lambda r: r["id"],
            )
            for row in candidates:
                ra = row["run_after"]
                if hasattr(ra, "tzinfo") and ra.tzinfo is None:
                    ra = ra.replace(tzinfo=timezone.utc)
                if ra > now:
                    continue
                row["status"] = "executing"
                row["claimed_at"] = now
                row["claimed_by"] = worker_id
                return dict(self._claimed_row_sql_shape(row))

            return None

        if (
            q.startswith("update action_proposals set")
            and "claimed_by = %s" in q
            and "where id = %s and user_id = %s" in q
        ):
            worker_id, pid, uid = str(p[0]), int(p[1]), str(p[2])
            row = self.rows.get(pid)
            if (
                not row
                or row["user_id"] != uid
                or row["status"] != "approved"
                or row["claimed_at"] is not None
            ):
                return None
            now = datetime.now(timezone.utc)
            row["status"] = "executing"
            row["claimed_at"] = now
            row["claimed_by"] = worker_id
            return dict(self._claimed_row_sql_shape(row))

        if q.startswith("select attempt from action_proposals"):
            (jid,) = p
            r = self.rows.get(int(jid))
            if not r or r["status"] != "executing":
                return None
            return {"attempt": r["attempt"]}

        return None

    def _claimed_row_sql_shape(self, row: dict) -> dict:
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "action_name": row["action_name"],
            "payload": row["payload"],
            "attempt": row["attempt"],
            "created_at": row["created_at"],
            "status": row["status"],
        }

    def execute(self, query: str, params: tuple | None = None) -> None:
        q = _norm(query)
        p = params or ()

        if (
            q.startswith("update action_proposals set")
            and "status = 'done'" in q
            and "executed_at" in q
        ):
            (jid,) = p
            r = self.rows.get(int(jid))
            if r and r["status"] == "executing":
                r["status"] = "done"
                r["finished_at"] = datetime.now(timezone.utc)
                r["executed_at"] = r["finished_at"]
            return

        if (
            "status = case when attempt + 1" in q
            and "status = 'executing'" in q
            and q.startswith("update action_proposals set")
        ):
            err, ma, ma2, ma3, backoff_min, jid = p
            ma = int(ma)
            jid = int(jid)
            r = self.rows.get(jid)
            if not r or r["status"] != "executing":
                return
            r["attempt"] = int(r["attempt"]) + 1
            r["error"] = (err or "")[:1000]
            if r["attempt"] < ma:
                r["status"] = "approved"
                r["finished_at"] = None
                r["executed_at"] = None
                r["claimed_at"] = None
                r["claimed_by"] = None
                r["run_after"] = datetime.now(timezone.utc) + timedelta(minutes=int(backoff_min))
            else:
                r["status"] = "dead"
                r["finished_at"] = datetime.now(timezone.utc)
                r["claimed_at"] = None
                r["claimed_by"] = None
                r["executed_at"] = None

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        q = _norm(query)
        p = params or ()

        if "returning id, user_id" in q and "status in ('executing', 'claimed')" in q:
            err, stuck_s = str(p[0]), int(p[1])
            now = datetime.now(timezone.utc)
            out: list[dict] = []
            for r in list(self.rows.values()):
                if r["status"] not in ("executing", "claimed"):
                    continue
                cat = r.get("claimed_at")
                if cat is None:
                    continue
                if hasattr(cat, "tzinfo") and cat.tzinfo is None:
                    cat = cat.replace(tzinfo=timezone.utc)
                if now - cat < timedelta(seconds=stuck_s):
                    continue
                r["status"] = "dead"
                r["finished_at"] = now
                r["error"] = err
                r["claimed_at"] = None
                r["claimed_by"] = None
                out.append({"id": r["id"], "user_id": r["user_id"]})
            return out

        return []
