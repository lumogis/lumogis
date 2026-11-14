# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Seeding helpers for the live paperless fixture (LUM-309).

Drives paperless's own REST API to put documents *into* the fixture, so the
lane under test can then read them back out through ``PaperlessPoller``. These
are deliberately hand-rolled against the documented endpoints rather than
sharing code with ``orchestrator/adapters/paperless_source.py`` — a seeder that
reused the adapter's request layer could not detect a defect in it, which is
the entire reason this fixture exists.

**This module is a contract for LUM-725.** ``post_document`` as a *product*
feature (writing to paperless) is that ticket's job, and it inherits this
call's shape. Changing the signature later means changing LUM-725's foundation.

**Content must be unique per document.** Paperless rejects an upload whose
checksum matches an existing document, and the fixture container lives for the
whole session with documents accumulating across tests. Two tests seeding
byte-identical text would see the second upload silently never appear, and
``wait_for_documents`` would then fail on a timeout that looks like a slow
consumer. Every caller composes its per-test tag into the *content* as well as
the title; :func:`tagged_text` exists so nobody has to remember that.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

import httpx

#: Per-process nonce mixed into every test's tag.
#:
#: Without it the lane is not re-runnable against a container that is already
#: up. Tags are otherwise derived from the test name and so are identical
#: between runs; re-seeding then produces byte-identical content, paperless's
#: checksum dedup silently declines to create a second document, and the
#: surviving one was added *before* the new run's watermark — so an
#: ``added__gt`` query correctly returns nothing and every assertion fails on
#: an empty set.
#:
#: `make compose-test-paperless` always starts from `down -v`, so the gate
#: itself never hit this. A developer iterating locally with the fixture up hits
#: it on their second run, and the failure (empty results, no error) points
#: nowhere near the cause.
RUN_ID = uuid.uuid4().hex[:8]

#: Generous relative to Step 0's measured 4.25s for three documents. The
#: consumer is a background Celery worker, so a cold or loaded runner is
#: materially slower than the laptop this was measured on.
_DEFAULT_CONSUME_TIMEOUT_S = 120.0
_DEFAULT_POLL_INTERVAL_S = 2.0

#: Seeding is a fixture concern, not the thing under test — a hang here should
#: surface as a failure quickly rather than inheriting the adapter's ladder.
_REQUEST_TIMEOUT_S = 30.0


def tagged_text(tag: str, body: str) -> str:
    """Compose document content that is unique to ``tag``.

    See the module docstring: paperless dedupes by checksum, so the tag has to
    reach the *bytes*, not only the title.
    """
    return f"{tag}\n\n{body}"


def mint_token(base_url: str, user: str, password: str) -> str:
    """Exchange the per-run admin password for an API token.

    Step 0 measured this at 0.71s once the container is healthy.
    """
    resp = httpx.post(
        f"{base_url.rstrip('/')}/api/token/",
        json={"username": user, "password": password},
        timeout=_REQUEST_TIMEOUT_S,
    )
    resp.raise_for_status()
    token = resp.json().get("token", "")
    if not token:
        raise AssertionError(
            f"POST /api/token/ returned {resp.status_code} with no token field. "
            "The admin user may not have been created — check "
            "PAPERLESS_ADMIN_USER / PAPERLESS_ADMIN_PASSWORD in the compose env."
        )
    # Deliberately not logged or returned in any assertion message: the
    # Definition of done requires that no token reaches test output.
    return str(token)


def seed_document(
    base_url: str,
    token: str,
    *,
    filename: str,
    content: str,
    title: str | None = None,
) -> None:
    """Upload one document and return once paperless has *accepted* it.

    Returns nothing on purpose. ``POST /api/documents/post_document/`` responds
    with a **task id string**, not a document — the id is not threaded through
    because the seed contract is "the document exists when
    :func:`wait_for_documents` says so", not "this task finished". Consuming is
    asynchronous; a caller that treated this return as "the document is
    queryable" would race the consumer.
    """
    data: dict[str, str] = {}
    if title is not None:
        data["title"] = title
    resp = httpx.post(
        f"{base_url.rstrip('/')}/api/documents/post_document/",
        headers={"Authorization": f"Token {token}"},
        files={"document": (filename, content.encode("utf-8"), "text/plain")},
        data=data,
        timeout=_REQUEST_TIMEOUT_S,
    )
    resp.raise_for_status()


def wait_for_documents(
    base_url: str,
    token: str,
    *,
    tag: str,
    expected: int,
    timeout_s: float = _DEFAULT_CONSUME_TIMEOUT_S,
    poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S,
) -> list[dict[str, Any]]:
    """Block until ``expected`` documents whose title starts with ``tag`` exist.

    Filters on the tag rather than reading a global ``count``: the container is
    shared for the whole session and documents accumulate, so any assertion
    against a global total is order-dependent and false the moment another test
    seeds something.

    **Never returns a short list.** On timeout this calls ``pytest.fail`` naming
    the tag, the expected and observed counts, and the elapsed time. Returning
    a partial list would make every downstream assertion vacuous while still
    passing — which is precisely the failure mode this whole fixture was built
    to eliminate.
    """
    import pytest

    deadline = time.monotonic() + timeout_s
    observed = 0
    results: list[dict[str, Any]] = []

    while time.monotonic() < deadline:
        resp = httpx.get(
            f"{base_url.rstrip('/')}/api/documents/",
            headers={"Authorization": f"Token {token}"},
            params={
                "ordering": "added",
                "page_size": 100,
                "title__istartswith": tag,
            },
            timeout=_REQUEST_TIMEOUT_S,
        )
        resp.raise_for_status()
        results = list(resp.json().get("results", []))
        observed = len(results)
        if observed >= expected:
            return results
        time.sleep(poll_interval_s)

    pytest.fail(
        f"paperless consumed {observed} of {expected} documents tagged "
        f"{tag!r} within {timeout_s:.0f}s. The consumer is a background Celery "
        "worker; a zero here usually means it never started (check "
        "`docker compose logs paperless`), while a partial count means it is "
        "merely slow and the timeout needs raising.",
        pytrace=False,
    )
    raise AssertionError("unreachable")  # pragma: no cover - pytest.fail raises


