# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Outbound attachment staging — disk, ownership, quota, sweep (LUM-695).

Attachment **bytes never enter the action payload**. ``action_proposals.payload``
is JSONB, is returned verbatim by ``PendingProposalItem``, and is rendered on the
approval card; a base64 blob there would bloat the row, cross the wire twice, and
land in a UI that expects a summary. So the composer stages bytes here, the
payload carries **references**, and the send handlers resolve them at execute
time. The approval card then shows "3 files, 1.2 MB" — the useful thing.

Two invariants worth stating plainly, because both are the kind that fail
silently rather than loudly:

**The on-disk leaf is server-controlled** — ``{staged_id}/blob``, never the
client's filename. The display name lives in the ``filename`` column and reaches
only the outgoing MIME part and the UI. This removes the
Unicode-normalisation / case-folding-collision / reserved-name class outright
rather than trusting a sanitiser to have caught every case; the shipped capture
path takes the same posture (``media_storage.leaf_filename_for_mime``).

**The sweep keys on PROPOSAL STATE, not wall clock.** ``action_proposals`` rows
never expire. A flat TTL would therefore delete the attachments of a proposal
staged on Friday and approved on Monday — deterministically, every weekend. See
:func:`sweep`.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from typing import Optional
from typing import Sequence

from services.media_storage import _reject_unsafe_path_component

import config
from services import email_attachments

_log = logging.getLogger(__name__)

# The real states from migration 022 (`022-action-proposals-claim.sql`). These
# are pinned by a test that reads the migration's CHECK constraint: inventing a
# state name is how a sweep silently matches nothing and the leak it exists to
# close stays open.
LIVE_PROPOSAL_STATES: frozenset[str] = frozenset({"pending", "approved", "claimed", "executing"})
TERMINAL_PROPOSAL_STATES: frozenset[str] = frozenset({"done", "rejected", "archived", "dead"})

# Bounded per tick so one sweep cannot hold a connection for an unbounded time.
_SWEEP_BATCH = 500

# Hourly, mirroring how feed_monitor schedules its polls.
SWEEP_INTERVAL_SECONDS = 3600


class StagingError(RuntimeError):
    """Base for staging failures the route maps to a status code."""


class AttachmentTooLargeError(StagingError):
    """One file exceeds the per-attachment cap (→ 413)."""


class StagingQuotaExceededError(StagingError):
    """The user's UNCONSUMED staged bytes would exceed their quota (→ 413)."""


class StagingWriteError(StagingError):
    """The bytes could not be written (disk full, permissions) (→ 507)."""


class StagedAttachmentUnavailable(StagingError):
    """A referenced staged id is missing, consumed, or another user's.

    At **propose** time the route turns this into a 422. At **execute** time
    there is no HTTP surface — the handler runs via the executor after approval
    — so it becomes a ``PermanentActionError`` and the proposal dead-letters
    like any other send failure.
    """


@dataclass(frozen=True)
class StagedAttachment:
    """The wire shape: a reference plus what the approval card needs to show."""

    id: str
    filename: str
    mime_type: str
    size_bytes: int


@dataclass(frozen=True)
class ResolvedStagedAttachment:
    """A staged file located on disk, ready to attach to an outgoing message."""

    id: str
    filename: str
    mime_type: str
    path: Path


# ---------------------------------------------------------------------------
# Configuration — read at call time so a test (or an operator restart) can
# change them without re-importing the module.
# ---------------------------------------------------------------------------


def _staging_root() -> Path:
    """Where staged bytes live.

    Default ``/var/lib/lumogis/email-staging``, deliberately **outside**
    ``/data``: ``docker-compose.yml`` mounts ``${FILESYSTEM_ROOT}:/data:ro``
    (so nothing could be written there) *and* sets ``FILESYSTEM_ROOT=/data``,
    making it the folder-watcher's root — a staged file placed there would be
    ingested as a new document.
    """
    return Path(os.environ.get("EMAIL_ATTACHMENT_STAGING_ROOT", "/var/lib/lumogis/email-staging"))


def _ttl_hours() -> int:
    """How long an **unreferenced** staged file survives.

    Files referenced by a live proposal are exempt however old — see :func:`sweep`.
    """
    try:
        return int(os.environ.get("EMAIL_ATTACHMENT_STAGING_TTL_HOURS", "24"))
    except ValueError:
        return 24


