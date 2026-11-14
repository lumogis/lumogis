# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Route-level tests for the LUM-695 attachment surface, through TestClient.

Asserted at the route layer because these are route-layer contracts: the header
pair on every download, the status matrix, and the ownership gate that must
refuse **before** any IMAP connection is opened.

Two splits are load-bearing and are pinned here:

* **503 (mailbox unreachable) vs 410 (message gone)** — one is worth retrying,
  the other never will be. Collapsing them would make fetch-on-demand's stated
  cost read as a bug.
* **``available: false`` vs an empty list** — a metadata-mode mailbox never
  fetched bodies, so it cannot know. An empty list would be an outright lie
  about a message that has three attachments.
"""

from __future__ import annotations

import io

import pytest
from actions.errors import PermanentActionError
from adapters.email_imap_source import AttachmentFetchError
from fastapi.testclient import TestClient

import config
from services import email_attachment_staging as staging


@pytest.fixture(autouse=True)
def _auth_disabled(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")


@pytest.fixture(autouse=True)
def _reset_buckets():
    from routes.api_v1.email_attachments import _download_calls
    from routes.api_v1.email_attachments import _stage_calls

    for bucket in (_stage_calls, _download_calls):
        bucket.clear()
    yield
    for bucket in (_stage_calls, _download_calls):
        bucket.clear()


@pytest.fixture
def client():
    import main

    with TestClient(main.app) as c:
        yield c


class _Source:
    def __init__(self, extraction_method: str = "imap_full_ingest") -> None:
        self.id = "src-1"
        self.user_id = "default"
        self.extraction_method = extraction_method


class _Store:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = rows if rows is not None else []

    def fetch_all(self, sql, params=None):
        if "FROM email_attachment " in sql:
            return list(self.rows)
        return []

    def fetch_one(self, sql, params=None):
        if "FROM email_attachment " in sql:
            wanted = params[2]
            for r in self.rows:
                if r["part_index"] == wanted:
                    return dict(r)
            return None
        return None

    def execute(self, sql, params=None):
        return None


def _own(monkeypatch, source: _Source | None = None):
    """Make every signal owned by the caller, resolving to ``source``."""
    src = source or _Source()
    wb = "actions.handlers.imap_writeback"
    monkeypatch.setattr(f"{wb}._signal_url", lambda sid: "imap://src-1/100/42")
    monkeypatch.setattr(f"{wb}._parse_imap_url", lambda url: ("src-1", 100, 42))
    monkeypatch.setattr(f"{wb}._load_owned_source", lambda sid, uid: src)
    return src


def _disown(monkeypatch):
    wb = "actions.handlers.imap_writeback"
    monkeypatch.setattr(f"{wb}._signal_url", lambda sid: "imap://src-1/100/42")
    monkeypatch.setattr(f"{wb}._parse_imap_url", lambda url: ("src-1", 100, 42))

    def _deny(sid, uid):
        raise PermanentActionError("not_found", "not owned")

    monkeypatch.setattr("actions.handlers.imap_writeback._load_owned_source", _deny)


def _row(part_index: int = 0, filename: str = "invoice.pdf", size: int = 1234) -> dict:
    return {
        "part_index": part_index,
        "filename": filename,
        "mime_type": "application/pdf",
        "size_bytes": size,
    }


class _FakeAdapter:
    """Stands in for EmailImapSource at the module seam the route imports from."""

    instances: list["_FakeAdapter"] = []

    def __init__(self, source, **kw):
        self.calls: list[dict] = []
        _FakeAdapter.instances.append(self)

    result: object = None
    error: AttachmentFetchError | None = None

    def fetch_attachment_part(self, **kw):
        self.calls.append(kw)
        if _FakeAdapter.error is not None:
            raise _FakeAdapter.error
        return _FakeAdapter.result


@pytest.fixture(autouse=True)
def _fake_adapter(monkeypatch):
    _FakeAdapter.instances = []
    _FakeAdapter.error = None
    from services import email_attachments

    _FakeAdapter.result = email_attachments.ResolvedPart(
        filename="invoice.pdf", mime_type="application/pdf", data=b"PDFBYTES", part_index=0
    )
    monkeypatch.setattr("adapters.email_imap_source.EmailImapSource", _FakeAdapter)
    return _FakeAdapter


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


def test_list_returns_the_recorded_rows(client, monkeypatch):
    _own(monkeypatch)
    monkeypatch.setattr(
        config, "get_metadata_store", lambda: _Store([_row(0), _row(1, "photo.png")])
    )

    r = client.get("/api/v1/email/signals/sig-1/attachments")

    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert [row["filename"] for row in body["rows"]] == ["invoice.pdf", "photo.png"]


def test_metadata_mode_reports_unavailable_not_empty(client, monkeypatch):
    """ "No attachments" and "cannot know" must be distinguishable."""
    _own(monkeypatch, _Source(extraction_method="imap_metadata"))
    monkeypatch.setattr(config, "get_metadata_store", lambda: _Store([]))

    r = client.get("/api/v1/email/signals/sig-1/attachments")

    assert r.status_code == 200
    assert r.json() == {"rows": [], "available": False}


def test_full_ingest_message_with_no_attachments_is_available_and_empty(client, monkeypatch):
    _own(monkeypatch)
    monkeypatch.setattr(config, "get_metadata_store", lambda: _Store([]))

    assert client.get("/api/v1/email/signals/sig-1/attachments").json() == {
        "rows": [],
        "available": True,
    }


def test_list_404_for_non_owned_signal(client, monkeypatch):
    _disown(monkeypatch)

    assert client.get("/api/v1/email/signals/sig-1/attachments").status_code == 404


# ---------------------------------------------------------------------------
# Download — headers and the status matrix
# ---------------------------------------------------------------------------


def test_download_returns_the_bytes(client, monkeypatch):
    _own(monkeypatch)
    monkeypatch.setattr(config, "get_metadata_store", lambda: _Store([_row()]))

    r = client.get("/api/v1/email/signals/sig-1/attachments/0")

    assert r.status_code == 200
    assert r.content == b"PDFBYTES"


def test_download_always_sets_content_disposition_attachment(client, monkeypatch):
    """Never inline — attacker-supplied text/html inline is stored XSS on our origin."""
    _own(monkeypatch)
    monkeypatch.setattr(config, "get_metadata_store", lambda: _Store([_row()]))

    r = client.get("/api/v1/email/signals/sig-1/attachments/0")

    assert r.headers["content-disposition"].startswith("attachment;")
    assert "invoice.pdf" in r.headers["content-disposition"]


def test_download_sets_nosniff(client, monkeypatch):
    _own(monkeypatch)
    monkeypatch.setattr(config, "get_metadata_store", lambda: _Store([_row()]))

    r = client.get("/api/v1/email/signals/sig-1/attachments/0")

    assert r.headers["x-content-type-options"] == "nosniff"


def test_download_404_for_non_owned_signal_without_touching_imap(client, monkeypatch):
    """The ownership gate must refuse BEFORE any IMAP connection is opened."""
    _disown(monkeypatch)
    monkeypatch.setattr(config, "get_metadata_store", lambda: _Store([_row()]))

    r = client.get("/api/v1/email/signals/sig-1/attachments/0")

    assert r.status_code == 404
    assert _FakeAdapter.instances == [], "no IMAP adapter may be constructed for a foreign signal"


def test_download_refuses_blocked_extension(client, monkeypatch):
    _own(monkeypatch)
    monkeypatch.setattr(config, "get_metadata_store", lambda: _Store([_row(filename="setup.exe")]))

    r = client.get("/api/v1/email/signals/sig-1/attachments/0")

    assert r.status_code == 415
    assert _FakeAdapter.instances == [], "a blocked type must be refused before any fetch"


def test_download_refuses_oversize_from_recorded_metadata(client, monkeypatch):
    """413 without streaming — the size is already known from ingest."""
    _own(monkeypatch)
    monkeypatch.setattr(
        config, "get_metadata_store", lambda: _Store([_row(size=200 * 1024 * 1024)])
    )

    r = client.get("/api/v1/email/signals/sig-1/attachments/0")

    assert r.status_code == 413
    assert _FakeAdapter.instances == [], "an oversize attachment must never be fetched"


def test_download_404_for_an_unrecorded_part_index(client, monkeypatch):
    _own(monkeypatch)
    monkeypatch.setattr(config, "get_metadata_store", lambda: _Store([_row(0)]))

    assert client.get("/api/v1/email/signals/sig-1/attachments/7").status_code == 404


def test_imap_unreachable_is_503_and_message_gone_is_410(client, monkeypatch):
    """Distinct codes: one is retryable, the other never is."""
    _own(monkeypatch)
    monkeypatch.setattr(config, "get_metadata_store", lambda: _Store([_row()]))

    _FakeAdapter.error = AttachmentFetchError("imap_unreachable", "down")
    assert client.get("/api/v1/email/signals/sig-1/attachments/0").status_code == 503

    _FakeAdapter.error = AttachmentFetchError("message_gone", "gone")
    assert client.get("/api/v1/email/signals/sig-1/attachments/0").status_code == 410


def test_whole_message_too_large_is_413(client, monkeypatch):
    _own(monkeypatch)
    monkeypatch.setattr(config, "get_metadata_store", lambda: _Store([_row()]))
    _FakeAdapter.error = AttachmentFetchError("message_too_large", "huge")

    assert client.get("/api/v1/email/signals/sig-1/attachments/0").status_code == 413


# ---------------------------------------------------------------------------
# Staging routes
# ---------------------------------------------------------------------------


@pytest.fixture
def staged(monkeypatch, tmp_path):
    """A real staging service over a tmp root and an in-memory index."""
    rows: list[dict] = []

    class _StagingStore:
        def fetch_one(self, sql, params=None):
            if "SELECT COALESCE(SUM(size_bytes)" in sql:
                return {"total": sum(r["size_bytes"] for r in rows if r["user_id"] == params[0])}
            if "SELECT id, filename, mime_type, storage_key" in sql:
                for r in rows:
                    if r["id"] == params[0] and r["user_id"] == params[1]:
                        return dict(r)
            return None

        def fetch_all(self, sql, params=None):
            if "WHERE id::text = ANY(%s) AND user_id = %s" in sql:
                return [dict(r) for r in rows if r["id"] in params[0] and r["user_id"] == params[1]]
            return []

        def execute(self, sql, params=None):
            if "INSERT INTO email_attachment_staged" in sql:
                rows.append(
                    {
                        "id": params[0],
                        "user_id": params[1],
                        "filename": params[2],
                        "mime_type": params[3],
                        "size_bytes": params[4],
                        "storage_key": params[5],
                        "proposal_id": None,
                        "consumed_at": None,
                    }
                )
            elif "DELETE FROM email_attachment_staged" in sql:
                rows[:] = [r for r in rows if r["id"] not in params[0]]
            return None

    monkeypatch.setattr(config, "get_metadata_store", lambda: _StagingStore())
    monkeypatch.setattr(staging, "_staging_root", lambda: tmp_path / "staging")
    return rows


def test_stage_returns_a_reference_not_the_bytes(client, staged):
    r = client.post(
        "/api/v1/email/attachments/stage",
        files={"file": ("invoice.pdf", io.BytesIO(b"PDFBYTES"), "application/pdf")},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["filename"] == "invoice.pdf"
    assert body["size_bytes"] == len(b"PDFBYTES")
    assert set(body) == {"id", "filename", "mime_type", "size_bytes"}
    assert "PDFBYTES" not in r.text, "the response must carry a reference, never the bytes"


def test_staging_rejects_oversize(client, staged, monkeypatch):
    from services import email_attachments

    monkeypatch.setattr(email_attachments, "max_attachment_bytes", lambda: 4)

    r = client.post(
        "/api/v1/email/attachments/stage",
        files={"file": ("big.pdf", io.BytesIO(b"x" * 50), "application/pdf")},
    )

    assert r.status_code == 413


def test_staging_quota_refuses_with_413(client, staged, monkeypatch):
    monkeypatch.setattr(staging, "_quota_bytes", lambda: 10)
    client.post(
        "/api/v1/email/attachments/stage",
        files={"file": ("a.pdf", io.BytesIO(b"x" * 8), "application/pdf")},
    )

    r = client.post(
        "/api/v1/email/attachments/stage",
        files={"file": ("b.pdf", io.BytesIO(b"x" * 8), "application/pdf")},
    )

    assert r.status_code == 413
    assert r.json()["detail"]["error"] == "staging_quota_exceeded"


def test_staging_rate_bucket_is_isolated_from_the_llm_buckets(client, staged, monkeypatch):
    """Draining the upload bucket must leave the draft/quote/refine buckets usable."""
    from routes.api_v1.email_send import _draft_calls
    from routes.api_v1.email_send import _quote_calls

    for _ in range(31):
        client.post(
            "/api/v1/email/attachments/stage",
            files={"file": ("a.pdf", io.BytesIO(b"x"), "application/pdf")},
        )

    last = client.post(
        "/api/v1/email/attachments/stage",
        files={"file": ("a.pdf", io.BytesIO(b"x"), "application/pdf")},
    )
    assert last.status_code == 429
    assert not _draft_calls, "the LLM draft bucket must be untouched by uploads"
    assert not _quote_calls, "the LLM quote bucket must be untouched by uploads"


def test_discard_removes_a_staged_file(client, staged):
    created = client.post(
        "/api/v1/email/attachments/stage",
        files={"file": ("a.pdf", io.BytesIO(b"X"), "application/pdf")},
    ).json()

    r = client.delete(f"/api/v1/email/attachments/stage/{created['id']}")

    assert r.status_code == 200
    assert client.delete(f"/api/v1/email/attachments/stage/{created['id']}").status_code == 404


def test_discard_404_for_an_unknown_id(client, staged):
    assert client.delete("/api/v1/email/attachments/stage/no-such-id").status_code == 404


def test_download_has_its_own_rate_bucket(client, monkeypatch):
    """One download pulls a whole message into memory over a link we do not
    control the latency of, so the read side needs a bound as much as the
    upload side — and its own, so neither starves the other."""
    _own(monkeypatch)
    monkeypatch.setattr(config, "get_metadata_store", lambda: _Store([_row()]))

    codes = [client.get("/api/v1/email/signals/sig-1/attachments/0").status_code for _ in range(31)]

    assert codes[:30] == [200] * 30
    assert codes[30] == 429


def test_the_download_bucket_is_isolated_from_the_upload_bucket(client, staged, monkeypatch):
    from routes.api_v1.email_attachments import _download_calls

    for _ in range(31):
        client.post(
            "/api/v1/email/attachments/stage",
            files={"file": ("a.pdf", io.BytesIO(b"x"), "application/pdf")},
        )

    assert not _download_calls, "draining uploads must leave downloads usable"


def test_an_ambiguous_message_id_is_409_not_a_wrong_file(client, monkeypatch):
    """Refusing is the correct answer when identity cannot be established.

    Distinct from 410: the message is not gone, the mailbox is in a state we
    will not guess at. Serving a plausible-but-wrong file would be worse than
    any error code.
    """
    _own(monkeypatch)
    monkeypatch.setattr(config, "get_metadata_store", lambda: _Store([_row()]))
    _FakeAdapter.error = AttachmentFetchError("message_ambiguous", "two matches")

    r = client.get("/api/v1/email/signals/sig-1/attachments/0")

    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "message_ambiguous"
