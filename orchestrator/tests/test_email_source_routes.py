# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Route tests for the LUM-170 email-source + sync endpoints.

POST /me/email-sources  — per-user IMAP mailbox registration
POST /sources/{id}/sync — manual "Sync now" (enqueue-only)
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from tests.test_auth_phase1 import FakeUsersStore


class _EmailRoutesStore(FakeUsersStore):
    def __init__(self) -> None:
        super().__init__()
        self.sources: dict[str, dict] = {}

    @staticmethod
    def _norm(q: str) -> str:
        return " ".join(q.split()).lower()

    def execute(self, query, params=None):
        q = self._norm(query)
        p = params or ()
        if q.startswith("insert into sources"):
            # Literals in SQL ('imap','email',TRUE,NULL,'pending'); params (LUM-644):
            # (id, user_id, name, url, poll_interval, extraction_method,
            #  import_scope, import_since, import_before)
            sid, user_id, name, url, poll_interval, extraction_method = p[:6]
            import_scope = p[6] if len(p) > 6 else None
            import_since = p[7] if len(p) > 7 else None
            import_before = p[8] if len(p) > 8 else None
            import_state = p[9] if len(p) > 9 else None
            self.sources[sid] = {
                "id": sid,
                "user_id": user_id,
                "name": name,
                "source_type": "imap",
                "url": url,
                "category": "email",
                "active": True,
                "poll_interval": poll_interval,
                "extraction_method": extraction_method,
                "css_selector_override": None,
                "last_polled_at": None,
                "last_signal_at": None,
                "poll_cursor": None,
                "import_scope": import_scope,
                "import_since": import_since,
                "import_before": import_before,
                "import_state": import_state,
            }
            return
        return super().execute(query, params)

    def fetch_one(self, query, params=None):
        q = self._norm(query)
        p = params or ()
        if (
            q.startswith("select id, name, source_type, url, category")
            and "from sources where id::text = %s and user_id = %s" in q
        ):
            sid, user_id = p
            row = self.sources.get(sid)
            if row and row["user_id"] == user_id:
                return dict(row)
            return None
        if q.startswith(
            "select url from sources where id = %s and user_id = %s and source_type = 'imap'"
        ):
            sid, user_id = p
            row = self.sources.get(sid)
            if row and row["user_id"] == user_id and row.get("source_type") == "imap":
                return {"url": row["url"]}
            return None
        return super().fetch_one(query, params)

    def fetch_all(self, query, params=None):
        q = self._norm(query)
        if "from sources" in q:
            return [dict(r) for r in self.sources.values() if r.get("active")]
        try:
            return super().fetch_all(query, params)
        except Exception:
            return []


class _FakeScheduler:
    def __init__(self) -> None:
        self.jobs: dict[str, object] = {}
        self.running = True

    def start(self, *a, **k):
        self.running = True

    def shutdown(self, *a, **k):
        self.running = False

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def get_jobs(self):
        return list(self.jobs.values())

    def remove_job(self, job_id, *a, **k):
        self.jobs.pop(job_id, None)

    def add_job(self, func, *a, **kwargs):
        job_id = kwargs.get("id") or f"job{len(self.jobs)}"
        job = type("Job", (), {"id": job_id, "remove": lambda self: None})()
        self.jobs[job_id] = job
        return job


@pytest.fixture
def env(monkeypatch):
    import config as _config
    from services import connector_credentials as ccs

    store = _EmailRoutesStore()
    sched = _FakeScheduler()
    _config._instances["metadata_store"] = store
    _config._instances["scheduler"] = sched
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)
    monkeypatch.setenv(
        "LUMOGIS_CREDENTIAL_KEY",
        "OlGLYckGIbBSt54y8XVmgb441LgKJWvvYoHnpQ_cv9A=",
    )
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEYS", raising=False)
    ccs.reset_for_tests()
    yield store, sched
    _config._instances.pop("metadata_store", None)
    _config._instances.pop("scheduler", None)
    ccs.reset_for_tests()