def dead_grace_hours() -> int:
    """How long a **terminal** proposal's files survive before the sweep takes them.

    LUM-793 needs this window to be non-zero: making failure terminal (LUM-762)
    put ``dead`` into :data:`TERMINAL_PROPOSAL_STATES`, so a failed send's
    attachments were swept on the next hourly tick — about an hour after the
    failure — while the recovery surface lists the message for 30 days.

    **This is a bounded extension for TERMINAL rows only.** It is emphatically
    not a return to the pre-LUM-762 behaviour where a live proposal's files were
    pinned forever: ``pending``/``approved``/``claimed``/``executing`` are
    untouched by this and are still never swept on age. The leak LUM-762 closed
    was unreferenced files with no proposal; that rule is also untouched.
    """
    try:
        return int(os.environ.get("EMAIL_ATTACHMENT_DEAD_GRACE_HOURS", "168"))
    except ValueError:
        return 168


def _quota_bytes() -> int:
    """Per-user cap on UNCONSUMED staged bytes."""
    try:
        return int(os.environ.get("EMAIL_ATTACHMENT_STAGING_QUOTA_MB", "200")) * 1024 * 1024
    except ValueError:
        return 200 * 1024 * 1024


def _max_attachment_bytes() -> int:
    return email_attachments.max_attachment_bytes()


# ---------------------------------------------------------------------------
# Stage / resolve / discard
# ---------------------------------------------------------------------------


def stage(user_id: str, filename: str, content_type: str, data: bytes) -> StagedAttachment:
    """Write ``data`` to the staging root and index it. Owner-scoped.

    Raises :class:`AttachmentTooLargeError` (per-file cap) or
    :class:`StagingQuotaExceededError` (per-user unconsumed total) **before**
    anything is written — a quota that refuses after the write is not a quota.
    """
    size = len(data)
    if size > _max_attachment_bytes():
        raise AttachmentTooLargeError(f"attachment exceeds {_max_attachment_bytes()} byte limit")

    quota = _quota_bytes()
    if _unconsumed_bytes(user_id) + size > quota:
        raise StagingQuotaExceededError(
            f"staged bytes would exceed the {quota} byte per-user quota"
        )

    display_name = email_attachments.sanitise_filename(filename)
    mime = (content_type or "application/octet-stream").strip().lower()

    # SERVER-minted id, so the storage key is known before the row exists and
    # the write is a single statement. Minting it here rather than letting
    # Postgres default it changes nothing about who controls it — the client
    # never contributes a character to the path either way.
    staged_id = str(uuid.uuid4())
    storage_key = f"{staged_id}/blob"

    try:
        path = _resolve_storage_key(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_bytes(data)
        path.chmod(0o600)
    except OSError as exc:
        raise StagingWriteError("could not write the staged attachment") from exc

    ms = config.get_metadata_store()
    try:
        ms.execute(
            "INSERT INTO email_attachment_staged "
            "(id, user_id, filename, mime_type, size_bytes, storage_key) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (staged_id, user_id, display_name, mime, size, storage_key),
        )
    except Exception as exc:  # noqa: BLE001
        # An unindexed blob is invisible to the sweep, so remove it rather than
        # leaving a file nothing will ever reclaim.
        try:
            path.unlink(missing_ok=True)
        except OSError:
            _log.warning("email_staging_orphan_blob_cleanup_failed")
        raise StagingWriteError("could not index the staged attachment") from exc

    _log.info(
        "email_attachment_staged user_scoped id=%s filename=%s size=%s",
        staged_id,
        display_name,
        size,
    )
    return StagedAttachment(id=staged_id, filename=display_name, mime_type=mime, size_bytes=size)


def resolve(user_id: str, staged_id: str) -> Optional[ResolvedStagedAttachment]:
    """The staged file for ``staged_id``, or ``None`` if it is not this user's.

    Owner-scoped in the query itself, so a foreign id is indistinguishable from
    a missing one.
    """
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "SELECT id, filename, mime_type, storage_key FROM email_attachment_staged "
        "WHERE id::text = %s AND user_id = %s AND consumed_at IS NULL LIMIT 1",
        (str(staged_id), user_id),
    )
    if not row:
        return None
    try:
        path = _resolve_storage_key(str(row["storage_key"]))
    except ValueError:
        _log.warning("email_staging_bad_storage_key id=%s", staged_id)
        return None
    return ResolvedStagedAttachment(
        id=str(row["id"]),
        filename=str(row["filename"]),
        mime_type=str(row["mime_type"] or "application/octet-stream"),
        path=path,
    )


