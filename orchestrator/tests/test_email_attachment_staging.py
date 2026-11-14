# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Outbound attachment staging — ownership, quota, and the proposal-state sweep (LUM-695).

The sweep is the subtle half. ``action_proposals`` rows **never expire**, so a
wall-clock TTL over staged files would delete the attachments of a proposal
staged on Friday and approved on Monday — deterministically, every weekend,
surfacing as "attachment no longer available" on a message the user wrote and
approved themselves. Sweeping keys on **proposal state** instead:

* live (``pending``/``approved``/``claimed``/``executing``) → never swept, however old
* unreferenced (``proposal_id IS NULL``) → swept after the TTL; this is the real leak
* terminal (``done``/``rejected``/``archived``/``dead``) → swept after
  ``EMAIL_ATTACHMENT_DEAD_GRACE_HOURS`` from the proposal's ``finished_at`` (LUM-793:
  7 days, so a failed send listed for recovery still has its attachments)

Those state names are the ones migration 022 actually CHECKs. Inventing names
(``executed``, ``cancelled``) is how a sweep silently matches nothing and the
leak it exists to close stays open, so a test pins them against the migration.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import config
from services import email_attachment_staging as staging

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "postgres"
    / "migrations"
    / "022-action-proposals-claim.sql"
)


class _Store:
    """A tiny in-memory stand-in for the metadata store's row semantics."""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.proposals: dict[int, str] = {}
        self.executed: list[tuple[str, tuple]] = []

    # -- the two shapes the service uses ---------------------------------
    def fetch_one(self, sql, params=None):
        self.executed.append((sql, params or ()))
        if "SELECT COALESCE(SUM(size_bytes)" in sql:
            total = sum(
                r["size_bytes"]
                for r in self.rows
                if r["user_id"] == params[0] and r["consumed_at"] is None
            )
            return {"total": total}
        if "SELECT id, filename, mime_type, storage_key" in sql:
            for r in self.rows:
                if r["id"] == params[0] and r["user_id"] == params[1] and r["consumed_at"] is None:
                    return dict(r)
            return None
        return None

    def fetch_all(self, sql, params=None):
        self.executed.append((sql, params or ()))
        if "FROM email_attachment_staged s" in sql:  # the sweep
            return [
                dict(r)
                for r in self.rows
                if r["consumed_at"] is None and self._sweepable(r, params)
            ]
        if "WHERE id::text = ANY(%s) AND user_id = %s" in sql:  # discard
            return [
                dict(r) for r in self.rows if r["id"] in params[0] and r["user_id"] == params[1]
            ]
        if "WHERE proposal_id = %s" in sql:  # discard_for_proposal
            return [dict(r) for r in self.rows if r["proposal_id"] == params[0]]
        return []

    def execute(self, sql, params=None):
        self.executed.append((sql, params or ()))
        if "INSERT INTO email_attachment_staged" in sql:
            self.rows.append(
                {
                    "id": params[0],
                    "user_id": params[1],
                    "filename": params[2],
                    "mime_type": params[3],
                    "size_bytes": params[4],
                    "storage_key": params[5],
                    "proposal_id": None,
                    "consumed_at": None,
                    "age_hours": 0.0,
                }
            )
        elif "SET proposal_id" in sql:
            for r in self.rows:
                if r["id"] in params[1] and r["user_id"] == params[2]:
                    r["proposal_id"] = params[0]
        elif "SET consumed_at" in sql:
            for r in self.rows:
                if r["id"] in params[0]:
                    r["consumed_at"] = "now"
        elif "DELETE FROM email_attachment_staged" in sql:
            self.rows = [r for r in self.rows if r["id"] not in params[0]]
        return None

    # -- sweep semantics, mirrored so the service's SQL is exercised ------
    def _sweepable(self, row, params) -> bool:
        """Mirror of the sweep predicate. LUM-793 added the terminal GRACE arm.

        Worth stating why this mirror had to be updated rather than left alone:
        it does not read the SQL, it re-implements it, so when LUM-793 gave
        terminal proposals a 7-day grace **every test below still passed** while
        asserting the opposite of what the service now does. A fake that
        re-implements logic cannot fail on a predicate change — the real
        behaviour is pinned in the Postgres lane
        (`tests/integration/test_recoverable_sends_live.py`), and this exists so
        the unit tests describe the shipped rule instead of a retired one.
        """
        ttl_hours = params[0] if params else 24
        grace_hours = params[2] if params and len(params) > 2 else 168
        pid = row["proposal_id"]
        if pid is None:
            return row["age_hours"] >= ttl_hours
        if self.proposals.get(pid) not in staging.TERMINAL_PROPOSAL_STATES:
            return False
        # `finished_at` is modelled by the same `age_hours` dial the TTL uses:
        # nothing in this fake distinguishes when the file was staged from when
        # its proposal finished, and the tests never need them to differ.
        return row["age_hours"] >= grace_hours


