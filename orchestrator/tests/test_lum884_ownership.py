# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-884 — per-user ownership for ingest paths, chat file tools and /browse.

Two-member regression tests: member B must not reach member A's files, names
or graph through ``POST /ingest``, ``read_file`` / the filename fallback,
``GET /browse`` or the tool-handler contract.

``FileIndexStore`` is a visibility-aware fake. The conftest ``MockMetadataStore``
returns ``None`` for every ``file_index`` query, so a ``read_file`` that skipped
the visibility check would still look "denied" and pass. This fake **asserts**
the SQL carries exactly the clause ``visible_filter`` builds for the caller, then
filters rows with a Python model of that clause. It cannot execute SQL, so the
statement itself is pinned by fragment assertions and proven once against a real
Postgres at ``/verify-plan`` (``regexp_replace`` + ``ESCAPE '!'``; matrix row
1.8.19 shows a literal-``%`` bug only a real driver exposes). No Postgres-backed
unit fixture exists; an automated real-PG test stays optional-nightly.
"""

from __future__ import annotations

import builtins
import importlib
import json
from pathlib import Path

import pytest
from auth import UserContext
from fastapi import FastAPI
from fastapi.testclient import TestClient
from visibility import visible_filter

import config

A = "user-a"  # library owner / member A
B = "user-b"  # second household member
ADMIN = "admin-c"  # a third, non-owner admin


def _norm(sql: str) -> str:
    return " ".join(sql.split())


class FileIndexStore:
    """Visibility-aware fake metadata store (see module docstring)."""

    def __init__(self, allows_shared: dict[str, bool] | None = None) -> None:
        self.rows: list[dict] = []  # {file_path, user_id, scope}
        self.allows_shared = {A: True, B: True, ADMIN: True, **(allows_shared or {})}
        self.queries: list[tuple[str, tuple]] = []
        self.raise_on_file_index: Exception | None = None

    def add(self, file_path: str | Path, user_id: str, scope: str = "personal") -> None:
        self.rows.append({"file_path": str(file_path), "user_id": user_id, "scope": scope})

    # -- MetadataStore surface used by the code under test -----------------
    def ping(self) -> bool:
        return True

    def execute(self, query: str, params: tuple | None = None) -> None:
        pass

    def close(self) -> None:
        pass

    def _caller(self, query: str, params: tuple) -> str:
        q = _norm(query)
        for uid in self.allows_shared:
            clause, _ = visible_filter(UserContext(user_id=uid))
            if _norm(clause) in q and uid in params:
                return uid
        raise AssertionError(
            f"file_index query without the caller's visible_filter clause: {query!r} {params!r}"
        )

    def _visible(self, row: dict, me: str) -> bool:
        return (
            (row["scope"] == "personal" and row["user_id"] == me)
            or row["scope"] == "system"
            or (row["scope"] == "shared" and self.allows_shared.get(me, True))
        )

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        q = query.lower()
        params = tuple(params or ())
        if "allows_shared" in q and "from users" in q:
            return {"allows_shared": self.allows_shared.get(params[0], True)}
        if "from file_index" in q:
            self.queries.append((query, params))
            if self.raise_on_file_index:
                raise self.raise_on_file_index
            me = self._caller(query, params)
            wanted = params[0]
            for r in self.rows:
                if r["file_path"] == wanted and self._visible(r, me):
                    return {"ok": 1}
            return None
        return None

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        q = query.lower()
        params = tuple(params or ())
        if "from file_index" in q:
            self.queries.append((query, params))
            if self.raise_on_file_index:
                raise self.raise_on_file_index
            me = self._caller(query, params)
            pattern, limit = params[-2], params[-1]
            # Undo the "!" escaping the code applies to LIKE metacharacters.
            needle = _unescape(pattern[1:-1]).lower()
            out = sorted(
                {
                    r["file_path"]
                    for r in self.rows
                    if self._visible(r, me) and needle in Path(r["file_path"]).name.lower()
                }
            )
            return [{"file_path": p} for p in out[:limit]]
        return []


def _unescape(escaped: str) -> str:
    out, i = [], 0
    while i < len(escaped):
        if escaped[i] == "!" and i + 1 < len(escaped):
            i += 1
        out.append(escaped[i])
        i += 1
    return "".join(out)


@pytest.fixture
def store() -> FileIndexStore:
    s = FileIndexStore()
    config._instances["metadata_store"] = s
    return s


@pytest.fixture
def library(monkeypatch, tmp_path) -> Path:
    lib = tmp_path / "lib"
    lib.mkdir()
    monkeypatch.setenv("INGEST_PATHS", json.dumps([str(lib)]))
    monkeypatch.delenv("FILESYSTEM_ROOT", raising=False)
    return lib


def _write(lib: Path, name: str, text: str = "content") -> Path:
    p = lib / name
    p.write_text(text, encoding="utf-8")
    return p.resolve()


# ---------------------------------------------------------------------------
# DATA-001 / AI-001: read_file and the filename fallback follow search visibility
# ---------------------------------------------------------------------------


def _read(path, user_id) -> dict:
    from services.tools import _read_file

    return json.loads(_read_file({"path": str(path)}, user_id=user_id))


def test_read_file_owner_reads_own_indexed_file(store, library):
    f = _write(library, "a.txt", "hello A")
    store.add(f, A)
    assert _read(f, A)["content"] == "hello A"


def test_read_file_shared_document_readable_by_member(store, library):
    f = _write(library, "shared.txt", "for the house")
    store.add(f, A)
    store.add(f, A, scope="shared")  # projection row keeps the source file_path
    assert _read(f, B)["content"] == "for the house"


def test_read_file_member_cannot_read_owner_personal_file(store, library):
    """Headline DATA-001."""
    f = _write(library, "private.txt", "A's secret")
    store.add(f, A)
    out = _read(f, B)
    assert out["error"] == "Access denied: file is not in your library"
    assert "content" not in out


def test_read_file_admin_cannot_read_member_personal_file(store, library):
    """No admin exception: admins get what search gives them."""
    f = _write(library, "private.txt", "A's secret")
    store.add(f, A)
    out = _read(f, ADMIN)
    assert out["error"] == "Access denied: file is not in your library"
    assert "content" not in out


def test_read_file_unindexed_file_denied_even_for_owner(store, library):
    f = _write(library, "unindexed.txt", "no row")
    out = _read(f, A)
    assert out["error"] == "Access denied: file is not in your library"


def test_read_file_same_error_for_missing_and_hidden(store, library):
    hidden = _write(library, "hidden.txt")
    store.add(hidden, A)
    missing = library / "missing.txt"
    assert _read(hidden, B)["error"] == _read(missing, B)["error"]


def test_read_file_member_without_shared_access_denied_shared_doc(store, library):
    store.allows_shared[B] = False
    f = _write(library, "shared.txt")
    store.add(f, A, scope="shared")
    assert "error" in _read(f, B)
    assert "content" not in _read(f, B)


def test_read_file_fails_closed_on_lookup_error(store, library, monkeypatch):
    f = _write(library, "a.txt", "do not leak")
    store.add(f, A)
    store.raise_on_file_index = RuntimeError("db down")
    real_open = builtins.open
    opened: list[str] = []

    def spy(file, *args, **kwargs):
        if str(file) == str(f):
            opened.append(str(file))
            raise AssertionError("file must not be opened when the lookup fails")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy)
    out = _read(f, A)
    assert out["error"] == "File lookup failed"
    assert "do not leak" not in json.dumps(out)
    assert opened == []


def test_read_file_symlink_follows_resolved_row(store, library, tmp_path):
    """A's row for the link's literal path must not unlock B's file."""
    b_file = _write(library, "b-private.txt", "B's content")
    store.add(b_file, B)
    link = library / "link.txt"
    link.symlink_to(b_file)
    store.add(link, A)  # literal link path; ingest would store the resolved path
    out = _read(link, A)
    assert out["error"] == "Access denied: file is not in your library"


