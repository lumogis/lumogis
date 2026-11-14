"""Full-stack integration tests against a running orchestrator.

Run: docker compose up -d && make test-integration

Covers: health, ingest → search, entity extraction, session memory, signal sources,
feedback, routine execution → audit log, the retired /export route, and the
LUM-884 Rescan contract (no body fields, admin only).

With ``AUTH_ENABLED=true`` the ``api`` fixture logs in with the smoke credentials.
Full live lane: ``make e2e-ingest-live``.
"""

import os
import time
import uuid
from pathlib import Path

import httpx
import pytest
from _smoke_auth import current_user_id, file_index_row, llm_wait_s

pytestmark = pytest.mark.integration


@pytest.mark.integration
def test_health_detailed(api):
    r = api.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert "qdrant_doc_count" in data
    assert "entity_count" in data


@pytest.mark.integration
def test_status_page(api):
    r = api.get("/")
    assert r.status_code == 200
    body = r.json()
    assert "services" in body
    assert "status" in body


@pytest.mark.integration
def test_ingest_and_file_index(api, inbox_dir: Path):
    """Drop a real file in the inbox and verify its file_index row exists and is
    filed under the seeded owner (LUM-884).

    The row is read from Postgres: the global ``/health`` ``file_index_count``
    would pass on any owner's row, and semantic search with random tokens has a
    poor embedding match.
    """
    # /ingest no longer takes a folder (LUM-884); the inbox watcher picks the file up.
    hz = {}
    try:
        hz = api.get("/healthz").json()
    except Exception:
        pass
    if hz.get("inbox_watcher") != "ok":
        pytest.skip(
            "inbox watcher not running (set INBOX_OWNER_USER_ID); "
            "/ingest no longer takes a folder — LUM-884"
        )
    inbox_dir.mkdir(parents=True, exist_ok=True)
    token = f"lginteg_{uuid.uuid4().hex[:12]}"
    test_file = inbox_dir / f"{token}.txt"
    test_file.write_text(
        "Integration test ingest.\n"
        "The project roadmap was reviewed during the quarterly meeting.\n"
        f"Reference: {token}\n",
        encoding="utf-8",
    )
    try:
        row = None
        for _ in range(60):
            time.sleep(2)
            row = file_index_row(token)
            if row is not None:
                break
        assert row is not None, f"no file_index row for {token} after 120 s"
        assert row["user_id"] == current_user_id(api), (
            "inbox file must be filed under the seeded owner (INBOX_OWNER_USER_ID)"
        )
    finally:
        test_file.unlink(missing_ok=True)


@pytest.mark.integration
def test_semantic_search_returns_results(api):
    """Semantic search returns results for a query with meaningful words.

    Requires documents to already be indexed (run make ingest first, or
    test_ingest_and_file_index must have run first).
    """
    r = api.get("/search", params={"q": "quarterly roadmap project review", "limit": 5})
    assert r.status_code == 200
    # May return empty on a fresh stack with no docs — that is also valid.
    assert isinstance(r.json(), list)


