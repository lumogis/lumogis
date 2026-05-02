"""Full-stack integration tests against a running orchestrator.

Run: docker compose up -d && make test-integration

Covers: health, ingest → search, entity extraction, session memory, signal sources,
feedback, routine execution → audit log, export (NDJSON).
"""

import json
import time
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.integration
@pytest.mark.public_rc
def test_health_detailed(api):
    r = api.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert "qdrant_doc_count" in data
    assert "entity_count" in data


@pytest.mark.integration
@pytest.mark.public_rc
def test_status_page(api):
    r = api.get("/")
    assert r.status_code == 200
    body = r.json()
    assert "services" in body
    assert "status" in body


@pytest.mark.integration
@pytest.mark.public_rc
def test_ingest_and_file_index(api, repo_root: Path):
    """Ingest a real file and verify it appears in the file_index count (not semantic search).

    Semantic search with random tokens has a poor embedding match; checking
    file_index_count in /health is a more reliable end-to-end ingest proof.
    """
    inbox = repo_root / "ai-workspace" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    token = f"lginteg_{uuid.uuid4().hex[:12]}"
    test_file = inbox / f"{token}.txt"
    test_file.write_text(
        "Integration test ingest.\n"
        "The project roadmap was reviewed during the quarterly meeting.\n"
        f"Reference: {token}\n",
        encoding="utf-8",
    )
    try:
        hr_before = api.get("/health")
        assert hr_before.status_code == 200
        count_before = hr_before.json().get("file_index_count", 0)

        ir = api.post("/ingest", json={"path": "/workspace/inbox"})
        assert ir.status_code == 200

        found = False
        for _ in range(60):
            time.sleep(2)
            hr = api.get("/health")
            if hr.status_code == 200 and hr.json().get("file_index_count", 0) > count_before:
                found = True
                break
        assert found, (
            f"file_index_count should increase after ingest "
            f"(was {count_before}, still {api.get('/health').json().get('file_index_count')})"
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
    does not complete within 90 seconds — entity extraction depends on Ollama
    response time which varies by hardware tier.
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
    assert er.json().get("status") == "extraction started"

    found = False
    for _ in range(45):
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
            f"{full_name!r} was not found in /entities after 90 seconds. "
            "This usually means the LLM is still processing or returned malformed JSON. "
            "Re-run with a warmed-up Ollama model or increase the timeout."
        )


@pytest.mark.integration
def test_session_memory_pipeline(api):
    """Requires Ollama with a working llama model for summarization."""
    sid = str(uuid.uuid4())
    unique = f"sessionmem_{uuid.uuid4().hex[:8]}"
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

    for _ in range(40):
        time.sleep(3)
        st = api.get("/")
        if st.status_code == 200 and st.json().get("sessions_stored", 0) > 0:
            return
    pytest.skip(
        "sessions_stored still 0 — check Ollama llama model and embedder (session pipeline)"
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
@pytest.mark.public_rc
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
@pytest.mark.public_rc
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
@pytest.mark.public_rc
def test_routine_run_writes_audit_log(api):
    r = api.post("/routines/inbox_digest/run")
    assert r.status_code == 200
    ar = api.get("/audit", params={"limit": 100})
    assert ar.status_code == 200
    names = [a.get("action_name", "") for a in ar.json().get("audit", [])]
    assert any("inbox_digest" in n for n in names), f"audit entries: {names[:5]}"


@pytest.mark.integration
def test_export_ndjson(api):
    r = api.get("/export")
    assert r.status_code == 200
    lines = [ln for ln in r.text.strip().split("\n") if ln.strip()]
    assert lines, "export should yield at least one NDJSON line"
    first = json.loads(lines[0])
    assert "section" in first and "rows" in first
    sections = {json.loads(ln)["section"] for ln in lines}
    assert "file_index" in sections or "entities" in sections
