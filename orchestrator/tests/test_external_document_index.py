# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""`file_index` rows for externally-ingested documents (LUM-724 Phase 2).

Both external ingest paths wrote Qdrant chunks plus their own ledger and no
`file_index` row, so paperless documents and filed email attachments were
*searchable but not browsable* — and, because `purge_document` selects from
`file_index` and returns early when absent, a paperless document had **no delete
path at all**.

Three behaviours here are contracts rather than conveniences, and each one was
found by a review round rather than written first:

* the write is an **UPSERT with the partial index's arbiter predicate**, because
  three ordinary flows write the same logical path twice and a plain INSERT makes
  each a 500 (proven against real Postgres in the integration lane);
* `is_external_path` is a **prefix** test, so an uploaded file whose name happens
  to contain `://` is not misrouted; and
* the paperless write is **strict**, because it runs inside the ledger+cursor
  transaction where a swallowed failure would commit an advanced `poll_cursor`
  with no library row — permanently unrepairable, since the document is never
  re-polled.
"""

from __future__ import annotations

import pytest

import config
from services import external_document_index as ext

USER = "alice"
PAPERLESS = "paperless://src-uuid/documents/42"
ATTACHMENT = "email-attachment://src-uuid/sig-uuid/0"


class _Store:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.writes: list[tuple] = []
        self.deletes: list[tuple] = []
        self._next_id = 1

    def fetch_one(self, sql, params=None):
        if self.fail:
            raise RuntimeError("postgres is down")
        if sql.strip().upper().startswith("INSERT"):
            self.writes.append((sql, params))
            self._next_id += 1
            return {"id": self._next_id - 1}
        if sql.strip().upper().startswith("DELETE"):
            self.deletes.append(params)
            # `logical_path` is params[1]; pretend only known paths exist.
            return {"id": 1} if params[1] in (PAPERLESS, ATTACHMENT) else None
        raise AssertionError(f"unexpected statement: {sql!r}")


@pytest.fixture
def store(monkeypatch):
    def _install(**kw):
        st = _Store(**kw)
        monkeypatch.setattr(config, "get_metadata_store", lambda: st)
        return st

    return _install


# --- origin, derived from the path and never stored --------------------------


@pytest.mark.parametrize(
    "path,expected",
    [
        (PAPERLESS, "paperless"),
        (ATTACHMENT, "email"),
        ("/data/docs/invoice.pdf", "upload"),
        ("", "upload"),
    ],
)
def test_origin_is_derived_from_the_scheme(path, expected):
    """No `origin` column: the logical path is already the stable key that the
    Qdrant payload, the KG evidence rows and purge all use, so a stored column
    would be a second source of truth able to disagree with it."""
    assert ext.origin_of(path) == expected


@pytest.mark.parametrize("path", [PAPERLESS, ATTACHMENT])
def test_external_paths_are_recognised(path):
    assert ext.is_external_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "/data/docs/invoice.pdf",
        "/data/docs/notes about http://example.com.txt",
        "/data/weird/paperless://not-really.pdf",
        "",
    ],
)
def test_a_local_file_is_never_mistaken_for_an_external_one(path):
    """A **prefix** test, not a substring one.

    The third case is the one that matters: a genuine uploaded file whose *name*
    contains the scheme. Substring matching would route it down the paperless
    branch — refusing to share it, skipping its legacy purge ids, and badging it
    wrongly.
    """
    assert ext.is_external_path(path) is False


# --- file_type, matching what ingest_file stores -----------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("invoice.pdf", "pdf"),
        ("Scan 2026.PDF", "pdf"),
        ("archive.tar.gz", "gz"),
        ("noextension", ""),
        ("", ""),
        ("trailing.", ""),
    ],
)
def test_file_type_matches_ingest_file(name, expected):
    """`ingest_file` stores the bare extension. A second vocabulary here would
    split every consumer that groups or filters by type."""
    assert ext.file_type_for(name) == expected


# --- the write ---------------------------------------------------------------


def test_recording_writes_a_personal_unprojected_row(store):
    st = store()

    ext.record(
        user_id=USER,
        logical_path=PAPERLESS,
        file_hash="h1",
        file_type="pdf",
        chunk_count=3,
        display_name="Electricity bill",
    )

    sql, params = st.writes[0]
    assert "'personal', NULL" in sql, "external rows are never projections"
    assert params[0] == PAPERLESS
    assert params[-1] == "Electricity bill"


def test_the_upsert_carries_the_partial_indexs_arbiter_predicate(store):
    """**Not decorative.** `file_index_user_path_uniq` is PARTIAL (046), so
    without `WHERE published_from IS NULL` Postgres cannot infer the arbiter and
    the statement fails outright — proven against real Postgres in the
    integration lane."""
    st = store()

    ext.record(
        user_id=USER,
        logical_path=PAPERLESS,
        file_hash="h1",
        file_type="pdf",
        chunk_count=1,
        display_name="x",
    )

    sql = st.writes[0][0]
    assert "ON CONFLICT (user_id, file_path) WHERE published_from IS NULL" in sql


def test_a_second_write_updates_rather_than_doing_nothing(store):
    """`DO NOTHING` would leave a stale `chunk_count` after a re-ingest that
    wrote a different number of chunks, so the listing would disagree with
    Qdrant permanently."""
    st = store()

    ext.record(
        user_id=USER,
        logical_path=PAPERLESS,
        file_hash="h1",
        file_type="pdf",
        chunk_count=1,
        display_name="x",
    )

    sql = st.writes[0][0]
    assert "DO UPDATE SET" in sql
    assert "chunk_count = EXCLUDED.chunk_count" in sql
    assert "DO NOTHING" not in sql


def test_an_empty_display_name_is_stored_as_null_not_empty_string(store):
    """NULL means "derive from the path", which is right for an uploaded file.
    An empty string would render as a blank row title."""
    st = store()

    ext.record(
        user_id=USER,
        logical_path=PAPERLESS,
        file_hash="h",
        file_type="",
        chunk_count=0,
        display_name="",
    )

    assert st.writes[0][1][-1] is None


def test_a_failed_write_degrades_instead_of_failing_the_ingest(store):
    """The chunks are already in Qdrant, so the document is searchable. A missing
    row means it is not *listed* — the pre-LUM-724 status quo, repairable by the
    backfill script. Failing here would discard a successful ingest."""
    store(fail=True)

    assert (
        ext.record(
            user_id=USER,
            logical_path=ATTACHMENT,
            file_hash="h",
            file_type="pdf",
            chunk_count=1,
            display_name="invoice.pdf",
        )
        is None
    )


def test_strict_mode_raises_so_the_paperless_transaction_rolls_back(store):
    """The paperless caller writes INSIDE its ledger+cursor transaction.

    Swallowing a failure there would commit an advanced `poll_cursor` with no
    `file_index` row — and because the poll is `added__gt` strict, that document
    is **never re-read**. The library gap would be permanent. Raising rolls the
    whole transaction back so the next poll retries cleanly.
    """
    store(fail=True)

    with pytest.raises(RuntimeError):
        ext.record(
            user_id=USER,
            logical_path=PAPERLESS,
            file_hash="h",
            file_type="pdf",
            chunk_count=1,
            display_name="x",
            strict=True,
        )


# --- the share refusal, and why it is a pure predicate -----------------------


@pytest.mark.parametrize("path", [PAPERLESS, ATTACHMENT])
def test_sharing_an_external_document_is_refused(path):
    with pytest.raises(ValueError):
        ext.refuse_share_if_external(path)


def test_sharing_an_uploaded_document_is_untouched():
    ext.refuse_share_if_external("/data/docs/invoice.pdf")  # must not raise


def test_the_refusal_is_a_pure_predicate_with_no_store_dependency():
    """**Three publish doors**, in two route modules, one of which never reaches
    `share_document` at all. A pure function is callable from `services/` and
    from `routes/` alike without a `services/` → `routes/` import — which is what
    lets one guard cover all three."""
    import inspect

    src = inspect.getsource(ext.refuse_share_if_external)

    assert "get_metadata_store" not in src
    assert "import" not in src.split('"""')[-1], "no imports in the body"


def test_the_refusal_explains_itself():
    """A 409 with no reason reads as a bug. The message names the cause: the
    upstream system can delete the original, and a shared copy would outlive it."""
    assert "paperless" in ext.SHARE_REFUSAL.lower()
    assert len(ext.SHARE_REFUSAL) > 40


# --- removal -----------------------------------------------------------------


def test_removing_deletes_only_the_unprojected_row(store):
    st = store()

    assert ext.remove(user_id=USER, logical_path=PAPERLESS) is True

    assert st.deletes[0] == (USER, PAPERLESS)


def test_removing_a_missing_row_is_not_an_error(store):
    """Every caller — undo, purge, source teardown — reaches here on paths that
    may already have run."""
    store()

    assert ext.remove(user_id=USER, logical_path="paperless://s/documents/999") is False


def test_bulk_removal_reports_how_many_it_removed(store):
    store()

    removed = ext.remove_for_source(
        user_id=USER,
        logical_paths=[PAPERLESS, ATTACHMENT, "paperless://s/documents/999"],
    )

    assert removed == 2
