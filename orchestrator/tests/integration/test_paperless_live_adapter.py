# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``PaperlessPoller`` against a real paperless-ngx server (LUM-309, Pass 1).

Every other paperless test in this repository monkeypatches
``PaperlessPoller.fetch_documents_page`` wholesale, so until this module existed
the adapter's request layer — its URL, its params, its auth header, its
envelope parsing, its terminal condition — **had never made a real request**.
That gap is not hypothetical: planning this lane is what surfaced LUM-747, a
production defect (~31s of retry backoff and an ERROR log on every healthy poll
tick) that the fakes had hidden because they modelled the end of a page walk as
an empty page. The real server returns 404.

**This module uses no Lumogis infrastructure at all** — no Postgres, no Qdrant,
no embedder. It is the HTTP contract and nothing else. The pipeline lives in
``test_paperless_live_pipeline.py``.

**This gate proves plumbing, not retrieval quality.** Nothing here says anything
about whether embeddings are good or search is useful.

Gate: ``make compose-test-paperless``. Skips when the fixture is absent unless
``LUMOGIS_REQUIRE_PAPERLESS_FIXTURE=1``, which makes an unreachable fixture a
hard failure (LUM-718).

**Test isolation.** The fixture container lives for the whole session and
documents accumulate across tests, so every test here owns a tag, titles its
seeded documents with it, and filters every assertion on it. No assertion may
read a global ``count`` or an unfiltered page — such an assertion is
order-dependent and becomes false the moment any other test seeds anything.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import httpx
import pytest
from adapters import paperless_source
from adapters.paperless_source import PaperlessPoller
from services.paperless_credentials import PaperlessConnection
from tests.integration._paperless_guard import paperless_base_url
from tests.integration._paperless_guard import require_paperless
from tests.integration._paperless_seed import RUN_ID
from tests.integration._paperless_seed import current_added_watermark
from tests.integration._paperless_seed import mint_token as _mint_token
from tests.integration._paperless_seed import seed_document
from tests.integration._paperless_seed import tagged_text
from tests.integration._paperless_seed import wait_for_documents

pytestmark = [pytest.mark.paperless_live]

_FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "paperless"


def _body(name: str) -> str:
    return (_FIXTURE_DIR / name).read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def paperless_api() -> tuple[str, str]:
    """Reachability check plus one token mint for the whole session.

    Session-scoped because minting is a real login: doing it per test would add
    a round trip to every case for no coverage.
    """
    base = paperless_base_url()
    user = os.environ.get("PAPERLESS_ADMIN_USER", "admin")
    password = os.environ.get("PAPERLESS_ADMIN_PASSWORD", "")
    try:
        # follow_redirects=True is load-bearing, not tidiness. `/api/` answers
        # 302 -> `schema/view/`, and httpx neither follows redirects by default
        # nor treats a 3xx as success — so the obvious probe reports a healthy
        # server as unreachable. This is the same 302-vs-200 subtlety the
        # compose healthcheck comment warns about, in a second place.
        httpx.get(f"{base}/api/", timeout=10.0, follow_redirects=True).raise_for_status()
    except Exception as exc:  # pragma: no cover - env-dependent
        require_paperless(exc)
    try:
        token = _mint_token(base, user, password)
    except Exception as exc:  # pragma: no cover - env-dependent
        require_paperless(exc, what="paperless token endpoint")
    return base, token


@pytest.fixture
def tag(request) -> str:
    """A per-test, per-run title prefix.

    Hashed rather than spelled out because paperless bounds the title length
    and these test names are long; the readable stem is kept so a failure
    message still points at the right test.

    ``RUN_ID`` is what makes the lane re-runnable against a container that is
    already up — see its docstring in ``_paperless_seed``.
    """
    name = request.node.name.removeprefix("test_")
    digest = hashlib.sha1(request.node.name.encode(), usedforsecurity=False).hexdigest()[:6]
    return f"lum309a-{name[:24]}-{digest}-{RUN_ID}-"


@pytest.fixture
def poller(paperless_api) -> PaperlessPoller:
    base, token = paperless_api
    return PaperlessPoller(PaperlessConnection(base_url=base, token=token))