@pytest.fixture
def store(monkeypatch):
    s = _Store()
    monkeypatch.setattr(config, "get_metadata_store", lambda: s)
    return s


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(staging, "_staging_root", lambda: tmp_path / "email-staging")
    return tmp_path / "email-staging"


# ---------------------------------------------------------------------------
# Staging + resolution
# ---------------------------------------------------------------------------


def test_stage_writes_the_bytes_and_returns_a_reference(store, root):
    out = staging.stage("u1", "invoice.pdf", "application/pdf", b"PDFBYTES")

    assert out.filename == "invoice.pdf"
    assert out.size_bytes == len(b"PDFBYTES")
    resolved = staging.resolve("u1", out.id)
    assert resolved is not None
    assert resolved.path.read_bytes() == b"PDFBYTES"


def test_the_on_disk_leaf_is_server_controlled_never_the_client_filename(store, root):
    """No client string is ever a path component.

    This eliminates the Unicode-normalisation / case-folding-collision /
    reserved-name class outright rather than hoping a sanitiser caught it — the
    shipped capture path takes the same posture (``leaf_filename_for_mime``).
    """
    out = staging.stage("u1", "../../etc/passwd", "application/pdf", b"X")

    resolved = staging.resolve("u1", out.id)
    assert resolved is not None
    parts = resolved.path.relative_to(root).parts
    assert parts[-1] == "blob", f"leaf must be the server-chosen 'blob', got {parts[-1]!r}"
    assert "passwd" not in str(resolved.path)
    assert ".." not in parts
    # The display name still travels — it is just never a path component.
    assert resolved.filename and "/" not in resolved.filename


def test_staged_file_not_readable_by_another_user(store, root):
    out = staging.stage("u1", "secret.pdf", "application/pdf", b"MINE")

    assert staging.resolve("u2", out.id) is None
    assert staging.resolve("u1", out.id) is not None


def test_resolve_returns_none_for_an_unknown_id(store, root):
    assert staging.resolve("u1", "no-such-id") is None


def test_discard_removes_the_row_and_the_file(store, root):
    out = staging.stage("u1", "a.pdf", "application/pdf", b"X")
    path = staging.resolve("u1", out.id).path

    staging.discard("u1", [out.id])

    assert not path.exists()
    assert staging.resolve("u1", out.id) is None


# ---------------------------------------------------------------------------
# Caps
# ---------------------------------------------------------------------------


def test_staging_rejects_oversize(store, root, monkeypatch):
    monkeypatch.setattr(staging, "_max_attachment_bytes", lambda: 10)

    with pytest.raises(staging.AttachmentTooLargeError):
        staging.stage("u1", "big.pdf", "application/pdf", b"x" * 11)


def test_staging_quota_refuses_before_writing(store, root, monkeypatch):
    """The disk-exhaustion regression.

    The TTL bounds a file's LIFETIME, not the RATE files arrive. Without a quota
    an authenticated user looping 25 MB uploads parks well over a terabyte
    inside one TTL window, every byte "legitimately" pending deletion — and if
    staging shares a filesystem with captures, disk-full breaks those too.
    """
    monkeypatch.setattr(staging, "_quota_bytes", lambda: 100)
    staging.stage("u1", "a.pdf", "application/pdf", b"x" * 80)
    before = list(root.rglob("blob")) if root.exists() else []

    with pytest.raises(staging.StagingQuotaExceededError):
        staging.stage("u1", "b.pdf", "application/pdf", b"x" * 80)

    after = list(root.rglob("blob")) if root.exists() else []
    assert after == before, "the quota must refuse BEFORE anything is written to disk"


def test_quota_is_per_user(store, root, monkeypatch):
    monkeypatch.setattr(staging, "_quota_bytes", lambda: 100)
    staging.stage("u1", "a.pdf", "application/pdf", b"x" * 80)

    # u2 has staged nothing, so u1's usage must not refuse them.
    assert staging.stage("u2", "b.pdf", "application/pdf", b"x" * 80)


# ---------------------------------------------------------------------------
# The sweep — keyed on proposal STATE, not wall clock
# ---------------------------------------------------------------------------


def test_the_sweep_state_sets_match_the_real_migration_022_check() -> None:
    """Invented state names are how a sweep silently matches nothing."""
    sql = _MIGRATION.read_text(encoding="utf-8")
    flags = re.IGNORECASE | re.DOTALL
    match = re.search(r"status[^\n]*CHECK\s*\(\s*status\s+IN\s*\(([^)]*)\)", sql, flags)
    if match is None:
        match = re.search(r"CHECK\s*\(\s*status\s+IN\s*\(([^)]*)\)", sql, flags)
    assert match, "could not find the status CHECK in migration 022"
    real = {s.strip().strip("'\"") for s in match.group(1).split(",") if s.strip()}

    assert staging.LIVE_PROPOSAL_STATES | staging.TERMINAL_PROPOSAL_STATES == real
    assert not (staging.LIVE_PROPOSAL_STATES & staging.TERMINAL_PROPOSAL_STATES)


