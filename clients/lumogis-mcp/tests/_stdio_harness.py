# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Shared subprocess stdio harness for lumogis-mcp tests (LUM-292 / LUM-299)."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time

import uvicorn

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ORCHESTRATOR = REPO_ROOT / "orchestrator"


class StdioTokenStore:
    """Minimal mcp_tokens metadata store for stdio bridge tests (no orchestrator.tests import)."""

    def __init__(self) -> None:
        self.tokens: dict[str, dict] = {}

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        pass

    @staticmethod
    def _norm(query: str) -> str:
        return " ".join(query.split()).lower()

    def transaction(self):
        from contextlib import contextmanager

        @contextmanager
        def _noop():
            yield

        return _noop()

    def execute(self, query: str, params: tuple | None = None) -> None:
        from datetime import datetime
        from datetime import timezone

        q = self._norm(query)
        p = params or ()
        if q.startswith("insert into mcp_tokens"):
            token_id, user_id, token_prefix, token_hash, label, scopes = p
            self.tokens[token_id] = {
                "id": token_id,
                "user_id": user_id,
                "token_prefix": token_prefix,
                "token_hash": token_hash,
                "label": label,
                "scopes": scopes,
                "created_at": datetime.now(timezone.utc),
                "last_used_at": None,
                "expires_at": None,
                "revoked_at": None,
            }
            return
        if q.startswith("update mcp_tokens set last_used_at = now() where id = %s"):
            (tid,) = p
            row = self.tokens.get(tid)
            if row is not None:
                row["last_used_at"] = datetime.now(timezone.utc)

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        q = self._norm(query)
        p = params or ()
        if "count" in q:
            return {"n": 1}
        if q.startswith("select * from mcp_tokens where token_prefix = %s and revoked_at is null"):
            (prefix,) = p
            for row in self.tokens.values():
                if row["token_prefix"] == prefix and row["revoked_at"] is None:
                    return dict(row)
        if q.startswith("select * from mcp_tokens where id = %s"):
            (tid,) = p
            row = self.tokens.get(tid)
            return dict(row) if row else None
        return None

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        return []


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_uvicorn(app, port: int) -> uvicorn.Server:
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)

    def _run() -> None:
        server.run()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    for _ in range(1200):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return server
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"uvicorn did not start on port {port}")


def stop_uvicorn(server: uvicorn.Server) -> None:
    server.should_exit = True
    time.sleep(0.2)


def stub_orchestrator_lifespan_for_stdio(monkeypatch) -> None:
    """Mirror orchestrator ``tests/conftest.py`` lifespan stubs for in-process uvicorn."""
    monkeypatch.setattr("db_default_user_remap.main", lambda: 0)
    monkeypatch.setattr("services.ingest.enqueue_initial_ingest_scan", lambda: False)
    monkeypatch.setattr("services.batch_queue.enqueue", lambda **_kwargs: 1)
    monkeypatch.setattr("services.batch_queue.reset_stuck", lambda **_kwargs: 0)
    monkeypatch.setattr("services.ingest.start_watcher", lambda *args, **kwargs: None)
    monkeypatch.setattr("services.ingest.stop_watcher", lambda: None)
    monkeypatch.setattr("services.ingest.start_ingest_path_watchers", lambda *args, **kwargs: None)
    monkeypatch.setattr("services.ingest.stop_ingest_path_watchers", lambda: None)
    monkeypatch.setattr("services.ingest.schedule_inbox_poll", lambda: None)
    monkeypatch.setattr("services.ingest.unschedule_inbox_poll", lambda: None)

    def _fast_embedding(state) -> bool:
        state.embedding_ready = True
        return True

    monkeypatch.setattr("services.embedding_readiness.try_activate_embedding", _fast_embedding)


def mcp_stdio_roundtrip(env: dict, messages: list[dict], timeout: float = 30.0) -> list[dict]:
    """Run ``lumogis-mcp`` and exchange newline-delimited JSON-RPC messages."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "lumogis_mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        bufsize=1,
    )
    assert proc.stdin is not None and proc.stdout is not None
    responses: list[dict] = []
    try:
        for msg in messages:
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()
            if "method" in msg and not msg["method"].startswith("notifications/"):
                line = proc.stdout.readline()
                if not line:
                    stderr = proc.stderr.read() if proc.stderr else ""
                    raise RuntimeError(f"bridge exited early: {stderr}")
                responses.append(json.loads(line))
        return responses
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