def test_read_file_owner_reads_through_symlinked_root(store, monkeypatch, tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    root_link = tmp_path / "lib"
    root_link.symlink_to(real, target_is_directory=True)
    monkeypatch.setenv("INGEST_PATHS", json.dumps([str(root_link)]))
    monkeypatch.delenv("FILESYSTEM_ROOT", raising=False)
    f = real / "doc.txt"
    f.write_text("via symlinked root", encoding="utf-8")
    store.add(f.resolve(), A)  # what ingest writes: the resolved path
    assert _read(root_link / "doc.txt", A)["content"] == "via symlinked root"


def test_fuzzy_filename_search_returns_only_visible_names(store):
    from services.search import fuzzy_filename_search

    store.add("/lib/report-a.pdf", A)
    store.add("/lib/report-s.pdf", A, scope="shared")
    store.add("/lib/report-s.pdf", A)  # A's own copy of the shared file: no duplicate path
    b_paths = [h["path"] for h in fuzzy_filename_search("report", user_id=B)]
    a_paths = [h["path"] for h in fuzzy_filename_search("report", user_id=A)]
    assert b_paths == ["/lib/report-s.pdf"]
    assert a_paths == ["/lib/report-a.pdf", "/lib/report-s.pdf"]


def test_fuzzy_filename_search_escapes_like_wildcards(store):
    from services.search import fuzzy_filename_search

    fuzzy_filename_search("50%_off!", user_id=A)
    fuzzy_filename_search("a", user_id=A)
    assert store.queries[0][1][-2] == "%50!%!_off!!%"
    assert store.queries[1][1][-2] == "%a%"


def test_fuzzy_filename_search_sql_shape(store):
    from services.search import fuzzy_filename_search

    fuzzy_filename_search("report", 7, user_id=B)
    sql, params = store.queries[0]
    clause, cparams = visible_filter(UserContext(user_id=B))
    n = _norm(sql)
    assert "regexp_replace(file_path, '^.*/', '') ILIKE %s ESCAPE '!'" in n
    assert "SELECT DISTINCT file_path" in n
    assert "ORDER BY file_path" in n
    assert "LIMIT %s" in n
    assert _norm(clause) in n
    assert params == (*cparams, "%report%", 7)


def test_fuzzy_filename_search_empty_query_returns_nothing(store):
    from services.search import fuzzy_filename_search

    assert fuzzy_filename_search("   ", user_id=A) == []
    assert store.queries == []  # no ILIKE '%%' table scan


def test_semantic_search_fallback_is_user_scoped(store, monkeypatch):
    from services.search import semantic_search

    store.add("/lib/report-a.pdf", A)
    store.add("/lib/report-s.pdf", A, scope="shared")
    monkeypatch.setattr(config.get_vector_store(), "search", lambda *a, **k: [], raising=False)
    paths = [r.file_path for r in semantic_search("report", user_id=B)]
    assert "/lib/report-a.pdf" not in paths
    assert paths == ["/lib/report-s.pdf"]


def test_search_files_exception_fallback_is_user_scoped(store, monkeypatch):
    import services.search as search_mod
    from services.tools import _search_files

    store.add("/lib/report-a.pdf", A)
    store.add("/lib/report-s.pdf", A, scope="shared")

    def boom(*a, **k):
        raise RuntimeError("vector store down")

    monkeypatch.setattr(search_mod, "semantic_search", boom)
    out = json.loads(_search_files({"query": "report"}, user_id=B))
    assert [h["path"] for h in out["results"]] == ["/lib/report-s.pdf"]


def test_fallback_search_degrades_when_store_fails(store, monkeypatch):
    from services.search import semantic_search
    from services.tools import _fallback_search

    store.raise_on_file_index = RuntimeError("db down")
    out = json.loads(_fallback_search("x", user_id=B))
    assert out["count"] == 0
    assert "error" in out
    monkeypatch.setattr(config.get_vector_store(), "search", lambda *a, **k: [], raising=False)
    assert semantic_search("x", user_id=B) == []


# ---------------------------------------------------------------------------
# ID-004 / ID-005: route gates and the Rescan contract
# ---------------------------------------------------------------------------


def _app() -> FastAPI:
    from routes import admin as admin_routes
    from routes import data as data_routes

    app = FastAPI()

    @app.middleware("http")
    async def _who(request, call_next):  # test stand-in for auth_middleware
        raw = request.headers.get("x-test-user")
        if raw:
            uid, role = raw.split(":")
            request.state.user = UserContext(user_id=uid, is_authenticated=True, role=role)
        return await call_next(request)

    app.include_router(data_routes.router)
    app.include_router(admin_routes.router)
    return app


MEMBER = {"x-test-user": f"{B}:user"}
ADMIN_H = {"x-test-user": f"{ADMIN}:admin"}


@pytest.fixture
def enqueued(monkeypatch) -> list[dict]:
    """Recorder replacing the conftest batch-queue stub; no in-flight jobs."""
    calls: list[dict] = []
    monkeypatch.setattr("services.batch_queue.enqueue", lambda **kw: calls.append(kw) or len(calls))
    monkeypatch.setattr("services.ingest._inflight_ingest_folder_paths", lambda owner: set())
    return calls


@pytest.fixture
def no_owner(monkeypatch):
    monkeypatch.delenv("INGEST_PATHS_OWNER_USER_ID", raising=False)
    monkeypatch.delenv("INBOX_OWNER_USER_ID", raising=False)


def test_ingest_rejects_member(monkeypatch, library, enqueued):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    r = TestClient(_app()).post("/ingest", headers=MEMBER)
    assert r.status_code == 403
    assert enqueued == []


def test_ingest_member_with_bad_body_gets_403(monkeypatch, library, enqueued):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    r = TestClient(_app()).post("/ingest", headers=MEMBER, json={"path": "/workspace/uploads/A"})
    assert r.status_code == 403
    assert enqueued == []


def test_ingest_rejects_path_field(monkeypatch, library, enqueued):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    r = TestClient(_app()).post("/ingest", headers=ADMIN_H, json={"path": "/workspace/uploads/B"})
    assert r.status_code == 422
    assert enqueued == []


def test_ingest_rejects_user_id_field(monkeypatch, library, enqueued):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    r = TestClient(_app()).post("/ingest", headers=ADMIN_H, json={"user_id": B})
    assert r.status_code == 422
    assert enqueued == []


@pytest.mark.parametrize("kwargs", [{}, {"json": {}}])
def test_ingest_accepts_empty_body(monkeypatch, library, enqueued, no_owner, kwargs):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    r = TestClient(_app()).post("/ingest", headers=ADMIN_H, **kwargs)
    assert r.status_code == 200, r.text


def test_rescan_enqueues_each_existing_library_root_for_owner(monkeypatch, tmp_path, enqueued):
    present = tmp_path / "present"
    present.mkdir()
    absent = tmp_path / "absent"
    monkeypatch.setenv("INGEST_PATHS", json.dumps([str(present), str(absent)]))
    monkeypatch.setenv("INGEST_PATHS_OWNER_USER_ID", A)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    r = TestClient(_app()).post("/ingest", headers=ADMIN_H)
    assert r.status_code == 200
    body = r.json()
    resolved = str(present.resolve())
    assert body == {
        "status": "rescan queued",
        "roots": [resolved],
        "already_queued": [],
        "user_id": A,
    }
    assert enqueued == [{"user_id": A, "kind": "ingest_folder", "payload": {"path": resolved}}]


def test_rescan_without_owner_files_under_calling_admin(monkeypatch, library, enqueued, no_owner):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    r = TestClient(_app()).post("/ingest", headers=ADMIN_H)
    assert r.status_code == 200
    assert r.json()["user_id"] == ADMIN
    assert [c["user_id"] for c in enqueued] == [ADMIN]


def test_rescan_single_user_dev_mode(monkeypatch, library, enqueued, no_owner):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    r = TestClient(_app()).post("/ingest")
    assert r.status_code == 200
    assert r.json()["user_id"] == "default"


def test_rescan_coalesces_pending_scan(monkeypatch, library, enqueued, no_owner):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    resolved = str(library.resolve())
    monkeypatch.setattr("services.ingest._inflight_ingest_folder_paths", lambda owner: {resolved})
    body = TestClient(_app()).post("/ingest").json()
    assert body["roots"] == []
    assert body["already_queued"] == [resolved]
    assert enqueued == []


def test_inflight_query_is_scoped_to_owner(store):
    from services.ingest import _inflight_ingest_folder_paths

    captured: list[tuple] = []

    class _Capture:
        def fetch_all(self, query, params=None):
            captured.append((query, params))
            return [{"path": "/lib"}, {"path": None}]

    config._instances["metadata_store"] = _Capture()
    assert _inflight_ingest_folder_paths(A) == {"/lib"}
    sql = _norm(captured[0][0])
    assert "kind = 'ingest_folder'" in sql
    assert "user_id = %s" in sql
    assert "status IN ('pending','running')" in sql
    assert captured[0][1] == (A,)


def test_rescan_refuses_when_no_library_configured(monkeypatch, tmp_path, enqueued, no_owner):
    """$HOME is the read-side fallback; it must never become a library."""
    monkeypatch.delenv("INGEST_PATHS", raising=False)
    monkeypatch.delenv("FILESYSTEM_ROOT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert config.get_effective_ingest_paths() == [str(Path.home())]
    monkeypatch.setenv("AUTH_ENABLED", "false")
    r = TestClient(_app()).post("/ingest")
    assert r.status_code == 409
    assert r.json()["detail"] == "no_library_folders"
    assert enqueued == []


def test_rescan_refuses_when_no_configured_folder_exists(monkeypatch, tmp_path, enqueued):
    monkeypatch.setenv("INGEST_PATHS", json.dumps([str(tmp_path / "gone")]))
    monkeypatch.setenv("AUTH_ENABLED", "false")
    r = TestClient(_app()).post("/ingest")
    assert r.status_code == 409
    assert enqueued == []


def test_browse_requires_admin(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    client = TestClient(_app())
    assert client.get("/browse", params={"path": "/home"}, headers=MEMBER).status_code == 403
    assert client.get("/browse", headers=ADMIN_H).status_code == 200


# ---------------------------------------------------------------------------
# ID-005 defence in depth: the ingest_folder batch handler
# ---------------------------------------------------------------------------

_HANDLER_MODULE = "services.batch_handlers.ingest_folder"


@pytest.fixture
def folder_handler(monkeypatch):
    mod = importlib.import_module(_HANDLER_MODULE)
    calls: list[tuple] = []
    monkeypatch.setattr(
        mod, "ingest_folder", lambda path, *, user_id: calls.append((path, user_id))
    )
    return mod, calls


def _run(mod, path: Path | str, user_id: str) -> None:
    mod.handle(user_id=user_id, payload=mod.IngestFolderPayload(path=str(path)), job_id=1)


def test_ingest_folder_handler_accepts_library_path(folder_handler, library, no_owner):
    mod, calls = folder_handler
    _run(mod, library / "sub", A)
    assert calls == [(str(library / "sub"), A)]


def test_ingest_folder_handler_rejects_path_outside_library(folder_handler, library, tmp_path):
    mod, calls = folder_handler
    with pytest.raises(mod.IngestFolderRejected):
        _run(mod, tmp_path / "elsewhere", A)
    assert calls == []


def test_ingest_folder_handler_rejects_home_fallback(folder_handler, monkeypatch, tmp_path):
    mod, calls = folder_handler
    monkeypatch.delenv("INGEST_PATHS", raising=False)
    monkeypatch.delenv("FILESYSTEM_ROOT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(mod.IngestFolderRejected):
        _run(mod, Path.home(), A)
    assert calls == []


def test_ingest_folder_handler_rejects_non_owner_user(folder_handler, library, monkeypatch):
    mod, calls = folder_handler
    monkeypatch.setenv("INGEST_PATHS_OWNER_USER_ID", A)
    with pytest.raises(mod.IngestFolderRejected):
        _run(mod, library, B)
    assert calls == []
    _run(mod, library, A)  # the owner is still accepted
    assert calls == [(str(library), A)]


def test_ingest_folder_handler_rejects_prefix_sibling(folder_handler, monkeypatch, tmp_path):
    mod, calls = folder_handler
    root = tmp_path / "data"
    root.mkdir()
    (tmp_path / "data-evil").mkdir()
    monkeypatch.setenv("INGEST_PATHS", json.dumps([str(root)]))
    with pytest.raises(mod.IngestFolderRejected):
        _run(mod, tmp_path / "data-evil", A)
    assert calls == []


# ---------------------------------------------------------------------------
# AI-002: no one-argument handler fallback
# ---------------------------------------------------------------------------


def test_run_tool_no_longer_falls_back_to_single_arg_handler(monkeypatch):
    import services.tools as tools
    from models.tool_spec import ToolSpec

    called: list[dict] = []

    def one_arg(input_):
        called.append(input_)
        return "{}"

    spec = ToolSpec(
        name="lum884_one_arg",
        connector="filesystem-mcp",
        action_type="search_files",
        is_write=False,
        definition={"name": "lum884_one_arg"},
        handler=one_arg,
    )
    monkeypatch.setattr(tools, "TOOL_SPECS", [*tools.TOOL_SPECS, spec])
    monkeypatch.setattr(tools, "_check_permission", lambda *a, **k: True)
    with pytest.raises(TypeError):
        tools.run_tool("lum884_one_arg", {}, user_id=B)
    assert called == []