def _seed_and_wait(base, token, tag, specs) -> list[dict]:
    """Seed ``specs`` (list of (suffix, body)) under ``tag``; return server rows.

    Returns the *watermark* captured before seeding alongside nothing else —
    callers take it separately. Content is tagged so no two tests can collide on
    paperless's checksum dedup.
    """
    for suffix, body in specs:
        seed_document(
            base,
            token,
            filename=f"{tag}{suffix}.txt",
            content=tagged_text(f"{tag}{suffix}", body),
            title=f"{tag}{suffix}",
        )
    return wait_for_documents(base, token, tag=tag, expected=len(specs))


def _mine(docs, tag: str):
    """Filter poller results down to this test's own documents."""
    return [d for d in docs if d.title.startswith(tag)]


def test_a_seeded_document_is_returned_with_its_content(paperless_api, poller, tag):
    """The whole request/parse chain, end to end, against a real server."""
    base, token = paperless_api
    cursor = current_added_watermark(base, token)
    _seed_and_wait(base, token, tag, [("invoice", _body("invoice.txt"))])

    docs, auth_fail = poller.fetch_documents_page(since_cursor=cursor, page=1)

    assert auth_fail is False
    mine = _mine(docs, tag)
    assert len(mine) == 1, f"expected exactly one document tagged {tag!r}, got {len(mine)}"
    doc = mine[0]
    assert doc.title == f"{tag}invoice"
    assert doc.content.strip(), "paperless returned an empty content field for a .txt upload"
    assert "Northgate Plumbing" in doc.content
    assert doc.added, "added must be populated — the watermark logic depends on it"
    assert isinstance(doc.id, int)


def test_a_bad_token_reports_auth_failure_and_does_not_raise(paperless_api, tag):
    """The 401 -> ``__auth_error__`` mapping, against a real 401.

    This is the branch that fails *open* if it breaks: a poller that raised
    here would be noticed, but one that reported "no documents" would silently
    stop ingesting forever.

    ``PaperlessConnection`` is constructed directly rather than through
    ``load_connection`` — it is a plain frozen dataclass, so this test needs no
    credential row, no ``AUTH_ENABLED`` juggling, and does not trip the
    outbound-URL validator for what is a question about one HTTP status code.

    Fast by construction: ``_request_json`` returns on 401/403 *before* the
    backoff ladder. A wrong *host* would instead cost ~31s across five
    attempts — do not write that variant.
    """
    base, _ = paperless_api
    bad = PaperlessPoller(PaperlessConnection(base_url=base, token="definitely-not-a-token"))

    docs, auth_fail = bad.fetch_documents_page(since_cursor=None, page=1)

    assert (docs, auth_fail) == ([], True)


def test_added_gt_actually_filters(paperless_api, poller, tag):
    """``added__gt`` is the filter LUM-309's version-floor worry is about.

    It had never been executed against a real server.
    """
    base, token = paperless_api
    # No pre-seed watermark here: this test's cursor is the *second seeded
    # document's* `added`, which is the whole point of the case.
    rows = _seed_and_wait(
        base,
        token,
        tag,
        [("a", _body("invoice.txt")), ("b", _body("receipt.txt")), ("c", _body("letter.txt"))],
    )
    assert len(rows) == 3
    second_added = rows[1]["added"]

    docs, auth_fail = poller.fetch_documents_page(since_cursor=second_added, page=1)

    assert auth_fail is False
    titles = {d.title for d in _mine(docs, tag)}
    assert titles == {f"{tag}c"}, (
        "added__gt is strict: only documents added strictly after the second "
        f"should return, got {sorted(titles)}"
    )


def test_ordering_by_added_is_ascending(paperless_api, poller, tag):
    """The watermark logic assumes oldest-first; nothing had checked the server agrees."""
    base, token = paperless_api
    cursor = current_added_watermark(base, token)
    _seed_and_wait(
        base,
        token,
        tag,
        [("a", _body("invoice.txt")), ("b", _body("receipt.txt")), ("c", _body("letter.txt"))],
    )

    docs, _ = poller.fetch_documents_page(since_cursor=cursor, page=1)

    added = [d.added for d in _mine(docs, tag)]
    assert len(added) == 3
    assert added == sorted(added), f"expected non-decreasing added values, got {added}"


