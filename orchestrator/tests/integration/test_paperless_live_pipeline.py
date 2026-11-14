# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""The paperless poll -> ingest path into real Postgres and real Qdrant (LUM-309, Pass 2).

Pass 1 (``test_paperless_live_adapter.py``) proves the HTTP contract. This
module proves what happens *after*: that ``_poll_paperless_source`` walks a real
paperless server and lands rows in a real ``external_documents`` table and
points in a real ``documents`` collection.

Neither store has ever seen a paperless document. ``test_two_users_have_
independent_paperless_ingest`` reads as though it covers this and does not — it
runs entirely on fakes (LUM-748), which is how the gap survived this long.

**Every test here takes the ``live_stores`` fixture.** That is not stylistic:
an autouse fixture in ``orchestrator/tests/conftest.py`` injects mock stores
into ``config._instances`` for every test in this tree, so a test that omits it
polls real paperless and writes into mocks while looking exactly like it
passed. See ``_paperless_live_stores`` for the full explanation.

**This gate proves plumbing, not retrieval quality.** The embedder is a hash
function. Nothing here says anything about whether search works.

**Isolation.** The fixture is shared for the whole session and documents
accumulate, so each test owns a tag *and* sets its source's ``poll_cursor`` to a
watermark captured immediately before its own seeding — otherwise a cursor-less
poll would ingest every document any earlier test seeded, Pass 1's included.
No assertion here may read a global count or an unfiltered table.
"""

from __future__ import annotations

import hashlib
import os
import uuid

import pytest
from models.signals import SourceConfig
from services.point_ids import external_document_chunk_point_id
from signals.feed_monitor import _poll_paperless_source
from tests.integration._paperless_guard import paperless_base_url
from tests.integration._paperless_guard import require_paperless
from tests.integration._paperless_live_stores import DOCUMENTS  # `live_stores` comes from conftest
from tests.integration._paperless_seed import RUN_ID
from tests.integration._paperless_seed import current_added_watermark
from tests.integration._paperless_seed import force_equal_added
from tests.integration._paperless_seed import mint_token as _mint_token
from tests.integration._paperless_seed import seed_document
from tests.integration._paperless_seed import tagged_text
from tests.integration._paperless_seed import wait_for_documents

import config

pytestmark = [pytest.mark.paperless_live]

FIXTURE_USER = f"lum309-user-{RUN_ID}"


@pytest.fixture(scope="session")
def paperless_api() -> tuple[str, str]:
    base = paperless_base_url()
    user = os.environ.get("PAPERLESS_ADMIN_USER", "admin")
    password = os.environ.get("PAPERLESS_ADMIN_PASSWORD", "")
    try:
        import httpx

        httpx.get(f"{base}/api/", timeout=10.0, follow_redirects=True).raise_for_status()
        token = _mint_token(base, user, password)
    except Exception as exc:  # pragma: no cover - env-dependent
        require_paperless(exc)
    return base, token


@pytest.fixture
def tag(request) -> str:
    name = request.node.name.removeprefix("test_")
    digest = hashlib.sha1(request.node.name.encode(), usedforsecurity=False).hexdigest()[:6]
    return f"lum309p-{name[:24]}-{digest}-{RUN_ID}-"


@pytest.fixture
def fixture_user(request) -> str:
    """One user id per test, so Qdrant assertions can scope by payload ``user_id``.

    ``scroll_collection`` filters on ``user_id``; a shared user would make every
    point-set assertion depend on execution order.
    """
    digest = hashlib.sha1(request.node.name.encode(), usedforsecurity=False).hexdigest()[:6]
    return f"lum309-{RUN_ID}-{digest}"


@pytest.fixture
def paperless_env(paperless_api, monkeypatch):
    """Point the credential fallback at the fixture.

    ``PAPERLESS_TOKEN`` cannot be exported by the Makefile — it does not exist
    until ``mint_token`` has run mid-session — so it is set here rather than in
    the environment the target builds.
    """
    base, token = paperless_api
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("PAPERLESS_BASE_URL", base)
    monkeypatch.setenv("PAPERLESS_TOKEN", token)
    return base, token


def _insert_source(ms, *, source_id: str, user_id: str, url: str, poll_cursor: str | None) -> None:
    ms.execute(
        (
            "INSERT INTO sources "
            "(id, user_id, name, source_type, url, category, active, poll_interval, "
            "extraction_method, poll_cursor) "
            "VALUES (%s, %s, %s, %s, %s, %s, TRUE, %s, %s, %s)"
        ),
        (
            source_id,
            user_id,
            "lum309-fixture",
            "paperless",
            url,
            "general",
            3600,
            "paperless_http",
            poll_cursor,
        ),
    )


def _source(source_id: str, url: str, user_id: str, poll_cursor: str | None) -> SourceConfig:
    return SourceConfig(
        id=source_id,
        name="lum309-fixture",
        source_type="paperless",
        url=url,
        category="general",
        active=True,
        poll_interval=3600,
        extraction_method="paperless_http",
        css_selector_override=None,
        last_polled_at=None,
        last_signal_at=None,
        poll_cursor=poll_cursor,
        user_id=user_id,
    )


def _rows_for(ms, source_id: str):
    """Rows for one source only — never a whole-table read.

    Scoped by ``source_id`` because each test inserts its own source; a
    whole-table assertion would fail on a developer's legitimate data and teach
    people to delete the assertion rather than trust it.
    """
    return ms.fetch_all(
        "SELECT external_id, user_id, chunk_count, external_kind, content_hash "
        "FROM external_documents WHERE source_id = %s ORDER BY external_id",
        (source_id,),
    )


def test_the_live_stores_fixture_displaces_the_autouse_mocks(live_stores):
    """The premise of every other test in this module, asserted on its own.

    ``orchestrator/tests/conftest.py`` injects ``MockMetadataStore`` /
    ``MockVectorStore`` / ``MockEmbedder`` into ``config._instances`` for every
    test in this tree, autouse. If that injection ever wins, this lane silently
    stops being an integration test — it keeps polling real paperless, keeps
    passing, and asserts against fakes.

    ``live_stores`` already asserts this at setup; this test exists so the
    property has a *name* in the test report, and so a regression in
    ``conftest.py`` surfaces as a clearly-labelled failure rather than an error
    inside some unrelated pipeline case.
    """
    from adapters.postgres_store import PostgresStore
    from adapters.qdrant_store import QdrantStore

    assert isinstance(config.get_metadata_store(), PostgresStore)
    assert isinstance(config.get_vector_store(), QdrantStore)

    embedder = config.get_embedder()
    vec = embedder.embed("lum309")
    assert len(vec) == embedder.vector_size
    assert any(v != 0.0 for v in vec), (
        "the zero-vector MockEmbedder is still installed; a cosine collection "
        "cannot store its output and every Qdrant assertion here would be vacuous"
    )


@pytest.fixture
def seeded_poll(live_stores, paperless_env, tag, fixture_user):
    """Seed three documents, wire a source scoped to them, and run one poll tick.

    Bundled into a fixture because five of the six cases below need exactly
    this arrangement, and the *ordering* inside it is load-bearing: the
    watermark must be captured **before** seeding, or a cursor-less poll
    ingests every document any earlier test left behind — Pass 1's included.
    """
    ms, vs = live_stores
    base, token = paperless_env
    source_id = str(uuid.uuid4())

    cursor = current_added_watermark(base, token)
    for suffix, body in (("a", "invoice"), ("b", "receipt"), ("c", "letter")):
        seed_document(
            base,
            token,
            filename=f"{tag}{suffix}.txt",
            content=tagged_text(f"{tag}{suffix}", f"{body} body for the live pipeline lane"),
            title=f"{tag}{suffix}",
        )
    rows = wait_for_documents(base, token, tag=tag, expected=3)

    _insert_source(ms, source_id=source_id, user_id=fixture_user, url=base, poll_cursor=cursor)
    _poll_paperless_source(_source(source_id, base, fixture_user, cursor))
    return ms, vs, source_id, rows


def test_poll_creates_external_document_rows_in_real_postgres(seeded_poll, fixture_user):
    """Real Postgres has never held a paperless row. This is that assertion."""
    ms, _vs, source_id, rows = seeded_poll

    stored = _rows_for(ms, source_id)

    assert len(stored) == 3, f"expected one row per seeded document, got {len(stored)}"
    for row in stored:
        assert row["user_id"] == fixture_user
        assert row["external_kind"] == "paperless"
        assert row["chunk_count"] > 0, "a document ingested with zero chunks is not ingested"
        assert row["content_hash"]
    assert {r["external_id"] for r in stored} == {str(d["id"]) for d in rows}


def test_poll_creates_qdrant_points_at_the_expected_deterministic_ids(seeded_poll, fixture_user):
    """The assertion LUM-304 was believed to make, against a real collection.

    Point ids are *computed* with the same function production uses rather than
    discovered by scanning, so this pins the id contract and not merely "some
    points exist".
    """
    ms, vs, source_id, _rows = seeded_poll

    stored = _rows_for(ms, source_id)
    expected: set[str] = set()
    for row in stored:
        for chunk_index in range(row["chunk_count"]):
            expected.add(
                external_document_chunk_point_id(
                    fixture_user, source_id, "paperless", row["external_id"], chunk_index
                )
            )
    assert expected, "no chunks were recorded, so this assertion would be vacuous"

    points = vs.scroll_collection(DOCUMENTS, user_id=fixture_user)
    got = {p["id"] for p in points}

    assert expected <= got, (
        f"{len(expected - got)} computed chunk point id(s) are missing from Qdrant; "
        "the deterministic id contract between ingest and point_ids has drifted"
    )
    for point in points:
        assert point["payload"]["user_id"] == fixture_user


def test_the_poll_cursor_advances_to_the_newest_added(seeded_poll):
    """The watermark must land on the newest ``added``, or the next tick re-reads."""
    ms, _vs, source_id, rows = seeded_poll

    row = ms.fetch_one("SELECT poll_cursor FROM sources WHERE id = %s", (source_id,))

    assert row["poll_cursor"] == rows[-1]["added"], (
        "poll_cursor should equal the newest seeded document's added timestamp"
    )


def test_a_second_poll_ingests_nothing_new(seeded_poll, fixture_user, paperless_env):
    """Dedup has only ever been proven against a fake store.

    Runs the tick a second time with no new documents and asserts neither
    Postgres nor Qdrant grew.
    """
    ms, vs, source_id, _rows = seeded_poll
    base, _token = paperless_env

    before_rows = _rows_for(ms, source_id)
    before_points = {p["id"] for p in vs.scroll_collection(DOCUMENTS, user_id=fixture_user)}

    _poll_paperless_source(_source(source_id, base, fixture_user, None))

    after_rows = _rows_for(ms, source_id)
    after_points = {p["id"] for p in vs.scroll_collection(DOCUMENTS, user_id=fixture_user)}

    assert len(after_rows) == len(before_rows), "a second poll created duplicate rows"
    assert after_points == before_points, "a second poll created duplicate Qdrant points"


def test_documents_sharing_an_added_timestamp_survive_a_page_boundary(
    live_stores, paperless_env, tag, fixture_user, monkeypatch
):
    """The scenario ADR 062 was actually written for — a bulk import stamping
    identical timestamps across a page boundary.

    ``added__gt`` is **strict**. If ``fetch_since`` were advanced mid-walk
    (rather than held constant for the whole tick), page N+1 would drop every
    document still sharing that exact ``added`` — silently. With
    ``page_size=1`` and two documents sharing a timestamp, that is exactly what
    would happen.

    **On how the precondition is manufactured.** LUM-309's plan said to PATCH
    two documents to a shared ``added``, on the strength of Step 0 Obs 7
    ("``PATCH {added} -> 200``, therefore writable"). That is wrong: ``added``
    is read-only over REST on paperless 3.0.4 — its own OpenAPI schema omits it
    from the writable set — and DRF answers 200 having silently ignored the
    field. Writing the test that way produced three *distinct* timestamps and a
    green run that proved nothing about ADR 062.

    The precondition assertion below is what caught it. The timestamps are now
    stamped in paperless's own database via
    :func:`_paperless_seed.force_equal_added`; see that function for why
    reaching past the API is legitimate here.

    **The precondition is asserted, not assumed.** A test whose name claims one
    scenario while silently exercising another is the failure this entire chunk
    exists to prevent — and in this case it caught the plan's own instructions.
    """
    ms, vs = live_stores
    base, token = paperless_env
    source_id = str(uuid.uuid4())
    monkeypatch.setenv("PAPERLESS_POLL_PAGE_SIZE", "1")

    cursor = current_added_watermark(base, token)
    for suffix in ("a", "b", "c"):
        seed_document(
            base,
            token,
            filename=f"{tag}{suffix}.txt",
            content=tagged_text(f"{tag}{suffix}", "shared-timestamp pagination case"),
            title=f"{tag}{suffix}",
        )
    rows = wait_for_documents(base, token, tag=tag, expected=3)

    # Force two of the three onto a byte-identical `added`, and place them
    # either side of a page boundary at page_size=1.
    force_equal_added([rows[0]["id"], rows[1]["id"]])

    after = wait_for_documents(base, token, tag=tag, expected=3)
    added_values = [d["added"] for d in after]
    assert len(set(added_values)) < len(added_values), (
        "PRECONDITION FAILED: the documents do not share an `added` timestamp "
        f"({added_values}), so this test would silently exercise the ordinary "
        "distinct-timestamp case and prove nothing about ADR 062"
    )

    _insert_source(ms, source_id=source_id, user_id=fixture_user, url=base, poll_cursor=cursor)
    _poll_paperless_source(_source(source_id, base, fixture_user, cursor))

    stored = _rows_for(ms, source_id)
    assert len(stored) == 3, (
        f"only {len(stored)} of 3 documents survived a page walk in which two "
        "share an `added` timestamp — a strict `added__gt` advanced mid-walk "
        "drops the ones sharing the boundary value (ADR 062)"
    )


def test_this_runs_writes_never_land_under_another_user(seeded_poll, fixture_user):
    """``user_id`` isolation in real Postgres *and* real Qdrant.

    Scoped to rows created during this tick rather than asserted over the whole
    table: a global assertion is only true against a guaranteed-empty store, and
    run against a developer's stack it fails on their own legitimate data —
    which teaches people to delete the assertion (D5.3).
    """
    ms, vs, source_id, _rows = seeded_poll

    stored = _rows_for(ms, source_id)
    assert stored, "nothing was ingested, so this isolation assertion would be vacuous"
    assert {r["user_id"] for r in stored} == {fixture_user}

    points = vs.scroll_collection(DOCUMENTS, user_id=fixture_user)
    assert points, "no Qdrant points for the fixture user"
    for point in points:
        assert point["payload"]["user_id"] == fixture_user

    leaked = ms.fetch_all(
        "SELECT user_id FROM external_documents WHERE source_id = %s AND user_id <> %s",
        (source_id, fixture_user),
    )
    assert leaked == [], f"rows for this source landed under another user: {leaked}"