@pytest.mark.integration
def test_entity_extraction_pipeline(api):
    """POST /entities/extract → async background task → GET /entities.

    Uses a realistic, clearly-formed person name so the LLM reliably classifies
    it as PERSON. Searches by first name substring to tolerate minor variations
    in how the LLM formats the extracted name. Skips (not fails) if the LLM
    does not complete in time (``LUMOGIS_INTEG_LLM_TIMEOUT_S``, default 90 s) —
    entity extraction depends on Ollama response time which varies by hardware
    tier. The live lane (``make e2e-ingest-live``) fails on any skip.
    """
    # Use a realistic person name. "Elena" is unambiguous as a first name.
    # The hex suffix makes the full name unique across test runs.
    suffix = uuid.uuid4().hex[:6].upper()
    first_name = "Elena"
    full_name = f"Dr. {first_name} {suffix}"
    er = api.post(
        "/entities/extract",
        json={
            "text": (
                f"{full_name} signed the quarterly contract with Acme Solutions Ltd today. "
                f"{full_name} is the Chief Executive Officer."
            ),
            "evidence_id": f"integ-evidence-{uuid.uuid4().hex[:8]}",
            "evidence_type": "SESSION",
        },
    )
    assert er.status_code == 200
    # /entities/extract queues the job (orchestrator/routes/data.py) since b263fbd2;
    # this assertion only surfaced once the test stopped skipping (LUM-929).
    assert er.json().get("status") == "extraction queued"

    wait_s = llm_wait_s(90)
    found = False
    for _ in range(max(1, int(wait_s // 2))):
        time.sleep(2)
        lr = api.get("/entities", params={"limit": 200})
        if lr.status_code != 200:
            continue
        for row in lr.json():
            name = row.get("name") or ""
            if suffix in name or (first_name in name and suffix[:3] in name):
                found = True
                break
        if found:
            break

    if not found:
        pytest.skip(
            f"{full_name!r} was not found in /entities after {wait_s:.0f} seconds. "
            "This usually means the LLM is still processing or returned malformed JSON. "
            "Re-run with a warmed-up Ollama model or increase the timeout."
        )


@pytest.mark.integration
def test_session_memory_pipeline(api):
    """Requires Ollama with a working llama model for summarization."""
    sid = str(uuid.uuid4())
    unique = f"sessionmem_{uuid.uuid4().hex[:8]}"
    # Baseline first: sessions_stored is global and the RC volume persists across runs.
    before = api.get("/")
    assert before.status_code == 200
    baseline = before.json().get("sessions_stored", 0)
    r = api.post(
        "/session/end",
        json={
            "session_id": sid,
            "messages": [
                {"role": "user", "content": f"Project codename is {unique}."},
                {"role": "assistant", "content": f"Confirmed: {unique}."},
            ],
        },
    )
    assert r.status_code == 200

    wait_s = llm_wait_s(120)
    for _ in range(max(1, int(wait_s // 3))):
        time.sleep(3)
        st = api.get("/")
        if st.status_code == 200 and st.json().get("sessions_stored", 0) > baseline:
            return
    pytest.skip(
        f"sessions_stored still {baseline} after {wait_s:.0f} s — "
        "check Ollama llama model and embedder (session pipeline)"
    )


@pytest.mark.integration
def test_signal_source_preview(api):
    """POST /sources confirm=false (preview) — no DB write, idempotent."""
    url = "https://news.ycombinator.com/rss"
    pr = api.post("/sources", json={"url": url, "confirm": False})
    assert pr.status_code == 200
    body = pr.json()
    assert body.get("source_type") == "rss"
    assert "preview_items" in body


@pytest.mark.integration
def test_signal_source_add_idempotent(api):
    """POST /sources confirm=true is idempotent: adds if absent, handles duplicate gracefully."""
    url = "https://news.ycombinator.com/rss"

    # Check if already present from a previous run.
    lr = api.get("/sources")
    assert lr.status_code == 200
    existing_urls = {s["url"] for s in lr.json().get("sources", [])}

    if url not in existing_urls:
        cr = api.post(
            "/sources",
            json={
                "url": url,
                "confirm": True,
                "name": f"hn-integ-{uuid.uuid4().hex[:6]}",
                "poll_interval": 300,
            },
        )
        # Created successfully.
        assert cr.status_code == 200
        assert cr.json().get("status") == "created"
    # Either way, it should appear in GET /sources now.
    lr2 = api.get("/sources")
    assert lr2.status_code == 200
    all_urls = {s["url"] for s in lr2.json().get("sources", [])}
    assert url in all_urls


@pytest.mark.integration
def test_signals_endpoint(api):
    """`GET /signals` always returns a valid response regardless of content."""
    sig = api.get("/signals", params={"limit": 10})
    assert sig.status_code == 200
    body = sig.json()
    assert "signals" in body
    assert "total" in body


@pytest.mark.integration
@pytest.mark.slow
def test_signals_populate_after_poll_window(api):
    """First RSS poll may take up to poll_interval (60s)."""
    time.sleep(65)
    r = api.get("/signals", params={"limit": 50})
    assert r.status_code == 200
    # May still be empty on very constrained networks; at least API works.
    assert "total" in r.json()


@pytest.mark.integration
def test_feedback_explicit(api):
    r = api.post(
        "/feedback",
        json={
            "item_type": "signal",
            "item_id": str(uuid.uuid4()),
            "positive": True,
        },
    )
    assert r.status_code == 200
    assert r.json().get("status") == "ok"


@pytest.mark.integration
def test_routine_run_writes_audit_log(api):
    r = api.post("/routines/inbox_digest/run")
    assert r.status_code == 200
    ar = api.get("/audit", params={"limit": 100})
    assert ar.status_code == 200
    names = [a.get("action_name", "") for a in ar.json().get("audit", [])]
    assert any("inbox_digest" in n for n in names), f"audit entries: {names[:5]}"


@pytest.mark.integration
def test_export_legacy_route_is_gone(api):
    """GET /export is retired (410) and names its successor."""
    r = api.get("/export")
    assert r.status_code == 410
    assert r.json()["detail"]["successor"] == "POST /api/v1/me/export"


@pytest.mark.integration
def test_rescan_rejects_body_fields(api):
    """LUM-884: POST /ingest takes no folder. A body field is a 422 (extra="forbid"),
    raised before the handler runs, so no library scan is queued."""
    r = api.post("/ingest", json={"path": "/workspace"})
    assert r.status_code == 422, f"expected 422, got {r.status_code}: {r.text[:300]}"


@pytest.mark.integration
def test_rescan_requires_auth():
    """LUM-884: Rescan is admin-only. Without a token it is a 401."""
    base_url = os.environ.get("LUMOGIS_API_URL", "http://127.0.0.1:8000")
    with httpx.Client(base_url=base_url, timeout=30.0) as anon:
        try:
            probe = anon.get("/")
        except httpx.ConnectError as exc:
            pytest.skip(f"Orchestrator unreachable at {base_url}: {exc}")
        if probe.status_code == 200:
            pytest.skip("AUTH_ENABLED=false — unauthenticated Rescan cannot be refused")
        r = anon.post("/ingest")
        assert r.status_code == 401, f"expected 401, got {r.status_code}: {r.text[:300]}"