def test_pagination_covers_every_document_in_this_tests_tag(
    paperless_api, poller, tag, monkeypatch
):
    """Walk the pages one at a time and prove nothing falls between them.

    Two things are pinned here. First, the union of a ``page_size=1`` walk
    equals this test's tagged set — the URL/param/envelope contract. Second,
    **the terminal condition is a 404**, asserted directly rather than assumed.

    Step 0 (live paperless 3.0.4) measured page 4 of 3 returning 404
    ``{"detail":"Invalid page."}``. The fakes had modelled it as an empty 200,
    which is how LUM-747 stayed hidden. The retry ladder is shrunk to a single
    attempt for this walk because what is under test is the pagination
    contract, not the ladder — the ladder has its own unit coverage.
    """
    base, token = paperless_api
    monkeypatch.setattr(paperless_source, "_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(paperless_source, "_BASE_DELAY_S", 0.0)
    monkeypatch.setenv("PAPERLESS_POLL_PAGE_SIZE", "1")

    cursor = current_added_watermark(base, token)
    _seed_and_wait(
        base,
        token,
        tag,
        [("a", _body("invoice.txt")), ("b", _body("receipt.txt")), ("c", _body("letter.txt"))],
    )

    seen: set[str] = set()
    page = 1
    while page < 25:  # bounded so a server-side regression cannot hang the gate
        docs, auth_fail = poller.fetch_documents_page(since_cursor=cursor, page=page)
        assert auth_fail is False
        if not docs:
            break
        seen.update(d.title for d in _mine(docs, tag))
        page += 1

    assert seen == {f"{tag}a", f"{tag}b", f"{tag}c"}

    # The terminal condition itself, asserted against the server rather than
    # inferred from the adapter's return value.
    terminal = httpx.get(
        f"{base}/api/documents/",
        headers={"Authorization": f"Token {token}"},
        params={"ordering": "added", "page_size": 1, "page": 9999, "added__gt": cursor or ""},
        timeout=30.0,
    )
    assert terminal.status_code == 404, (
        "DRF PageNumberPagination is expected to answer a page past the last "
        f"with 404, got {terminal.status_code}. If paperless has switched to a "
        "custom paginator returning an empty 200, LUM-747's terminal handling "
        "and this test both need revisiting."
    )


def test_the_auth_header_is_the_token_scheme_the_server_accepts(paperless_api, poller):
    """Pin *our* client's header, not merely the server's preference.

    The positive leg deliberately routes through ``PaperlessPoller._headers()``
    rather than building its own. If both legs built their own headers, this
    test would pin the server while proving nothing about the adapter, and it
    would stay green through a regression in ``_headers()``.
    """
    base, token = paperless_api

    ours = httpx.get(
        f"{base}/api/documents/",
        headers=poller._headers(),
        params={"page_size": 1},
        timeout=30.0,
    )
    assert ours.status_code == 200, "the header PaperlessPoller actually sends was rejected"

    bearer = httpx.get(
        f"{base}/api/documents/",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        params={"page_size": 1},
        timeout=30.0,
    )
    assert bearer.status_code in (401, 403), (
        "paperless is expected to reject the Bearer scheme; if it now accepts "
        f"it ({bearer.status_code}), the Token scheme is no longer load-bearing "
        "and this assertion should be revisited rather than deleted"
    )


def test_a_document_with_empty_content_is_skipped_not_crashed(paperless_api, poller, tag):
    """``paperless_source.py`` claims to skip blank content; prove the live shape reaches it.

    A whitespace-only upload is what an image-only scan looks like before OCR
    completes, so this is a real state, not a contrived one.
    """
    base, token = paperless_api
    cursor = current_added_watermark(base, token)
    # Seeded together so the call under test returns a mixed page: proving the
    # blank one is dropped means nothing if it is the only thing there.
    seed_document(
        base,
        token,
        filename=f"{tag}blank.txt",
        content=_body("blank.txt"),
        title=f"{tag}blank",
    )
    seed_document(
        base,
        token,
        filename=f"{tag}real.txt",
        content=tagged_text(f"{tag}real", _body("receipt.txt")),
        title=f"{tag}real",
    )
    wait_for_documents(base, token, tag=tag, expected=2)

    docs, auth_fail = poller.fetch_documents_page(since_cursor=cursor, page=1)

    assert auth_fail is False, "a blank document must not be reported as an auth failure"
    titles = {d.title for d in _mine(docs, tag)}
    assert titles == {f"{tag}real"}, (
        f"the blank document should have been skipped, not returned: {sorted(titles)}"
    )