def resolve_all(user_id: str, staged_ids: Sequence[str]) -> list[ResolvedStagedAttachment]:
    """Resolve every id or raise :class:`StagedAttachmentUnavailable`.

    All-or-nothing: a send that quietly dropped one attachment would be worse
    than one that refused, because the user already approved a message they
    believed carried it.
    """
    out: list[ResolvedStagedAttachment] = []
    for sid in staged_ids:
        found = resolve(user_id, str(sid))
        if found is None or not found.path.exists():
            raise StagedAttachmentUnavailable(f"staged attachment {sid} is no longer available")
        out.append(found)
    return out


def discard(user_id: str, staged_ids: Iterable[str]) -> int:
    """Delete the rows and their files. The fast path after a successful send.

    The sweep is the backstop; this is what returns the disk promptly.
    """
    ids = [str(s) for s in staged_ids]
    if not ids:
        return 0
    ms = config.get_metadata_store()
    rows = (
        ms.fetch_all(
            "SELECT id, storage_key FROM email_attachment_staged "
            "WHERE id::text = ANY(%s) AND user_id = %s",
            (ids, user_id),
        )
        or []
    )
    return _delete_rows(ms, rows)


def available_attachments(proposal_id: int, *, user_id: str) -> list[StagedAttachment]:
    """The staged files still on disk for ``proposal_id`` (LUM-793).

    Read from ``email_attachment_staged``, **never** from the proposal payload.
    The payload records what the user attached at compose time and never changes;
    the sweep deletes files behind it. A surface that counted payload ids would
    promise "3 files" for a message that has none left — and it is a recovery
    surface, so the one thing it must not do is over-promise.

    Returns only UNCONSUMED rows: a consumed file was already attached to a
    message that went out and its bytes are gone.
    """
    ms = config.get_metadata_store()
    rows = (
        ms.fetch_all(
            "SELECT id, filename, mime_type, size_bytes FROM email_attachment_staged "
            "WHERE proposal_id = %s AND user_id = %s AND consumed_at IS NULL "
            "ORDER BY created_at",
            (int(proposal_id), user_id),
        )
        or []
    )
    return [
        StagedAttachment(
            id=str(r["id"]),
            filename=str(r.get("filename") or ""),
            mime_type=str(r.get("mime_type") or ""),
            size_bytes=int(r.get("size_bytes") or 0),
        )
        for r in rows
    ]


def discard_for_proposal(proposal_id: int) -> int:
    """Delete every staged file bound to ``proposal_id``.

    Hooked from ``action_proposals.cancel_proposal``: a user who backs out of a
    send gets their disk back immediately rather than waiting for the sweep.
    """
    ms = config.get_metadata_store()
    rows = (
        ms.fetch_all(
            "SELECT id, storage_key FROM email_attachment_staged WHERE proposal_id = %s",
            (int(proposal_id),),
        )
        or []
    )
    return _delete_rows(ms, rows)


def mark_consumed(staged_ids: Iterable[str]) -> None:
    """Stamp ``consumed_at`` so a resolved-and-sent file stops counting to quota."""
    ids = [str(s) for s in staged_ids]
    if not ids:
        return
    config.get_metadata_store().execute(
        "UPDATE email_attachment_staged SET consumed_at = NOW() WHERE id::text = ANY(%s)",
        (ids,),
    )


def bind_to_proposal(user_id: str, staged_ids: Sequence[str], *, proposal_id: int) -> None:
    """Link staged files to the proposal that will send them.

    This is the column the sweep keys on, and this is the **propose-time**
    ownership check: ``create_proposal`` runs only ``model.model_validate``, and
    a Pydantic model cannot check DB ownership. Raises
    :class:`StagedAttachmentUnavailable` if any id is unknown, consumed, or
    another user's — the route turns that into a 422 before the proposal exists.
    """
    ids = [str(s) for s in staged_ids]
    if not ids:
        return
    resolve_all(user_id, ids)  # ownership + existence, all-or-nothing
    config.get_metadata_store().execute(
        "UPDATE email_attachment_staged SET proposal_id = %s "
        "WHERE id::text = ANY(%s) AND user_id = %s",
        (int(proposal_id), ids, user_id),
    )


def total_size(user_id: str, staged_ids: Sequence[str]) -> int:
    """Summed ``size_bytes`` of the named staged files (0 for unknown ids)."""
    ids = [str(s) for s in staged_ids]
    if not ids:
        return 0
    ms = config.get_metadata_store()
    rows = (
        ms.fetch_all(
            "SELECT size_bytes FROM email_attachment_staged "
            "WHERE id::text = ANY(%s) AND user_id = %s AND consumed_at IS NULL",
            (ids, user_id),
        )
        or []
    )
    return sum(int(r.get("size_bytes") or 0) for r in rows)


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------