def test_live_proposal_states_all_exempt_from_sweep() -> None:
    assert staging.LIVE_PROPOSAL_STATES == {"pending", "approved", "claimed", "executing"}


def test_terminal_proposal_states_are_the_rest() -> None:
    assert staging.TERMINAL_PROPOSAL_STATES == {"done", "rejected", "archived", "dead"}


def test_pending_proposal_attachments_are_never_swept(store, root, monkeypatch):
    """Staged 30 days ago, proposal still ``pending`` → the file survives.

    Proposals do not expire, so neither may their attachments.
    """
    monkeypatch.setattr(staging, "_ttl_hours", lambda: 24)
    out = staging.stage("u1", "a.pdf", "application/pdf", b"X")
    staging.bind_to_proposal("u1", [out.id], proposal_id=7)
    store.proposals[7] = "pending"
    store.rows[0]["age_hours"] = 24 * 30

    swept = staging.sweep()

    assert swept == 0
    assert staging.resolve("u1", out.id) is not None


def test_unreferenced_staged_file_is_swept_after_ttl(store, root, monkeypatch):
    """The actual leak: a composer abandoned before Review."""
    monkeypatch.setattr(staging, "_ttl_hours", lambda: 24)
    out = staging.stage("u1", "a.pdf", "application/pdf", b"X")
    path = staging.resolve("u1", out.id).path
    store.rows[0]["age_hours"] = 25

    swept = staging.sweep()

    assert swept == 1
    assert not path.exists()


def test_unreferenced_staged_file_survives_inside_the_ttl(store, root, monkeypatch):
    monkeypatch.setattr(staging, "_ttl_hours", lambda: 24)
    out = staging.stage("u1", "a.pdf", "application/pdf", b"X")
    store.rows[0]["age_hours"] = 1

    assert staging.sweep() == 0
    assert staging.resolve("u1", out.id) is not None


@pytest.mark.parametrize("state", sorted(staging.TERMINAL_PROPOSAL_STATES))
def test_terminal_proposal_attachments_are_swept_after_the_grace(store, root, monkeypatch, state):
    """LUM-793 replaced "swept promptly" with "swept after the grace".

    Renamed rather than edited in place: the old name asserted the rule the
    service no longer follows, and a stale name on a passing test is how the
    next reader learns something untrue.
    """
    monkeypatch.setattr(staging, "_ttl_hours", lambda: 24)
    monkeypatch.setattr(staging, "dead_grace_hours", lambda: 168)
    out = staging.stage("u1", "a.pdf", "application/pdf", b"X")
    staging.bind_to_proposal("u1", [out.id], proposal_id=9)
    store.proposals[9] = state
    store.rows[0]["age_hours"] = 24 * 8  # past the 7-day grace

    assert staging.sweep() == 1, f"{state} should sweep once the grace has passed"


@pytest.mark.parametrize("state", sorted(staging.TERMINAL_PROPOSAL_STATES))
def test_terminal_proposal_attachments_survive_inside_the_grace(store, root, monkeypatch, state):
    """The window LUM-793 needs: a failed send is listed for recovery for 30
    days, and offering "Save to Drafts with your attachments" an hour after the
    files were deleted would be a lie."""
    monkeypatch.setattr(staging, "_ttl_hours", lambda: 24)
    monkeypatch.setattr(staging, "dead_grace_hours", lambda: 168)
    out = staging.stage("u1", "a.pdf", "application/pdf", b"X")
    staging.bind_to_proposal("u1", [out.id], proposal_id=10)
    store.proposals[10] = state
    store.rows[0]["age_hours"] = 24  # one day in

    assert staging.sweep() == 0, f"{state} inside the grace must keep its files"


@pytest.mark.parametrize("state", sorted(staging.LIVE_PROPOSAL_STATES))
def test_every_live_state_is_exempt(store, root, monkeypatch, state):
    monkeypatch.setattr(staging, "_ttl_hours", lambda: 24)
    out = staging.stage("u1", "a.pdf", "application/pdf", b"X")
    staging.bind_to_proposal("u1", [out.id], proposal_id=11)
    store.proposals[11] = state
    store.rows[0]["age_hours"] = 24 * 365

    assert staging.sweep() == 0, f"{state} is live; its attachments must never be swept"


def test_bind_to_proposal_refuses_an_id_owned_by_another_user(store, root):
    out = staging.stage("u1", "a.pdf", "application/pdf", b"X")

    with pytest.raises(staging.StagedAttachmentUnavailable):
        staging.bind_to_proposal("u2", [out.id], proposal_id=3)


def test_bind_to_proposal_refuses_an_unknown_id(store, root):
    with pytest.raises(staging.StagedAttachmentUnavailable):
        staging.bind_to_proposal("u1", ["no-such-id"], proposal_id=3)
