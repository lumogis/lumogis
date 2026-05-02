# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for ``services/batch_queue`` with in-memory SQL fake."""

from __future__ import annotations

import contextlib
import json
from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest
from pydantic import BaseModel

import config as _config
from services import batch_queue


def _norm(query: str) -> str:
    return " ".join(query.split()).lower()


@pytest.fixture(autouse=True)
def _clear_batch_handlers():
    saved = dict(batch_queue._handlers)
    batch_queue._handlers.clear()
    yield
    batch_queue._handlers.clear()
    batch_queue._handlers.update(saved)


class _FakeBatchQueueStore:
    """In-memory store matching ``batch_queue`` canonical SQL."""

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
        if q.startswith("insert into user_batch_jobs"):
            uid, kind, payload_json, run_after = p
            rid = self._next_id
            self._next_id += 1
            pl = json.loads(payload_json) if isinstance(payload_json, str) else payload_json
            self.rows[rid] = {
                "id": rid,
                "user_id": uid,
                "kind": kind,
                "payload": pl,
                "status": "pending",
                "attempt": 0,
                "run_after": run_after,
                "enqueued_at": datetime.now(timezone.utc),
                "started_at": None,
                "finished_at": None,
                "error": None,
                "worker_id": None,
            }
            return {"id": rid}

        if "with next_eligible as" in q and "for update skip locked" in q:
            cap, worker_id = p
            cap = int(cap)
            now = datetime.now(timezone.utc)
            candidates = sorted(
                (r for r in self.rows.values() if r["status"] == "pending"),
                key=lambda r: r["id"],
            )
            for row in candidates:
                ra = row["run_after"]
                if hasattr(ra, "tzinfo") and ra.tzinfo is None:
                    ra = ra.replace(tzinfo=timezone.utc)
                if ra > now:
                    continue
                uid = row["user_id"]
                running = sum(1 for r in self.rows.values() if r["user_id"] == uid and r["status"] == "running")
                if running >= cap:
                    continue
                row["status"] = "running"
                row["started_at"] = now
                row["worker_id"] = worker_id
                return {
                    "id": row["id"],
                    "user_id": row["user_id"],
                    "kind": row["kind"],
                    "payload": row["payload"],
                    "attempt": row["attempt"],
                    "enqueued_at": row["enqueued_at"],
                }
            return None

        if q.startswith("select attempt from user_batch_jobs"):
            (jid,) = p
            r = self.rows.get(int(jid))
            if not r or r["status"] != "running":
                return None
            return {"attempt": r["attempt"]}
        return None

    def execute(self, query: str, params: tuple | None = None) -> None:
        q = _norm(query)
        p = params or ()

        if q.startswith("update user_batch_jobs set") and "status = 'done'" in q:
            (jid,) = p
            r = self.rows.get(int(jid))
            if r:
                r["status"] = "done"
                r["finished_at"] = datetime.now(timezone.utc)
            return

        if "status = case when attempt + 1" in q and "status = 'running'" in q:
            err, ma, ma2, ma3, backoff, jid = p
            ma = int(ma)
            r = self.rows.get(int(jid))
            if not r or r["status"] != "running":
                return
            r["attempt"] = int(r["attempt"]) + 1
            r["error"] = (err or "")[:1000]
            if r["attempt"] < ma:
                r["status"] = "pending"
                r["finished_at"] = None
                r["run_after"] = datetime.now(timezone.utc) + timedelta(minutes=int(backoff))
            else:
                r["status"] = "dead"
                r["finished_at"] = datetime.now(timezone.utc)
            r["worker_id"] = None
            r["started_at"] = None
            return

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        q = _norm(query)
        p = params or ()
        now = datetime.now(timezone.utc)
        if "returning id" in q and "status = 'pending'" in q and "attempt + 1" in q:
            stuck_s, ma = int(p[0]), int(p[1])
            out = []
            for r in list(self.rows.values()):
                if r["status"] != "running":
                    continue
                st = r["started_at"]
                if st is None:
                    continue
                if st.tzinfo is None:
                    st = st.replace(tzinfo=timezone.utc)
                if r["attempt"] >= ma:
                    continue
                if st > now - timedelta(seconds=stuck_s):
                    continue
                r["status"] = "pending"
                r["worker_id"] = None
                r["started_at"] = None
                r["attempt"] = int(r["attempt"]) + 1
                out.append({"id": r["id"]})
            return out
        if "returning id" in q and "status = 'dead'" in q:
            stuck_s, ma = int(p[0]), int(p[1])
            out = []
            for r in list(self.rows.values()):
                if r["status"] != "running":
                    continue
                st = r["started_at"]
                if st is None:
                    continue
                if st.tzinfo is None:
                    st = st.replace(tzinfo=timezone.utc)
                if r["attempt"] < ma:
                    continue
                if st > now - timedelta(seconds=stuck_s):
                    continue
                r["status"] = "dead"
                r["finished_at"] = now
                out.append({"id": r["id"]})
            return out
        return []


@pytest.fixture(autouse=True)
def _fake_batch_store(monkeypatch):
    store = _FakeBatchQueueStore()
    monkeypatch.setitem(_config._instances, "metadata_store", store)
    yield store


def test_register_batch_handler_rejects_invalid_kind_name() -> None:
    class P(BaseModel):
        n: int = 1

    with pytest.raises(ValueError):
        batch_queue.register_batch_handler("Bad-Kind", P)(lambda **_: None)


def test_enqueue_inserts_row_with_pending_status(_fake_batch_store) -> None:
    class P(BaseModel):
        pass

    @batch_queue.register_batch_handler("noop", P)
    def _h(*, user_id: str, payload: P) -> None:
        pass

    jid = batch_queue.enqueue(user_id="alice", kind="noop", payload={})
    row = _fake_batch_store.rows[jid]
    assert row["status"] == "pending"
    assert row["attempt"] == 0
    assert row["worker_id"] is None
    assert row["started_at"] is None


def test_enqueue_unknown_kind_raises_value_error() -> None:
    with pytest.raises(ValueError):
        batch_queue.enqueue(user_id="alice", kind="ghost", payload={})


def test_claim_next_respects_per_user_max_concurrent(monkeypatch, _fake_batch_store) -> None:
    monkeypatch.setattr(batch_queue, "BATCH_QUEUE_PER_USER_MAX_CONCURRENT", 1)

    class P(BaseModel):
        pass

    @batch_queue.register_batch_handler("noop", P)
    def _h(*, user_id: str, payload: P) -> None:
        pass

    batch_queue.enqueue(user_id="alice", kind="noop", payload={})
    batch_queue.enqueue(user_id="alice", kind="noop", payload={})
    j1 = batch_queue.claim_next("w1")
    assert j1 is not None
    j2 = batch_queue.claim_next("w1")
    assert j2 is None
    batch_queue.complete(j1.id)
    j3 = batch_queue.claim_next("w1")
    assert j3 is not None


def test_run_one_tick_dispatches_to_handler(_fake_batch_store) -> None:
    seen: list[tuple[str, int]] = []

    class P(BaseModel):
        n: int = 0

    @batch_queue.register_batch_handler("rec", P)
    def _h(*, user_id: str, payload: P) -> None:
        seen.append((user_id, payload.n))

    batch_queue.enqueue(user_id="bob", kind="rec", payload={"n": 7})
    assert batch_queue._run_one_tick("worker-x") is True
    assert seen == [("bob", 7)]
    assert _fake_batch_store.rows[1]["status"] == "done"
