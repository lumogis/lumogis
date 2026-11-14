# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""External documents are browsable, deletable, and never bare integers.

Two contracts, and both were found by review rather than written first.

**Three renderers derive a display name, not one.** `list_documents`,
`get_document` and the memory-search hit builder each computed
`Path(file_path).name` independently. For a logical path that yields `42` or
`0`, and a library where every external document is an integer is not browsable
— the entire point of LUM-724. Each review round found one more renderer, so
these tests cover all three by name.

**Purge marks the verdict row; it must never delete it.** Deleting it returns
the part to `_CANDIDATES_SQL` (`AND l.id IS NULL`) and a matching filing rule
re-files it on the next tick — the purge silently undoes itself. The row carries
two orthogonal facts, and using one signal for both is what defeated three
earlier designs.
"""

from __future__ import annotations

import pytest

import config
from services import document_purge
from services import documents as doc_svc

USER = "alice"
PAPERLESS = "paperless://src-uuid/documents/42"
ATTACHMENT = "email-attachment://src-uuid/sig-uuid/0"
UPLOAD = "/data/docs/invoice.pdf"


# --- the display name, across every surface that renders one ----------------


@pytest.mark.parametrize(
    "path,stored,expected",
    [
        (PAPERLESS, "Electricity bill", "Electricity bill"),
        (ATTACHMENT, "invoice.pdf", "invoice.pdf"),
        (UPLOAD, None, "invoice.pdf"),
        (UPLOAD, "", "invoice.pdf"),
    ],
)
def test_the_stored_name_wins_and_uploads_are_unchanged(path, stored, expected):
    """NULL means "derive from the path", which is correct for every uploaded
    file — so no backfill is needed for those to stay right."""
    assert doc_svc.display_name_for({"file_path": path, "display_name": stored}) == expected


@pytest.mark.parametrize("path", [PAPERLESS, ATTACHMENT])
def test_an_external_document_is_never_rendered_as_a_bare_number(path):
    """**The regression this feature exists to prevent.**

    Without a stored name, `Path(...).name` gives `42` and `0`. Asserted against
    the digit-only shape rather than a specific string, so any future scheme
    whose last segment is an id is caught too.
    """
    name = doc_svc.display_name_for({"file_path": path, "display_name": "Electricity bill"})

    assert not name.isdigit(), f"{path} rendered as a bare number: {name!r}"


def test_a_missing_display_name_key_does_not_crash_the_renderer():
    """Rows come from several SELECTs; one that has not been widened yet must
    degrade to the old behaviour rather than raising."""
    assert doc_svc.display_name_for({"file_path": UPLOAD}) == "invoice.pdf"


class _NameStore:
    def __init__(self, stored=None):
        self.stored = stored
        self.queries = 0

    def fetch_one(self, sql, params=None):
        self.queries += 1
        return {"display_name": self.stored} if self.stored is not None else None


def test_a_search_hit_resolves_an_external_name_from_the_row(monkeypatch):
    st = _NameStore("Electricity bill")
    monkeypatch.setattr(config, "get_metadata_store", lambda: st)

    assert doc_svc.display_name_for_path(USER, PAPERLESS) == "Electricity bill"


def test_a_search_hit_for_an_UPLOAD_never_queries(monkeypatch):
    """One database round trip per search hit, to learn a basename we already
    have, would be absurd. Short-circuited on the scheme."""
    st = _NameStore("unused")
    monkeypatch.setattr(config, "get_metadata_store", lambda: st)

    assert doc_svc.display_name_for_path(USER, UPLOAD) == "invoice.pdf"
    assert st.queries == 0


def test_a_search_hit_with_no_row_still_avoids_a_bare_number(monkeypatch):
    """A readable fallback beats `42` even when the row is missing entirely."""
    st = _NameStore(None)
    monkeypatch.setattr(config, "get_metadata_store", lambda: st)

    name = doc_svc.display_name_for_path(USER, PAPERLESS)

    assert not name.isdigit()
    assert "42" in name


def test_a_failed_name_lookup_never_breaks_search(monkeypatch):
    class _Broken:
        def fetch_one(self, sql, params=None):
            raise RuntimeError("postgres is down")

    monkeypatch.setattr(config, "get_metadata_store", lambda: _Broken())

    assert doc_svc.display_name_for_path(USER, PAPERLESS)  # non-empty, no raise


# --- purge marks the ledger; it must never delete it -------------------------


class _MarkStore:
    def __init__(self):
        self.statements: list[tuple] = []

    def execute(self, sql, params=None):
        self.statements.append((" ".join(sql.split()), params))

    def sql(self) -> str:
        return " || ".join(s for s, _ in self.statements)


def test_purging_an_attachment_sets_both_columns_in_one_statement():
    """**The whole design.**

    `reversed_at` frees the content-hash slot, because
    `uq_email_attachment_ingest_active` is partial on `reversed_at IS NULL AND
    skipped_reason IS NULL` — without it the purged row occupies that slot
    forever and every later message carrying the same bytes is silently recorded
    as a duplicate, with no recovery.

    `purged_at` is read only by application code, and is what stops `refile_row`
    offering to restore a deliberate deletion.

    ONE statement: as two, a crash between them leaves a row indistinguishable
    from an ordinary undo.
    """
    st = _MarkStore()

    document_purge._mark_external_ledger_purged(st, user_id=USER, file_path=ATTACHMENT)

    assert len(st.statements) == 1, "two statements leave a crash window"
    sql, params = st.statements[0]
    assert sql.startswith("UPDATE email_attachment_ingest_log")
    assert "reversed_at = COALESCE(reversed_at, NOW())" in sql
    assert "purged_at = NOW()" in sql
    assert "DELETE" not in sql, "deleting re-arms the filing rules"
    assert params == (USER, ATTACHMENT)


def test_an_existing_undo_timestamp_is_preserved():
    """`COALESCE`: purging something the user had already undone must not rewrite
    when they undid it."""
    st = _MarkStore()

    document_purge._mark_external_ledger_purged(st, user_id=USER, file_path=ATTACHMENT)

    assert "COALESCE(reversed_at, NOW())" in st.statements[0][0]


def test_purging_a_paperless_document_sets_only_purged_at():
    """`external_documents` has no `reversed_at` column at all (024) and no
    content-slot index to free, so naming it here would be unrunnable SQL."""
    st = _MarkStore()

    document_purge._mark_external_ledger_purged(st, user_id=USER, file_path=PAPERLESS)

    sql, params = st.statements[0]
    assert sql.startswith("UPDATE external_documents")
    assert "reversed_at" not in sql
    assert "purged_at = NOW()" in sql


def test_purging_an_uploaded_document_touches_no_ledger():
    st = _MarkStore()

    document_purge._mark_external_ledger_purged(st, user_id=USER, file_path=UPLOAD)

    assert st.statements == []


def test_marking_is_idempotent():
    """`purged_at IS NULL` in the WHERE: a retried purge must not move the
    timestamp, or the tombstone retry would rewrite history on every attempt."""
    st = _MarkStore()

    document_purge._mark_external_ledger_purged(st, user_id=USER, file_path=ATTACHMENT)

    assert "purged_at IS NULL" in st.statements[0][0]


# --- sharing: THREE doors, and one test per door -----------------------------
#
# Round 2 found that the guard sat in `share_document`, which the scope route
# calls only ABOVE the inline-chunk threshold — external documents are typically
# small, so the common path was unguarded. Round 3 then found a THIRD door,
# `POST /api/v1/documents/{id}/publish`, which has no size branch at all and is
# the one the web ShareToggle actually calls.
#
# A single test on any one door passes against both broken designs. That is why
# there are three.


def test_door_1_share_document_refuses_an_external_row(monkeypatch):
    class _S:
        def fetch_one(self, sql, params=None):
            return {"id": 1, "file_path": PAPERLESS}

    monkeypatch.setattr(config, "get_metadata_store", lambda: _S())

    with pytest.raises(ValueError):
        doc_svc.share_document(USER, 1)


def test_door_1_still_shares_an_uploaded_document(monkeypatch):
    """The guard must not become a blanket refusal."""
    enqueued: list = []

    class _S:
        def fetch_one(self, sql, params=None):
            return {"id": 1, "file_path": UPLOAD}

    monkeypatch.setattr(config, "get_metadata_store", lambda: _S())
    monkeypatch.setattr(
        doc_svc, "_enqueue_share_job", lambda *a, **k: enqueued.append(a) or "queued"
    )

    assert doc_svc.share_document(USER, 1) == "queued"


def test_door_2_and_3_the_scope_route_checks_before_the_size_branch():
    """Structural: the refusal must appear ahead of the threshold test in the
    source, or the small-document path never reaches it."""
    import inspect

    from routes import scope as scope_mod

    src = inspect.getsource(scope_mod.publish_file)
    guard = src.index("refuse_share_if_external")
    branch = src.index("get_share_inline_max_chunks")

    assert guard < branch, (
        "the guard must precede the size branch — below the threshold the route "
        "projects inline and never calls share_document"
    )


def test_door_3_maps_the_refusal_to_409_not_500():
    """`share_document` raises `ValueError`; without an explicit mapping the
    api_v1 route would surface the refusal as a server error."""
    import inspect

    from routes.api_v1 import documents as doc_routes

    src = inspect.getsource(doc_routes.publish_document)

    assert "except ValueError" in src
    assert "HTTP_409_CONFLICT" in src


# --- the paperless title, end to end -----------------------------------------


def test_the_adapter_exposes_the_paperless_title():
    """Without this the library shows "Paperless document 42" for everything.

    Paperless always has a title — it defaults to the original filename — so the
    fallback should be a genuine last resort, not the normal case.
    """
    from adapters.paperless_source import PaperlessDocument

    doc = PaperlessDocument(id=42, content="text", added="2026-07-01", title="Electricity bill")

    assert doc.title == "Electricity bill"


def test_a_response_without_a_title_still_yields_a_document():
    """Defaulted, so an older or trimmed paperless response degrades to the
    constructed fallback rather than dropping the document entirely."""
    from adapters.paperless_source import PaperlessDocument

    assert PaperlessDocument(id=42, content="t", added="2026-07-01").title == ""


def test_the_ingest_prefers_the_real_title_over_the_fallback(monkeypatch):
    """`title or f"Paperless document {id}"` — asserted so a later refactor
    cannot quietly invert the precedence and make every row a fallback."""
    import inspect

    from services import ingest

    src = inspect.getsource(ingest.ingest_external_document)

    assert 'display_name=(title or "").strip()' in src
    assert "Paperless document" in src