@contextmanager
def _client():
    import main
    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        yield client


def test_create_email_source_returns_201(env):
    store, sched = env
    with _client() as client:
        resp = client.post(
            "/me/email-sources",
            json={"host": "imap.example.com", "mailbox_label": "personal", "name": "Me"},
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["source_type"] == "imap"
    assert body["url"] == "imap://imap.example.com:993/personal"
    assert "@" not in body["url"]  # no PII in the source url
    assert body["extraction_method"] == "imap_metadata"
    assert len(store.sources) == 1


def test_create_email_source_full_ingest_method(env):
    with _client() as client:
        resp = client.post(
            "/me/email-sources",
            json={"host": "h.com", "mailbox_label": "work", "full_ingest": True},
        )
    assert resp.status_code == 201
    assert resp.json()["extraction_method"] == "imap_full_ingest"


def test_create_email_source_bad_label_422(env):
    with _client() as client:
        resp = client.post(
            "/me/email-sources",
            json={"host": "h.com", "mailbox_label": "Not A Label!"},
        )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "bad_mailbox_label"


@pytest.mark.parametrize("host", ["", "   ", "\t", "\n  "])
def test_create_email_source_empty_host_422(env, host):
    """LUM-781 — an empty or whitespace-only host is refused at creation.

    Without this the route builds `imap://:993/label`. Under LUM-773's invariant
    that row is permanently un-credentialable: every credential PUT returns 422
    `imap_source_url_invalid`, and `sources.url` has no edit path, so the mailbox
    can never complete the two-step contract. The whitespace variants are the ones
    a bare `host: str` type cannot reject on its own.
    """
    store, _ = env
    with _client() as client:
        resp = client.post(
            "/me/email-sources",
            json={"host": host, "mailbox_label": "personal"},
        )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "bad_host"
    # And nothing was written -- a 4xx that still inserts is the worse bug.
    assert store.sources == {}


def test_create_email_source_stores_idna_a_label(env):
    """LUM-782 — unicode / trailing-dot / mixed-case hosts are stored as A-labels."""
    store, _ = env
    with _client() as client:
        resp = client.post(
            "/me/email-sources",
            json={"host": "Bücher.Example.", "mailbox_label": "personal"},
        )
    assert resp.status_code in (200, 201), resp.text
    urls = [r["url"] for r in store.sources.values()]
    assert urls == ["imap://xn--bcher-kva.example:993/personal"]


@pytest.mark.parametrize("host", ["xn--zz.example", "a..b"])
def test_create_email_source_invalid_idna_host_422(env, host):
    """LUM-782 — an invalid host is refused at creation, nothing written."""
    store, _ = env
    with _client() as client:
        resp = client.post("/me/email-sources", json={"host": host, "mailbox_label": "personal"})
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "bad_host"
    assert store.sources == {}


def test_create_email_source_missing_host_422(env):
    """LUM-781 — an absent `host` key is a 422 from pydantic, not a 500."""
    store, _ = env
    with _client() as client:
        resp = client.post("/me/email-sources", json={"mailbox_label": "personal"})
    assert resp.status_code == 422, resp.text
    assert store.sources == {}


def test_create_email_source_trims_surrounding_whitespace_in_host(env):
    """LUM-781 — a host with stray surrounding space is stored trimmed.

    Asserted on the STORED url rather than the response alone: the url is what the
    credential layer parses the host back out of (LUM-773), so `imap:// h.com :993/`
    would round-trip to a host that never resolves.
    """
    store, _ = env
    with _client() as client:
        resp = client.post(
            "/me/email-sources",
            json={"host": "  imap.example.com  ", "mailbox_label": "personal"},
        )
    assert resp.status_code == 201, resp.text
    assert resp.json()["url"] == "imap://imap.example.com:993/personal"
    stored = next(iter(store.sources.values()))
    assert stored["url"] == "imap://imap.example.com:993/personal"


def test_two_mailboxes_have_distinct_urls(env):
    store, _ = env
    with _client() as client:
        client.post(
            "/me/email-sources", json={"host": "imap.gmail.com", "mailbox_label": "personal"}
        )
        client.post("/me/email-sources", json={"host": "imap.gmail.com", "mailbox_label": "work"})
    urls = sorted(s["url"] for s in store.sources.values())
    assert urls == ["imap://imap.gmail.com:993/personal", "imap://imap.gmail.com:993/work"]


def test_sync_existing_source_returns_202(env):
    store, sched = env
    with _client() as client:
        created = client.post(
            "/me/email-sources", json={"host": "h.com", "mailbox_label": "personal"}
        ).json()
        sid = created["source_id"]
        resp = client.post(f"/sources/{sid}/sync")
    assert resp.status_code == 202, resp.text
    assert resp.json() == {"status": "queued", "source_id": sid}
    assert f"signal_sync_now_{sid}" in sched.jobs


def test_sync_unknown_source_404(env):
    with _client() as client:
        resp = client.post("/sources/does-not-exist/sync")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "source_not_found"


def test_sync_already_running_409(env):
    store, sched = env
    with _client() as client:
        sid = client.post(
            "/me/email-sources", json={"host": "h.com", "mailbox_label": "personal"}
        ).json()["source_id"]
        # Pre-seed the sync job to simulate one already queued.
        sched.jobs[f"signal_sync_now_{sid}"] = object()
        resp = client.post(f"/sources/{sid}/sync")
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "already_running"


# --- LUM-644 import scope on create ------------------------------------------


def test_create_sets_import_state_pending_and_scope(env):
    store, _ = env
    with _client() as client:
        resp = client.post(
            "/me/email-sources",
            json={
                "host": "imap.example.com",
                "mailbox_label": "personal",
                "import_scope": "last_30d",
            },
        )
    assert resp.status_code == 201, resp.text
    row = next(iter(store.sources.values()))
    # import_state MUST be set at create ('pending') so seeding fires and the
    # legacy NULL-cursor 1:* drain never runs for a setup-created mailbox.
    assert row["import_state"] == "pending"
    assert row["import_scope"] == "last_30d"


def test_create_just_new_scope(env):
    store, _ = env
    with _client() as client:
        resp = client.post(
            "/me/email-sources",
            json={"host": "h.com", "mailbox_label": "a", "import_scope": "just_new"},
        )
    assert resp.status_code == 201
    assert next(iter(store.sources.values()))["import_scope"] == "just_new"


def test_custom_without_since_is_422(env):
    with _client() as client:
        resp = client.post(
            "/me/email-sources",
            json={"host": "h.com", "mailbox_label": "a", "import_scope": "custom"},
        )
    assert resp.status_code == 422


def test_non_custom_with_dates_is_422(env):
    with _client() as client:
        resp = client.post(
            "/me/email-sources",
            json={
                "host": "h.com",
                "mailbox_label": "a",
                "import_scope": "last_30d",
                "import_since": "2026-01-01",
            },
        )
    assert resp.status_code == 422


def test_full_ingest_ignores_import_scope(env):
    # Import scopes apply to metadata mode only; a full-ingest source leaves
    # import_state NULL (its separate ingest path handles polling).
    store, _ = env
    with _client() as client:
        resp = client.post(
            "/me/email-sources",
            json={"host": "h.com", "mailbox_label": "a", "full_ingest": True},
        )
    assert resp.status_code == 201
    assert next(iter(store.sources.values()))["import_state"] is None


def test_full_ingest_with_explicit_dates_is_422(env):
    with _client() as client:
        resp = client.post(
            "/me/email-sources",
            json={
                "host": "h.com",
                "mailbox_label": "a",
                "full_ingest": True,
                "import_scope": "custom",
                "import_since": "2024-01-01",
            },
        )
    assert resp.status_code == 422


# --- LUM-771: the flag and the opt-in route must agree about the host --------


@pytest.mark.parametrize(
    ("host", "expected_required"),
    [("imap.gmail.com", False), ("mail.selfhosted.example", True)],
)
def test_the_flag_and_the_route_agree_about_the_host(env, host, expected_required):
    """`GET /sources`'s `smtp_host_required` parses `sources.url`; `set_email_smtp`
    matches on the host in the decrypted IMAP credential. Nothing structurally
    forces those two to be the same host.

    The OAuth path is safe by construction — `sources.url` and the credential
    payload are both built from `flow['host']` inside one function (`signals.py`
    :814/:816). The **app-password path is not**: the URL comes from `body.host`
    on `POST /me/email-sources` while the credential is written by a separate
    client call to `PUT /connector-credentials/imap`. Two independent
    client-supplied values that agree today only because `EmailMailboxSetupForm`
    sends `host.trim()` to both — client discipline, not a server invariant, and
    exactly the kind that holds until someone writes a second client.

    So this pins the agreement rather than merely describing it. A server-side
    invariant is the real fix and is deferred (follow-up under LUM-650).
    """
    from routes.signals import _smtp_host_required
    from services.email_providers import provider_for_host

    store, _ = env
    with _client() as client:
        resp = client.post("/me/email-sources", json={"host": host, "mailbox_label": "personal"})
    assert resp.status_code == 201, resp.text
    row = next(iter(store.sources.values()))

    # What the flag derives from `sources.url` …
    assert _smtp_host_required(row) is expected_required
    # … and what `set_email_smtp` derives from the credential the client stores
    # under the SAME host string. If these ever diverge, one mailbox gets two
    # different answers to "do I need to type a submission host?".
    assert (provider_for_host(host) is None) is expected_required


def test_an_oauth_mailbox_is_never_asked_for_a_submission_host(env):
    """The device flow's URL format, cross-checked against the registry.

    `imap://outlook.office365.com:993/inbox` — with the port every real row
    carries. Under a `.netloc` parse this reports `true`, and LUM-769 would show
    host/port fields to every Outlook user for a mailbox whose submission host
    the registry already knows.
    """
    from routes.signals import _smtp_host_required

    row = {"source_type": "imap", "url": "imap://outlook.office365.com:993/inbox"}
    assert _smtp_host_required(row) is False


def test_smtp_host_required_false_when_valid_host_bad_port():
    from routes.signals import _smtp_host_required

    row = {
        "source_type": "imap",
        "url": "imap://imap.gmail.com:notaport/personal",
    }
    assert _smtp_host_required(row) is False


def test_smtp_host_required_true_when_url_unparseable():
    from routes.signals import _smtp_host_required

    row = {"source_type": "imap", "url": "imap://[bad:993/x"}
    assert _smtp_host_required(row) is True


def test_imap_credential_put_rejects_host_mismatch_after_email_source_create(env):
    """HTTP two-step: POST source then mismatch PUT → 422, credential not stored."""
    with _client() as client:
        create = client.post(
            "/me/email-sources",
            json={"host": "imap.gmail.com", "mailbox_label": "personal"},
        )
        assert create.status_code == 201, create.text
        source_id = create.json()["source_id"]
        mismatch = client.put(
            f"/api/v1/me/connector-credentials/imap?instance={source_id}",
            json={
                "payload": {
                    "host": "evil.example.com",
                    "port": 993,
                    "username": "u",
                    "password": "p",
                }
            },
        )
        got = client.get(f"/api/v1/me/connector-credentials/imap?instance={source_id}")
    assert mismatch.status_code == 422
    assert mismatch.json()["detail"]["code"] == "imap_host_mismatch"
    assert got.status_code == 404