def sweep() -> int:
    """Delete staged files that are safe to delete. Returns the count.

    Three rules, and the first is the one a wall-clock TTL got wrong:

    * A file whose proposal is **live** (``pending``/``approved``/``claimed``/
      ``executing``) is **never** swept, however old. Proposals do not expire,
      so neither may their attachments — otherwise a batch staged on Friday and
      approved on Monday loses its files, deterministically, every weekend.
    * A file with ``proposal_id IS NULL`` ages out after
      ``EMAIL_ATTACHMENT_STAGING_TTL_HOURS``. That is the real leak: a route any
      authenticated user can call, where the composer was abandoned before Review.
    * A file whose proposal reached a **terminal** state is swept after
      ``EMAIL_ATTACHMENT_DEAD_GRACE_HOURS`` (7 days), measured from the
      proposal's ``finished_at`` — **not** promptly, as it was between LUM-762
      and LUM-793. LUM-793 lists a failed send for 30 days and offers to save it
      to Drafts; sweeping its files an hour after the failure made that offer a
      lie for anything with an attachment. The grace is bounded and applies to
      terminal rows ONLY (see :func:`dead_grace_hours`).

      A terminal row with a NULL ``finished_at`` (nothing writes one today, but
      the column is nullable) is swept immediately, as before — ``NULL <
      NOW() - interval`` is NULL, so the coalesce below is what decides it, and
      it defaults to the epoch rather than to "keep forever".

    Idempotent and bounded (``_SWEEP_BATCH`` rows per tick).
    """
    ms = config.get_metadata_store()
    rows = (
        ms.fetch_all(
            "SELECT id, storage_key FROM email_attachment_staged s "
            "WHERE s.consumed_at IS NULL AND ( "
            "  (s.proposal_id IS NULL AND s.created_at < NOW() - (%s * INTERVAL '1 hour')) "
            "  OR EXISTS ( "
            "    SELECT 1 FROM action_proposals p "
            "    WHERE p.id = s.proposal_id AND p.status = ANY(%s) "
            "      AND COALESCE(p.finished_at, 'epoch'::timestamptz) "
            "          < NOW() - (%s * INTERVAL '1 hour') "
            "  ) "
            ") "
            "LIMIT %s",
            (
                _ttl_hours(),
                sorted(TERMINAL_PROPOSAL_STATES),
                dead_grace_hours(),
                _SWEEP_BATCH,
            ),
        )
        or []
    )
    deleted = _delete_rows(ms, rows)
    if deleted:
        _log.info("email_attachment_staging_swept count=%s", deleted)
    return deleted


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _delete_rows(ms, rows: list[dict]) -> int:
    if not rows:
        return 0
    ids = [str(r["id"]) for r in rows]
    for r in rows:
        key = str(r.get("storage_key") or "")
        if not key:
            continue
        try:
            path = _resolve_storage_key(key)
        except ValueError:
            _log.warning("email_staging_bad_storage_key_on_delete")
            continue
        try:
            path.unlink(missing_ok=True)
            parent = path.parent
            if parent != _staging_root() and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            _log.warning("email_staging_unlink_failed")
    ms.execute("DELETE FROM email_attachment_staged WHERE id::text = ANY(%s)", (ids,))
    return len(ids)


def _unconsumed_bytes(user_id: str) -> int:
    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: email_attachment_staged is per-user pre-send scratch with no
    # scope column — a staged file is never household-visible, so an owner-only
    # user_id filter IS the correct isolation here, not a visibility regression.
    row = ms.fetch_one(
        "SELECT COALESCE(SUM(size_bytes), 0) AS total FROM email_attachment_staged "
        "WHERE user_id = %s AND consumed_at IS NULL",
        (user_id,),
    )
    return int((row or {}).get("total") or 0)


def _resolve_storage_key(storage_key: str) -> Path:
    """Resolve a ``{id}/blob`` key under the staging root, asserting no escape.

    Reuses the shipped capture-path *shape* — ``_reject_unsafe_path_component``
    on every segment plus an ``is_relative_to(root)`` assertion on the resolved
    candidate — rather than the capture function itself, which hardcodes a
    ``user/capture/attachment/filename`` layout this does not have.
    """
    root = _staging_root().resolve()
    parts = [p for p in str(storage_key).strip().strip("/").split("/") if p]
    if not parts:
        raise ValueError(f"invalid storage_key: {storage_key!r}")
    for part in parts:
        _reject_unsafe_path_component(part, field="storage_key segment")
    candidate = root.joinpath(*parts).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError(
            f"path traversal in storage_key: {storage_key!r} resolves outside {root!r}"
        )
    return candidate