#: The fixture's paperless container. Derived from
#: COMPOSE_PROJECT_NAME=lumogis-paperless-test in the Makefile target.
PAPERLESS_CONTAINER_ENV = "LUMOGIS_PAPERLESS_CONTAINER"
_DEFAULT_CONTAINER = "lumogis-paperless-test-paperless-1"


def force_equal_added(document_ids: list[int], *, container: str | None = None) -> str:
    """Stamp ``document_ids`` with one byte-identical ``added``, in paperless's own DB.

    Returns the timestamp they now share.

    **Why this reaches past the REST API, which nothing else here does.**
    ``added`` is genuinely read-only over REST on paperless 3.0.4. The server's
    own OpenAPI schema lists the writable document fields and ``added`` is not
    among them (``created`` is). A ``PATCH {"added": ...}`` still answers **200**
    — DRF serialises and returns the object having silently ignored the
    read-only field — so a spike that checks only the status code concludes the
    field is writable. That is exactly what LUM-309's Step 0 Obs 7 did, and the
    plan reversed a correct earlier decision on the strength of it. Verified
    here by PATCHing and reading back: the value does not move.

    **Why doing it this way is legitimate rather than a cheat.** This
    manufactures a *precondition*, not a result. The code under test still polls
    the real REST API and sees whatever the server reports; all this does is put
    the server into a state a real bulk import genuinely produces — paperless
    stamps identical ``added`` values when a batch is consumed in the same
    instant, which is the precise scenario ADR 062 exists for. The alternative
    is a test that claims to cover the equal-timestamp case while quietly
    exercising the ordinary distinct-timestamp one.

    Requires the fixture container (``make compose-test-paperless``); it is not
    reachable from a lane that only has an HTTP endpoint.
    """
    import subprocess

    import pytest

    if not document_ids:
        raise ValueError("force_equal_added requires at least one document id")
    ids = [int(d) for d in document_ids]  # ints only — these are interpolated into SQL
    name = container or os.environ.get(PAPERLESS_CONTAINER_ENV, "").strip() or _DEFAULT_CONTAINER
    id_list = ",".join(str(i) for i in ids)

    script = (
        "import sqlite3;"
        "c=sqlite3.connect('/usr/src/paperless/data/db.sqlite3');"
        f"t=c.execute('select added from documents_document where id={ids[0]}').fetchone()[0];"
        f"c.execute('update documents_document set added=? where id in ({id_list})',(t,));"
        "c.commit();"
        "print(t)"
    )
    try:
        out = subprocess.run(
            ["docker", "exec", name, "python3", "-c", script],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.fail(
            f"could not stamp an equal `added` via container {name!r}: {exc}. "
            "This case needs the compose fixture, not merely an HTTP endpoint — "
            f"set {PAPERLESS_CONTAINER_ENV} if the container is named differently.",
            pytrace=False,
        )
    return out.stdout.strip()


def current_added_watermark(base_url: str, token: str) -> str | None:
    """Return the newest ``added`` currently in the fixture, or ``None`` if empty.

    This is how a test scopes itself to *its own* documents. The container is
    shared for the whole session and documents accumulate, so a test that read
    page 1 unfiltered would mostly see other tests' documents — and because
    ``ordering=added`` is ascending, its own freshly-seeded ones would be on the
    *last* page, not the first.

    Deliberately server-side rather than ``datetime.now(timezone.utc)``: the
    timestamps being compared are stamped by paperless, and any clock skew
    between the host and the container would make a host-generated cursor
    either drop the test's own documents or admit its predecessors'. Reading
    the server's own maximum has no such failure mode.
    """
    resp = httpx.get(
        f"{base_url.rstrip('/')}/api/documents/",
        headers={"Authorization": f"Token {token}"},
        params={"ordering": "-added", "page_size": 1},
        timeout=_REQUEST_TIMEOUT_S,
    )
    resp.raise_for_status()
    results = resp.json().get("results", [])
    if not results:
        return None
    return str(results[0]["added"])


def patch_document(
    base_url: str,
    token: str,
    *,
    document_id: int,
    fields: dict[str, Any],
) -> dict[str, Any]:
    """PATCH a consumed document and return the updated representation.

    Used to manufacture the equal-``added`` precondition that ADR 062 exists
    for. Step 0 Obs 7 established that ``added`` is read-only *on upload* but
    writable by PATCH *after* consume (HTTP 200) — which is what makes
    ``test_documents_sharing_an_added_timestamp_survive_a_page_boundary``
    provable through the API rather than needing SQLite surgery.
    """
    resp = httpx.patch(
        f"{base_url.rstrip('/')}/api/documents/{document_id}/",
        headers={"Authorization": f"Token {token}"},
        json=fields,
        timeout=_REQUEST_TIMEOUT_S,
    )
    resp.raise_for_status()
    return dict(resp.json())
