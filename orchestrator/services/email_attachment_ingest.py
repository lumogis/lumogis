# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Auto-file email attachments into the document library (LUM-702).

An **opt-in, per-mailbox** standing sweep. It takes the attachment metadata
LUM-695 already records (``email_attachment``), fetches those bytes on demand,
runs them through the existing ``@extractor`` registry, and files the text as a
document — deduped by the sha256 of the **attachment bytes**, logged per part,
and reversible.

Three things about the shape are deliberate and easy to get wrong later.

**Every part gets exactly one verdict, forever.** The selection query's LEFT JOIN
carries no ``reversed_at IS NULL`` predicate, and ``uq_..._part`` is a full unique
index. A row in this ledger means "already judged" whether the judgement was a
filing, a skip, or a filing the user later undid. That is what makes undo stick:
a partial index would let the next tick re-file the attachment the user just
removed, fifteen minutes after they removed it.

**Transient failures leave no row; terminal ones leave a skip row.** An
unreachable mailbox must be retried next tick, so it writes nothing. A message
that is gone, ambiguous, or unextractable will never succeed, so it writes a skip
row and is never reconsidered. Getting this backwards either retries forever or
gives up on a mailbox that was briefly down.

**One failing attachment never stops the tick.** ``feed_monitor``'s poll loop
``break``s on a handler error, so one bad message stalls everything behind it.
That lesson is already paid for; this sweep ``continue``s.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from typing import Optional

# LUM-813: one implementation, aliased under the private names this module's
# tests and call sites already bind. See services/email_message_locate.py.
from services.email_message_locate import message_id_for as _message_id_for
from services.email_message_locate import organize_folder_for as _organize_folder_for

import config

_log = logging.getLogger(__name__)

try:  # Prefer a typed unique-violation catch; degrade to never-matching if absent.
    from psycopg2.errors import UniqueViolation as _UniqueViolation
except Exception:  # pragma: no cover - psycopg2 is always present in the product image

    class _UniqueViolation(Exception):  # type: ignore[no-redef]
        """Placeholder so the narrow catches below are still syntactically valid."""


INGEST_TICK_INTERVAL_SECONDS: int = int(
    os.environ.get("EMAIL_ATTACHMENT_INGEST_INTERVAL_SECONDS", "900")
)

# Bounds one tick's IMAP + extraction + embedding work. The honest cost is one
# IMAP session per MESSAGE (``fetch_attachment_parts`` connects and logs out
# inside each call and takes no ``client=``, unlike ``apply_organize``), so a
# 50-attachment tick spread over 50 messages is 50 logins. This constant is what
# bounds that; session reuse is a follow-up.
MAX_ATTACHMENTS_PER_TICK: int = int(os.environ.get("EMAIL_ATTACHMENT_INGEST_MAX_PER_TICK", "50"))

# Skip reasons. A row carrying one is a DECISION, not a filing.
SKIP_NO_EXTRACTOR = "no_extractor"
SKIP_BLOCKED_EXT = "blocked_ext"
SKIP_TOO_LARGE = "too_large"
SKIP_NO_TEXT = "no_text"
SKIP_DUPLICATE = "duplicate"
SKIP_MESSAGE_GONE = "message_gone"
SKIP_AMBIGUOUS = "ambiguous"

# Terminal fetch failures — these will never succeed, so they earn a skip row.
# ``imap_unreachable`` is deliberately absent: it is transient and must leave no
# row so the next tick retries.
_TERMINAL_FETCH_REASONS = {
    "message_gone": SKIP_MESSAGE_GONE,
    "part_gone": SKIP_MESSAGE_GONE,
    "message_too_large": SKIP_TOO_LARGE,
    "message_ambiguous": SKIP_AMBIGUOUS,
}

_SUFFIX_STEM_RE = re.compile(r"[^a-z0-9]")


def max_ingest_bytes() -> int:
    """Per-file ceiling. Defaults to LUM-695's attachment cap when unset.

    Read at call time so a test can patch the environment without reimporting.
    """
    raw = os.environ.get("EMAIL_ATTACHMENT_INGEST_MAX_MB")
    if raw:
        try:
            return int(raw) * 1024 * 1024
        except ValueError:
            _log.warning("EMAIL_ATTACHMENT_INGEST_MAX_MB is not an integer; using default")
    from services import email_attachments

    return email_attachments.max_attachment_bytes()


def ingestible_extensions() -> frozenset[str]:
    """Dotted, lowercased extensions the extractor registry can read.

    Derived at call time rather than hardcoded, so registering a new
    ``@extractor`` widens this feature without editing this module.
    """
    try:
        return frozenset(str(k).lower() for k in (config.get_extractors() or {}))
    except Exception:  # pragma: no cover - registry import failure is not this sweep's problem
        _log.exception("email_attachment_ingest: extractor registry unavailable")
        return frozenset()


@dataclass(frozen=True)
class PendingAttachment:
    """One attachment awaiting a verdict, with everything needed to reach one."""

    signal_id: str
    part_index: int
    filename: str
    mime_type: str
    size_bytes: int
    signal_url: str


@dataclass
class IngestTickResult:
    scanned: int = 0
    filed: int = 0
    skipped: int = 0
    failed: int = 0

    def as_dict(self) -> dict:
        return {
            "scanned": self.scanned,
            "filed": self.filed,
            "skipped": self.skipped,
            "failed": self.failed,
        }


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #

# `_PENDING_SQL` / `pending_for_source` were removed by LUM-727 and replaced by
# `_CANDIDATES_SQL` / `candidates_for_source` below. They had become unreachable
# in production the moment the sweep stopped filing what it found, and two
# near-identical queries where only one actually runs is a trap — the next
# person to fix a join fixes the dead one. The live query keeps everything that
# mattered about the old one: the `s.signal_id::text` cast, and a LEFT JOIN with
# NO `reversed_at IS NULL` predicate, so ANY verdict row — filing, skip, decline
# or reversed filing — means the part is judged and is never offered again.


def _opted_in_sources(user_id: Optional[str] = None) -> list[dict]:
    """Every mailbox that has opted in. Defaults keep this empty until someone does.

    LUM-742: `user_id` is optional because the sweep genuinely wants every
    user's mailboxes, but every per-caller path must pass it. Without the
    predicate this returns the whole table and callers discard the rest in
    Python — harmless while the result was only iterated, and NOT harmless once
    its length became the quota denominator: one household's page size would
    have been sized by other households' mailbox counts.
    """
    ms = config.get_metadata_store()
    sql = (
        "SELECT id::text AS id, user_id, name, attachment_ingest_since, extraction_method "
        "FROM sources "
        "WHERE source_type = 'imap' AND attachment_ingest_enabled = TRUE "
        "  AND attachment_ingest_since IS NOT NULL"
    )
    if user_id is None:
        return ms.fetch_all(sql) or []
    return ms.fetch_all(sql + " AND user_id = %s", (user_id,)) or []


# --------------------------------------------------------------------------- #
# The text bridge
# --------------------------------------------------------------------------- #


def _suffix_for(filename: str) -> str:
    """A dotted, lowercased, alphanumeric-only extension — or ``""``.

    ``mkstemp(suffix=…)`` takes a **dotted** suffix (``".pdf"``) and
    ``config.get_extractors()`` is keyed on the **dotted, lowercased** extension
    (``@extractor(".pdf")``), so one value serves both. Stripping the dot for one
    and not the other yields a silent ``no_extractor`` skip on every file.

    The attacker-supplied name contributes nothing but these few characters: the
    on-disk stem is ``mkstemp``'s own random one, so ``../../etc/cron.d/x.pdf``
    cannot become a path component. Same conclusion LUM-695 reached for staging.
    """
    tail = (filename or "").rsplit(".", 1)
    if len(tail) != 2 or not tail[1]:
        return ""
    stem = _SUFFIX_STEM_RE.sub("", tail[1].lower())[:10]
    return f".{stem}" if stem else ""


def _extract_text(*, data: bytes, filename: str) -> Optional[str]:
    """Bytes → text, via a temp file, because extractors take a **path**.

    Returns ``None`` when no text could be read — an empty result and a raising
    extractor are the same verdict to the caller (``no_text``), and neither is
    allowed to escape: one hostile attachment must not end the tick.
    """
    suffix = _suffix_for(filename)
    extractor = (config.get_extractors() or {}).get(suffix)
    if extractor is None:
        return None

    # mkstemp already creates 0600; fchmod restates it so the guarantee is local
    # to this function rather than an inherited detail. Handing the fd to fdopen
    # FIRST means the file object owns it from here on, so no path — including
    # fchmod raising — can leak the descriptor.
    fd, path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as fh:
            os.fchmod(fh.fileno(), 0o600)
            fh.write(data)
        text = extractor(path)
    except Exception:
        _log.exception("email_attachment_extract_failed suffix=%s", suffix)
        return None
    finally:
        # Runs on every path, including an extractor that raised.
        try:
            os.unlink(path)
        except OSError:  # pragma: no cover - already gone
            pass
    if not isinstance(text, str) or not text.strip():
        return None
    return text


# --------------------------------------------------------------------------- #
# Ledger writes
# --------------------------------------------------------------------------- #


def _record_skip(
    *,
    user_id: str,
    source_id: str,
    att: PendingAttachment,
    reason: str,
    content_sha256: str = "",
) -> None:
    """Write the verdict so the sweep never reconsiders this part.

    ``ON CONFLICT DO NOTHING`` on the part index: another process reaching the
    same verdict first is not an error, and there is nothing to update.
    """
    ms = config.get_metadata_store()
    ms.execute(
        "INSERT INTO email_attachment_ingest_log "
        "(user_id, source_id, signal_id, part_index, filename, mime_type, "
        " size_bytes, content_sha256, logical_path, chunk_count, skipped_reason) "
        "VALUES (%s, %s::uuid, %s, %s, %s, %s, %s, %s, %s, 0, %s) "
        "ON CONFLICT (user_id, signal_id, part_index) DO NOTHING",
        (
            user_id,
            str(source_id),
            att.signal_id,
            att.part_index,
            att.filename,
            att.mime_type,
            att.size_bytes,
            content_sha256,
            _logical_path(source_id, att),
            reason,
        ),
    )


def _logical_path(source_id: str, att: PendingAttachment) -> str:
    return f"email-attachment://{source_id}/{att.signal_id}/{att.part_index}"


def _active_row_for_hash(*, user_id: str, source_id: str, digest: str) -> Optional[dict]:
    ms = config.get_metadata_store()
    return ms.fetch_one(
        "SELECT id, logical_path FROM email_attachment_ingest_log "
        # SCOPE-EXEMPT: email_attachment_ingest_log has no `scope`
        # column (067) — per-user provenance for the owner's own
        # mailbox, like its email_organize_log sibling.
        "WHERE user_id = %s AND source_id = %s::uuid AND content_sha256 = %s "
        "  AND reversed_at IS NULL AND skipped_reason IS NULL LIMIT 1",
        (user_id, str(source_id), digest),
    )


# --------------------------------------------------------------------------- #
# The verdict
# --------------------------------------------------------------------------- #


def _file_one(
    *,
    user_id: str,
    source_id: str,
    att: PendingAttachment,
    data: bytes,
    filed_by_rule_id: Optional[str] = None,
) -> str:
    """Reach and record a verdict for one attachment. Returns ``"filed"`` or the skip reason.

    Raises only for failures that must **not** leave a row — a dead vector store
    or embedder, which the next tick should retry.
    """
    from services.ingest import ingest_email_attachment

    digest = hashlib.sha256(data).hexdigest()

    # Cheap path first; the partial unique index below is the real gate and
    # catches the race this SELECT cannot.
    if _active_row_for_hash(user_id=user_id, source_id=source_id, digest=digest):
        _record_skip(
            user_id=user_id,
            source_id=source_id,
            att=att,
            reason=SKIP_DUPLICATE,
            content_sha256=digest,
        )
        return SKIP_DUPLICATE

    text = _extract_text(data=data, filename=att.filename)
    if text is None:
        _record_skip(
            user_id=user_id,
            source_id=source_id,
            att=att,
            reason=SKIP_NO_TEXT,
            content_sha256=digest,
        )
        return SKIP_NO_TEXT

    # Vectors before the ledger row. A vector-store failure here raises past the
    # ledger write, so no row is left and the next tick retries — the "do not
    # write a log row" branch of the error contract.
    result = ingest_email_attachment(
        user_id=user_id,
        source_id=str(source_id),
        signal_id=att.signal_id,
        part_index=att.part_index,
        filename=att.filename,
        content=text,
    )
    if result.chunk_count == 0:
        _record_skip(
            user_id=user_id,
            source_id=source_id,
            att=att,
            reason=SKIP_NO_TEXT,
            content_sha256=digest,
        )
        return SKIP_NO_TEXT

    ms = config.get_metadata_store()
    try:
        # RETURNING, not a bare execute: `ON CONFLICT DO NOTHING` returns NO row
        # when another tick judged this part first, and the vectors are already
        # written by then. Treating that as a filing would leave a searchable
        # document with no ledger row pointing at it — one undo can never reach,
        # because undo works off the ledger. Reachable only across processes
        # (`max_instances=1` rules out the in-process race), which is exactly the
        # kind of narrow path that never gets noticed once it does happen.
        landed = ms.fetch_one(
            "INSERT INTO email_attachment_ingest_log "
            "(user_id, source_id, signal_id, part_index, filename, mime_type, "
            " size_bytes, content_sha256, logical_path, chunk_count, filed_by_rule_id) "
            "VALUES (%s, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s::uuid) "
            "ON CONFLICT (user_id, signal_id, part_index) DO NOTHING "
            "RETURNING id",
            (
                user_id,
                str(source_id),
                att.signal_id,
                att.part_index,
                att.filename,
                att.mime_type,
                att.size_bytes,
                digest,
                result.file_path,
                int(result.chunk_count),
                filed_by_rule_id,
            ),
        )
    except _UniqueViolation:
        # uq_..._active: a concurrent tick claimed this content slot between our
        # SELECT and this INSERT. Ours is the redundant copy, so take its vectors
        # back out rather than leaving a second searchable document behind, and
        # record the part as a duplicate so it is never reconsidered.
        _log.info("email_attachment_ingest_duplicate_race logical_path=%s", result.file_path)
        _discard_vectors(
            user_id=user_id,
            logical_path=result.file_path,
            signal_id=att.signal_id,
            part_index=att.part_index,
            chunk_count=int(result.chunk_count),
        )
        _record_skip(
            user_id=user_id,
            source_id=source_id,
            att=att,
            reason=SKIP_DUPLICATE,
            content_sha256=digest,
        )
        return SKIP_DUPLICATE

    if landed is None:
        # Another tick reached a verdict for this part first. Its verdict stands
        # (one verdict per part, ever), so take our now-unreferenced vectors back
        # out rather than leaving a document nothing can undo.
        _log.info("email_attachment_ingest_part_race logical_path=%s", result.file_path)
        _discard_vectors(
            user_id=user_id,
            logical_path=result.file_path,
            signal_id=att.signal_id,
            part_index=att.part_index,
            chunk_count=int(result.chunk_count),
        )
        return SKIP_DUPLICATE
    return "filed"


def _discard_vectors(
    *, user_id: str, logical_path: str, signal_id: str, part_index: int, chunk_count: int
) -> None:
    from services.ingest import _delete_document_vectors_for_path
    from services.point_ids import email_attachment_chunk_point_id

    _delete_document_vectors_for_path(
        user_id=user_id,
        file_path=logical_path,
        chunk_count_fallback=chunk_count,
        point_id_for_chunk=lambda i: email_attachment_chunk_point_id(
            user_id, signal_id, part_index, i
        ),
    )


def _pre_fetch_verdict(att: PendingAttachment, extensions: frozenset[str]) -> Optional[str]:
    """The skips decidable from METADATA alone — reached before any bytes move.

    Refusing an executable or an oversize file after downloading it would be a
    refusal in name only.
    """
    from services import email_attachments

    if email_attachments.is_blocked_extension(att.filename):
        return SKIP_BLOCKED_EXT
    if att.size_bytes > max_ingest_bytes():
        return SKIP_TOO_LARGE
    if _suffix_for(att.filename) not in extensions:
        return SKIP_NO_EXTRACTOR
    return None


# --------------------------------------------------------------------------- #
# The tick
# --------------------------------------------------------------------------- #


def _adapter_for(source_id: str, user_id: str):
    """Deferred imports, matching ``email_organize``: ``services/`` must not carry
    a module-level dependency on ``adapters/`` or ``actions/``."""
    from actions.handlers.imap_writeback import _load_owned_source
    from adapters.email_imap_source import EmailImapSource

    return EmailImapSource(_load_owned_source(source_id, user_id))


def _uid_from_signal_url(url: str) -> Optional[tuple[int, int]]:
    """``imap://{source}/{uidvalidity}/{uid}`` → ``(uidvalidity, uid)``."""
    from actions.handlers.imap_writeback import _parse_imap_url

    parsed = _parse_imap_url(url or "")
    if parsed is None:
        return None
    _source_id, uidvalidity, uid = parsed
    return int(uidvalidity), int(uid)


class _Session:
    """One lazily-opened, reusable IMAP session for a tick.

    Lazy because a tick whose every attachment is decided from metadata (blocked
    extension, unreadable type, oversize) must open nothing at all.

    Droppable because reuse trades away a resilience the per-message version had
    for free: with one shared connection, a mid-tick drop would otherwise fail
    every remaining message. :meth:`drop` closes the dead session so the next
    message reconnects — one transient failure costs one message, as before.
    """

    def __init__(self, *, source_id: str, user_id: str) -> None:
        self._source_id = source_id
        self._user_id = user_id
        self._adapter = None
        self._client = None

    def get(self):
        """``(adapter, client)``, opening the session on first use."""
        if self._adapter is None:
            self._adapter = _adapter_for(self._source_id, self._user_id)
        if self._client is None:
            self._client = self._adapter.read_session()
        return self._adapter, self._client

    def drop(self) -> None:
        """Discard a session that failed, so the next message opens a fresh one."""
        if self._client is not None and self._adapter is not None:
            try:
                self._adapter.close_session(self._client)
            except Exception:  # pragma: no cover - already broken
                pass
        self._client = None

    def close(self) -> None:
        self.drop()
        self._adapter = None


# `_sweep_source` was removed by LUM-727. It filed every candidate it found,
# which is the behaviour this change exists to end — and a dead function that
# still does the old thing is exactly what gets re-wired by accident later.
# Its replacement is `_apply_rules_for_source`, which files only what a rule
# the user wrote matches. The batching it did (group by message, one parse per
# message) lives on in `_accept_for_source`.


def _fetch_parts(*, source_id, user_id, signal_id, atts, result, session):
    """One session, one parse, all of this message's wanted indexes.

    Returns ``{part_index: ResolvedPart}``, or ``None`` when the message could not
    be read — having already recorded a terminal skip, or deliberately recorded
    **nothing** for a transient failure so the next tick retries.
    """
    from adapters.email_imap_source import AttachmentFetchError

    located = _uid_from_signal_url(atts[0].signal_url)
    if located is None:
        for att in atts:
            _record_skip(user_id=user_id, source_id=source_id, att=att, reason=SKIP_MESSAGE_GONE)
            result.skipped += 1
        return None
    uidvalidity, uid = located

    try:
        adapter, client = session.get()
        parts = adapter.fetch_attachment_parts(
            uid=uid,
            part_indexes=[a.part_index for a in atts],
            message_id=_message_id_for(signal_id, user_id),
            folder_hint=_organize_folder_for(signal_id, user_id),
            uidvalidity=uidvalidity,
            # Reused across every message in this tick; the adapter leaves a
            # caller-supplied session open.
            client=client,
        )
    except AttachmentFetchError as exc:
        reason = _TERMINAL_FETCH_REASONS.get(exc.code)
        if reason is None:
            # Transient (imap_unreachable). NO row — the next tick retries. Drop
            # the shared session too: it is the most likely thing that just died,
            # and a dead connection reused for the rest of the tick would turn one
            # transient failure into every remaining message failing.
            session.drop()
            _log.warning("email_attachment_ingest_transient source=%s code=%s", source_id, exc.code)
            result.failed += len(atts)
            return None
        # A TERMINAL verdict is about the message, not the connection — the
        # session is still good, so it is deliberately not dropped here.
        for att in atts:
            _record_skip(user_id=user_id, source_id=source_id, att=att, reason=reason)
            result.skipped += 1
        return None
    except Exception:
        # Unknown failure: assume the connection is suspect rather than reusing it.
        session.drop()
        _log.exception("email_attachment_ingest_fetch_failed source=%s", source_id)
        result.failed += len(atts)
        return None

    return {p.part_index: p for p in parts}


def ingest_tick(*, limit: int = MAX_ATTACHMENTS_PER_TICK) -> IngestTickResult:
    """One bounded standing sweep — **rule-filing only** (LUM-727).

    This job no longer files what it finds. Since LUM-727, a candidate with no
    matching rule is left for the user to decide per attachment, so the only work
    that must happen while nobody is watching is a rule firing: a document the
    user has *already* said yes to, arriving without them visiting a page.

    The consequence is that this tick's IMAP and embedding cost is now
    proportional to what the user actually wants, rather than to everything that
    arrives — and with no rules configured it is one cheap query and an exit.
    """
    result = IngestTickResult()
    budget = int(limit)
    for row in _opted_in_sources():
        if budget <= 0:
            break
        # Defensive: the opt-in route refuses a metadata-mode mailbox, but a
        # mailbox can be downgraded after opting in, and such a mailbox has no
        # `email_attachment` rows to begin with.
        if str(row.get("extraction_method") or "") != "imap_full_ingest":
            continue
        try:
            budget -= _apply_rules_for_source(
                source_id=str(row["id"]),
                user_id=str(row["user_id"]),
                since=row.get("attachment_ingest_since"),
                budget=budget,
                result=result,
            )
        except Exception:
            # One broken mailbox must not stop the others.
            _log.exception("email_attachment_ingest_source_failed source=%s", row.get("id"))
            result.failed += 1
    return result


def _apply_rules_for_source(
    *, source_id: str, user_id: str, since, budget: int, result: IngestTickResult
) -> int:
    """File the candidates a rule matches. Everything else is left pending.

    A declined part can never reach here — it has a ledger row, the candidate
    query excludes anything with a ledger row, and rules are only ever evaluated
    over candidates. **The guarantee is structural, not conditional**: there is
    no code path on which a rule sees a part the user declined. Anyone
    "optimising" this to evaluate rules over `email_attachment` directly removes
    that guarantee silently.
    """
    from services import email_attachment_rules

    candidates = candidates_for_source(
        source_id=source_id, user_id=user_id, since=since, limit=budget
    )
    if not candidates:
        return 0

    matched: list[tuple[Candidate, dict]] = []
    for c in candidates:
        rule = email_attachment_rules.matches(
            user_id, source_id, from_addr=c.from_addr, category=c.category
        )
        if rule is not None:
            matched.append((c, rule))

    result.scanned += len(candidates)
    if not matched:
        return len(candidates)

    accept: dict = {"accepted": 0, "skipped": []}
    _accept_for_source(
        user_id=user_id,
        source_id=source_id,
        candidates=[c for c, _ in matched],
        result=accept,
        rule_ids={(c.signal_id, c.part_index): str(r["id"]) for c, r in matched},
        tick=result,
    )
    result.filed += int(accept["accepted"])
    # `failed` is owned by `tick` (transient fetch failures and per-attachment
    # exceptions); everything else the accept declined to file is a skip.
    result.skipped += sum(
        1 for sk in accept["skipped"] if sk.get("reason") not in ("failed", "unavailable")
    )
    return len(candidates)


# --------------------------------------------------------------------------- #
# Undo
# --------------------------------------------------------------------------- #
#
# ORDERING IS THE CONTRACT: delete the vectors FIRST, then set ``reversed_at``.
#
# The other order leaves a row marked reversed with its chunks still searchable —
# the user is told the document is gone and it keeps appearing in search results,
# which is the single failure that destroys trust in an undo. A crash between the
# two leaves orphaned-but-unreferenced vectors that the *next* undo attempt cleans
# up, precisely because ``reversed_at`` is still NULL.


def _reverse_row(row: dict) -> bool:
    """Vectors, then the library row, then the flag.

    **The ordering is the contract** and the flag stays last for the reason
    LUM-702 documented: a row marked reversed with its chunks still searchable
    tells the user a document is gone while it keeps appearing in results, which
    is the single failure that destroys trust in an undo. A crash before the flag
    leaves everything retryable.

    The ``file_index`` row (LUM-724) is removed in the middle — after the vectors,
    before the flag. Removing it first would leave a document that is unlisted but
    still searchable; leaving it behind entirely would leave a library entry
    pointing at chunks that no longer exist.
    """
    user_id = str(row["user_id"])
    try:
        _discard_vectors(
            user_id=user_id,
            logical_path=str(row["logical_path"]),
            signal_id=str(row["signal_id"]),
            part_index=int(row["part_index"]),
            chunk_count=int(row.get("chunk_count") or 0),
        )
    except Exception:
        # Leave reversed_at NULL so the next attempt retries, rather than
        # reporting a document gone that is still searchable.
        _log.exception(
            "email_attachment_undo_vector_delete_failed logical_path=%s",
            row.get("logical_path"),
        )
        return False

    # LUM-724: the library row goes with the vectors. Best-effort — a failure
    # here leaves a listed document whose chunks are gone, which the backfill
    # script's counterpart can repair, whereas failing the whole undo would
    # leave the user unable to take back a filing at all.
    from services import external_document_index as ext_index

    ext_index.remove(user_id=user_id, logical_path=str(row["logical_path"]))

    ms = config.get_metadata_store()
    ms.execute(
        "UPDATE email_attachment_ingest_log SET reversed_at = NOW() "
        "WHERE id = %s AND user_id = %s AND reversed_at IS NULL",
        (int(row["id"]), user_id),
    )
    return True


_ACTIVE_COLUMNS = "id, user_id, signal_id, part_index, logical_path, chunk_count"


def undo_row(user_id: str, log_id: int) -> bool:
    """Reverse one filing. Idempotent: an already-reversed row is a no-op, not a
    second delete."""
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        f"SELECT {_ACTIVE_COLUMNS} FROM email_attachment_ingest_log "
        "WHERE id = %s AND user_id = %s AND reversed_at IS NULL "
        "  AND skipped_reason IS NULL",
        (int(log_id), user_id),
    )
    if row is None:
        return False
    return _reverse_row(dict(row))


def undo_for_source(user_id: str, source_id: str) -> int:
    """Reverse every active filing for a mailbox. Returns how many actually landed.

    The count excludes rows whose vector delete failed — reporting five removed
    when four were is the same broken promise as the wrong ordering.
    """
    ms = config.get_metadata_store()
    rows = (
        ms.fetch_all(
            f"SELECT {_ACTIVE_COLUMNS} FROM email_attachment_ingest_log "
            # SCOPE-EXEMPT: email_attachment_ingest_log has no `scope`
            # column (067) — per-user provenance for the owner's own
            # mailbox, like its email_organize_log sibling.
            "WHERE user_id = %s AND source_id = %s::uuid AND reversed_at IS NULL "
            "  AND skipped_reason IS NULL ORDER BY id",
            (user_id, str(source_id)),
        )
        or []
    )
    return sum(1 for row in rows if _reverse_row(dict(row)))


def filed_rows(
    *,
    user_id: str,
    source_id: Optional[str] = None,
    limit: int = 100,
    include_declined: bool = False,
) -> list[dict]:
    """The filed list — filings and skips alike, newest first.

    Joins ``signals`` for the sender and subject. A filename alone does not
    answer the question this surface exists for: "where did this document in my
    library come from?" — and ``invoice.pdf`` is exactly the name that arrives
    from a dozen different senders. LEFT, not inner, so a filing whose signal was
    since purged still lists (the ledger deliberately has no FK on ``signal_id``,
    matching ``email_attachment`` in 066).
    """
    ms = config.get_metadata_store()
    params: list = [user_id]
    clause = ""
    if source_id:
        clause = " AND l.source_id = %s::uuid"
        params.append(str(source_id))
    # Declines are excluded by DEFAULT. They are a skip reason like any other,
    # but they arrive at many times the rate of the rest — a user declining most
    # of what arrives would otherwise turn "Filed from email" into a list of
    # things they said no to, which is the flooding LUM-727 exists to prevent,
    # in the one surface it was not looking at.
    if not include_declined:
        clause += " AND (l.skipped_reason IS DISTINCT FROM %s)"
        params.append(SKIP_DECLINED)
    params.append(int(limit))
    return (
        ms.fetch_all(
            "SELECT l.id, l.signal_id, l.part_index, l.filename, l.mime_type, "
            "       l.size_bytes, l.logical_path, l.ingested_at, l.reversed_at, "
            "       l.skipped_reason, s.from_addr, s.from_display, s.title, "
            # LUM-742: the ledger already carries source_id — it is filtered
            # on above — it was just never selected. Deliberately NO source
            # name here: unlike `_CANDIDATES_SQL` this has no `sources` join,
            # and adding one purely for a label is not worth it when the page
            # already holds the sources listing and can resolve it client-side.
            "       l.source_id::text AS source_id "
            "FROM email_attachment_ingest_log l "
            # `AND s.user_id = l.user_id` is defence in depth, not a live fix: the
            # sweep only ever writes ledger rows for the owner's own mailbox, so
            # the joined signal is already theirs. But this join reads a table
            # that DOES carry user_id/scope, and LUM-305 was exactly this class —
            # a visibility predicate that quietly did not apply. Cheap to state.
            "LEFT JOIN signals s ON s.signal_id::text = l.signal_id "
            "AND s.user_id = l.user_id "
            # SCOPE-EXEMPT: email_attachment_ingest_log has no `scope`
            # column (067) — per-user provenance for the owner's own
            # mailbox, like its email_organize_log sibling.
            f"WHERE l.user_id = %s{clause} "
            "ORDER BY l.ingested_at DESC, l.id DESC LIMIT %s",
            tuple(params),
        )
        or []
    )


# --------------------------------------------------------------------------- #
# Re-file (LUM-721)
# --------------------------------------------------------------------------- #
#
# Undo is deliberately STICKY: `uq_..._part` is a full unique index, so the sweep
# never reconsiders a part the user removed. That is what stops an undo silently
# reverting itself fifteen minutes later. The cost is that changing your mind
# needs an explicit action, and this is it.
#
# It works by clearing `reversed_at`, NOT by weakening the index — the index is
# what makes undo trustworthy, and re-filing must not buy itself convenience by
# spending that.

REFILE_OK = "refiled"
REFILE_NOT_FOUND = "not_found"
REFILE_DUPLICATE = "duplicate"
REFILE_UNAVAILABLE = "unavailable"


def _signal_url_for(signal_id: str, user_id: str) -> Optional[str]:
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "SELECT url FROM signals WHERE signal_id::text = %s AND user_id = %s LIMIT 1",
        (str(signal_id), user_id),
    )
    return str(row["url"]) if row and row.get("url") else None


def refile_row(user_id: str, log_id: int) -> str:
    """Re-file a filing the user previously undid.

    The bytes were never stored, so this re-runs the whole path: fetch, extract,
    ingest. Returns one of the ``REFILE_*`` outcomes.

    **The content slot is respected by the database, not by a check here.**
    Clearing ``reversed_at`` re-enters the partial ``uq_..._active`` index, so if
    the same bytes were filed from another message in the meantime, the UPDATE
    raises and this reports a duplicate — rather than creating a second document
    for content the library already has.
    """
    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: email_attachment_ingest_log has no `scope`
    # column (067) — per-user provenance for the owner's own
    # mailbox, like its email_organize_log sibling.
    row = ms.fetch_one(
        "SELECT id, user_id, source_id::text AS source_id, signal_id, part_index, "
        "       filename, mime_type, size_bytes, logical_path "
        "FROM email_attachment_ingest_log "
        "WHERE id = %s AND user_id = %s AND reversed_at IS NOT NULL "
        # LUM-724: a PURGE also sets `reversed_at` (to free the content-hash
        # slot), so without this a purged document would be offered "File
        # again" — resurrecting a deliberate deletion. `purged_at` is the one
        # signal that separates "undone, restorable" from "destroyed".
        "AND purged_at IS NULL "
        "  AND skipped_reason IS NULL",
        (int(log_id), user_id),
    )
    if row is None:
        # Not the caller's row, never filed, or not currently reversed. All three
        # are honestly "there is nothing here to put back", and keeping them
        # indistinguishable keeps the route from becoming an ownership oracle.
        return REFILE_NOT_FOUND

    row = dict(row)
    signal_id = str(row["signal_id"])
    source_id = str(row["source_id"])
    att = PendingAttachment(
        signal_id=signal_id,
        part_index=int(row["part_index"]),
        filename=str(row["filename"] or ""),
        mime_type=str(row["mime_type"] or ""),
        size_bytes=int(row["size_bytes"] or 0),
        signal_url=_signal_url_for(signal_id, user_id) or "",
    )

    session = _Session(source_id=source_id, user_id=user_id)
    result = IngestTickResult()
    try:
        parts = _fetch_parts(
            source_id=source_id,
            user_id=user_id,
            signal_id=signal_id,
            atts=[att],
            result=result,
            session=session,
        )
    finally:
        session.close()

    part = (parts or {}).get(att.part_index)
    if part is None:
        # The message may have been deleted since the original filing — the
        # standing cost of never storing the bytes. Note that `_fetch_parts` will
        # have recorded a terminal skip for a gone message; that write is a no-op
        # here because this part already has a verdict row (uq_..._part), which is
        # exactly the invariant working as intended.
        return REFILE_UNAVAILABLE

    digest = hashlib.sha256(part.data).hexdigest()
    text = _extract_text(data=part.data, filename=att.filename)
    if text is None:
        return REFILE_UNAVAILABLE

    ingest_result = _ingest_for_refile(user_id=user_id, source_id=source_id, att=att, text=text)
    if ingest_result is None or ingest_result.chunk_count == 0:
        return REFILE_UNAVAILABLE

    try:
        ms.execute(
            "UPDATE email_attachment_ingest_log "
            "SET reversed_at = NULL, content_sha256 = %s, chunk_count = %s, "
            "    logical_path = %s, ingested_at = NOW() "
            "WHERE id = %s AND user_id = %s AND reversed_at IS NOT NULL AND purged_at IS NULL",
            (
                digest,
                int(ingest_result.chunk_count),
                ingest_result.file_path,
                int(row["id"]),
                user_id,
            ),
        )
    except _UniqueViolation:
        # uq_..._active: the same bytes are already filed from another message.
        # Ours is the redundant copy — take its vectors back out and leave the row
        # reversed, rather than leaving a second searchable document behind.
        _discard_vectors(
            user_id=user_id,
            logical_path=ingest_result.file_path,
            signal_id=signal_id,
            part_index=att.part_index,
            chunk_count=int(ingest_result.chunk_count),
        )
        return REFILE_DUPLICATE
    return REFILE_OK


def _ingest_for_refile(*, user_id: str, source_id: str, att: PendingAttachment, text: str):
    from services.ingest import ingest_email_attachment

    try:
        return ingest_email_attachment(
            user_id=user_id,
            source_id=source_id,
            signal_id=att.signal_id,
            part_index=att.part_index,
            filename=att.filename,
            content=text,
        )
    except Exception:
        _log.exception(
            "email_attachment_refile_ingest_failed signal=%s part=%s",
            att.signal_id,
            att.part_index,
        )
        return None


# --------------------------------------------------------------------------- #
# Candidates (LUM-727)
# --------------------------------------------------------------------------- #
#
# The sweep no longer files what it finds. It proposes, and the user decides per
# attachment — because the share of attachments not worth filing is large enough
# that a global "file everything from this mailbox" rule does not work at all.
#
# THIS QUERY COSTS ZERO IMAP CALLS. Everything it needs is already stored:
# `email_attachment` (066) has filename, MIME type, size and part index for every
# attachment; `signals` has sender, subject and category. Fetch, extract and
# embed move behind the accept, so the attachments a user declines cost nothing
# beyond a row that already existed.

SKIP_DECLINED = "declined"

_CANDIDATES_SQL = """
SELECT ea.signal_id  AS signal_id,
       ea.part_index AS part_index,
       ea.filename   AS filename,
       ea.mime_type  AS mime_type,
       ea.size_bytes AS size_bytes,
       s.url         AS signal_url,
       -- LUM-742: `src` is already joined below for attachment_ingest_enabled,
       -- so the mailbox's identity costs nothing here. On the candidates path
       -- the name comes from the server for that reason; the filed-rows path
       -- has no `sources` join and resolves it client-side instead.
       src.id::text  AS source_id,
       src.name      AS source_name,
       s.from_addr   AS from_addr,
       s.from_display AS from_display,
       s.title       AS subject,
       s.category    AS category,
       ea.created_at AS received_at
  FROM email_attachment ea
  JOIN signals s
    -- uuid -> text, never text -> uuid. `signals.signal_id` is UUID and
    -- `email_attachment.signal_id` is TEXT; casting text->uuid would use the PK
    -- index and then RAISE on the first non-UUID value, taking the whole list
    -- down for one bad row. uuid->text is total. This exact join shipped broken
    -- once already (LUM-702) because nobody compared the two column types.
    ON s.signal_id::text = ea.signal_id
  JOIN sources src
    -- uuid -> text AGAIN, and for the third time in this feature: `sources.id`
    -- is UUID while `signals.source_id` is TEXT. This join was ADDED to fix a
    -- critique finding and shipped the very defect the critique was about;
    -- the real-Postgres lane caught it. Check every join in this file.
    ON src.id::text = s.source_id
  LEFT JOIN email_attachment_ingest_log l
    ON l.user_id = ea.user_id
   AND l.signal_id = ea.signal_id
   AND l.part_index = ea.part_index
 WHERE s.source_id = %s
   AND ea.user_id = %s
   -- IN the query, not in the caller. `set_attachment_ingest` deliberately
   -- PRESERVES attachment_ingest_since on disable (so re-enabling does not
   -- replay history), which means `since` alone does not express "off": a
   -- mailbox enabled once and then disabled has a perfectly good watermark and
   -- would otherwise keep producing candidates forever.
   AND src.attachment_ingest_enabled = TRUE
   AND ea.created_at >= %s
   AND l.id IS NULL                     -- no verdict yet, in either direction
   AND ea.size_bytes <= %s              -- would be refused on accept anyway
   -- Filtered HERE and not in Python: after a LIMIT, unfilable attachments
   -- would fill the window and permanently starve the filable ones behind them.
   -- LUM-645's review found exactly this and fixed it the same way.
   AND lower(substring(ea.filename from '\\.([^.]+)$')) = ANY(%s)
 ORDER BY ea.created_at DESC, ea.signal_id, ea.part_index
 LIMIT %s
"""


def _bare_ingestible_extensions() -> list[str]:
    """Extractor keys with the leading dot stripped.

    Both forms exist in this codebase and they are NOT interchangeable:
    ``get_extractors()`` is keyed dotted (``.pdf``), ``_BLOCKED_EXTENSIONS`` is
    stored bare (``exe``), and ``_suffix_for`` returns dotted. The SQL capture
    yields a bare extension, so passing the dotted set here matches nothing and
    the review list is silently empty forever.
    """
    return [e.lstrip(".") for e in ingestible_extensions() if e]


@dataclass(frozen=True)
class Candidate:
    """One attachment awaiting a per-file decision. No bytes have moved."""

    signal_id: str
    part_index: int
    filename: str
    mime_type: str
    size_bytes: int
    from_addr: str
    from_display: str
    subject: str
    category: str
    received_at: str
    signal_url: str
    # LUM-742. A filing rule belongs to exactly ONE mailbox, so a cross-mailbox
    # list that groups by sender alone has no rule it could offer — which is
    # why `AttachmentReviewList` hid "Always file from this sender" whenever it
    # was unscoped, taking the LUM-728 paperless gate with it. Carrying the
    # source makes the group key (sender, source) and gives both back.
    source_id: str = ""
    source_name: str = ""


@dataclass(frozen=True)
class CandidatePage:
    """One page of candidates plus what the caller must be told about it.

    `truncated_sources` is not a nicety: it is the whole user-facing mitigation
    for a mailbox not being fully represented. Without it a household with two
    mailboxes cannot tell "nothing pending here" from "your window filled up
    before we got to this one".
    """

    candidates: list["Candidate"]
    #: (source_id, source_name) for mailboxes with rows beyond their quota.
    truncated_sources: list[tuple[str, str]]
    #: (source_id, source_name) for mailboxes whose query raised. Reported, not
    #: swallowed — a silently-absent mailbox reads as a broken feature.
    errored_sources: list[tuple[str, str]]
    #: Every pending attachment across the caller's mailboxes, unbounded by
    #: `limit`. NOT the page length: `total` in the response is `len(rows)`
    #: after trimming, so it can never exceed `limit` and is useless as a count.
    pending_total: int


def candidates_for_source(
    *, source_id: str, user_id: str, since, limit: int = 200
) -> list[Candidate]:
    """Attachments with no verdict yet, newest first. **No IMAP.**"""
    extensions = _bare_ingestible_extensions()
    if not extensions:
        return []
    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: the owner's own source_id plus their user_id. email_attachment
    # has no scope column (066) and is per-user by construction.
    rows = (
        ms.fetch_all(
            _CANDIDATES_SQL,
            (
                str(source_id),
                user_id,
                since,
                max_ingest_bytes(),
                extensions,
                int(limit),
            ),
        )
        or []
    )
    return [
        Candidate(
            signal_id=str(r["signal_id"]),
            part_index=int(r["part_index"]),
            filename=str(r["filename"] or ""),
            mime_type=str(r["mime_type"] or ""),
            size_bytes=int(r["size_bytes"] or 0),
            from_addr=str(r.get("from_addr") or ""),
            from_display=str(r.get("from_display") or ""),
            subject=str(r.get("title") or r.get("subject") or ""),
            category=str(r.get("category") or ""),
            received_at=_iso(r.get("received_at")),
            signal_url=str(r.get("signal_url") or ""),
            # Fall back to the argument rather than the row: a fake store in a
            # unit test may not echo the join, and the caller always knows which
            # mailbox it asked about.
            source_id=str(r.get("source_id") or source_id),
            source_name=str(r.get("source_name") or ""),
        )
        for r in rows
    ]


def _count_sql() -> str:
    """`_CANDIDATES_SQL`'s FROM/WHERE with a COUNT head and no ORDER BY/LIMIT.

    **Derived, never re-typed.** This file already carries the scar: three joins
    in `_CANDIDATES_SQL` cast uuid→text, two of them shipped broken, and the
    comment there says *"two near-identical queries where only one actually runs
    is a trap — the next person to fix a join fixes the dead one."* A
    hand-written COUNT would be that second query, and it would drift the first
    time a predicate changes.
    """
    body = _CANDIDATES_SQL[_CANDIDATES_SQL.index("  FROM email_attachment ea") :]
    return "SELECT COUNT(*) AS n" + body[: body.index("\n ORDER BY")]


def pending_count_for_source(*, source_id: str, user_id: str, since) -> int:
    """How many attachments await a verdict — **unbounded by any page limit**.

    The route's `total` is `len(rows)` *after* the page is trimmed, so it can
    never exceed `limit` and cannot answer "how many are waiting?". A tab badge
    reading it with `limit=1` would show 1 forever.
    """
    extensions = _bare_ingestible_extensions()
    if not extensions:
        return 0
    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: same predicates as `candidates_for_source` — the owner's own
    # source_id plus their user_id, both parameterised.
    row = ms.fetch_one(
        _count_sql(),
        (str(source_id), user_id, since, max_ingest_bytes(), extensions),
    )
    return int((row or {}).get("n") or 0)


def _iso(value) -> str:
    if value is None:
        return ""
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _eligible_sources(user_id: str, source_id: Optional[str]) -> list[dict]:
    """The caller's opted-in, full-ingest mailboxes — optionally just one.

    One helper so `candidates_for_user` and `candidates_page_for_user` cannot
    drift apart on *which mailboxes count*. This file already carries a comment
    (see `_CANDIDATES_SQL`) about two near-identical queries where only one runs;
    a second copy of this filter would be the same trap.
    """
    out = []
    for row in _opted_in_sources(user_id):
        if source_id and str(row["id"]) != str(source_id):
            continue
        if str(row.get("extraction_method") or "") != "imap_full_ingest":
            continue
        out.append(row)
    return out


def candidates_page_for_user(
    *, user_id: str, source_id: Optional[str] = None, limit: int = 200
) -> CandidatePage:
    """One page of candidates, fair-shared across mailboxes by quota.

    **Why quotas and not a merge.** The obvious cross-source design — fetch from
    every mailbox, sort newest-first, take `limit` — does not fair-share at all:
    a mailbox whose mail is simply more recent takes every slot, which is the
    exact defect this exists to fix. It looks like it preserves a newest-first
    contract, but no such contract exists at this layer: the route sorts groups
    by ``(-count, from_display)``, so recency only decides which candidates
    enter the window, never how they are shown. With nothing to preserve, there
    is nothing to trade, and quotas are simply correct.

    Sorting across sources is therefore not merely unnecessary but a hazard:
    ``Candidate.received_at`` is already a **string** (``""`` when the row has
    no timestamp), so a lexicographic DESC merge would push undated attachments
    past the window and silently drop them.

    Selection routes through `candidates_for_source` once per mailbox. Never
    write a combined query: `_CANDIDATES_SQL` is where ``ea.user_id = %s`` lives,
    and a single cross-source query would move user isolation into new code.
    """
    sources = _eligible_sources(user_id, source_id)
    # Not an edge case — this is the activation branch, the most-hit state on a
    # fresh install, and `limit // 0` raises there.
    if not sources:
        return CandidatePage(
            candidates=[], truncated_sources=[], errored_sources=[], pending_total=0
        )

    n = len(sources)
    quota = max(1, int(limit) // n)
    # Spread the remainder so the page reaches `limit` exactly instead of
    # stopping short of it when n does not divide limit.
    extra = max(0, int(limit) - quota * n)

    out: list[Candidate] = []
    truncated: list[tuple[str, str]] = []
    errored: list[tuple[str, str]] = []
    pending_total = 0

    for idx, row in enumerate(sources):
        sid = str(row["id"])
        name = str(row.get("name") or "")
        share = quota + (1 if idx < extra else 0)
        try:
            # +1 detects "this mailbox had more" without a second query.
            got = candidates_for_source(
                source_id=sid,
                user_id=user_id,
                since=row.get("attachment_ingest_since"),
                limit=share + 1,
            )
        except Exception as exc:  # noqa: BLE001 — one bad mailbox must not blank the page
            _log.warning("candidates_page_for_user: source %s failed — %s", sid, exc)
            errored.append((sid, name))
            continue
        # The count is a badge number, so it gets its OWN catch. Sharing the
        # block above meant a failing COUNT discarded a mailbox's candidates
        # that had already been fetched successfully — losing the user's list
        # over a number that only decorates a tab.
        try:
            pending_total += pending_count_for_source(
                source_id=sid, user_id=user_id, since=row.get("attachment_ingest_since")
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning("candidates_page_for_user: count for %s failed — %s", sid, exc)
        if len(got) > share:
            truncated.append((sid, name))
        # Trim to the quota, or the page overshoots `limit`.
        out.extend(got[:share])

    return CandidatePage(
        candidates=out,
        truncated_sources=truncated,
        errored_sources=errored,
        pending_total=pending_total,
    )


def candidates_for_user(*, user_id: str, source_id: Optional[str] = None, limit: int = 200):
    """Candidates across the caller's opted-in mailboxes, or just one.

    **Signature deliberately unchanged (LUM-742).** `_candidate_index` calls
    this on the accept/decline path and expects a plain list; the paging
    concerns live in `candidates_page_for_user` precisely so filing cannot break
    when the review surface's contract moves.
    """
    out: list[Candidate] = []
    for row in _opted_in_sources():
        if str(row.get("user_id")) != user_id:
            continue
        if source_id and str(row["id"]) != str(source_id):
            continue
        if str(row.get("extraction_method") or "") != "imap_full_ingest":
            continue
        out.extend(
            candidates_for_source(
                source_id=str(row["id"]),
                user_id=user_id,
                since=row.get("attachment_ingest_since"),
                limit=limit - len(out),
            )
        )
        if len(out) >= limit:
            break
    return out[:limit]


# --------------------------------------------------------------------------- #
# Accept / decline — the per-attachment decision
# --------------------------------------------------------------------------- #


def _candidate_index(user_id: str) -> dict[tuple[str, int], Candidate]:
    """Every live candidate for this user, keyed by ref. Built ONCE per call.

    Resolving refs one at a time would rescan the whole candidate set per ref —
    O(refs x candidates) for a bulk accept, and the scan is a database query.

    Not-yours, already-judged and never-a-candidate all collapse to "absent" on
    purpose: the caller must not be able to distinguish them, or the route
    becomes an oracle over another user's mail.

    LUM-742: must use the same fair-share selection as the review surface
    (`candidates_page_for_user`), not `candidates_for_user`'s sequential fill.
    A mailbox whose sibling consumed the index budget would be visible on the
    page but absent here — accept/decline would return ``not_found`` for rows
    the user is looking at.
    """
    page = candidates_page_for_user(user_id=user_id, limit=10_000)
    return {(c.signal_id, c.part_index): c for c in page.candidates}


def _source_for_signal(user_id: str, signal_id: str) -> Optional[str]:
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "SELECT source_id::text AS source_id FROM signals "
        "WHERE signal_id::text = %s AND user_id = %s LIMIT 1",
        (str(signal_id), user_id),
    )
    return str(row["source_id"]) if row and row.get("source_id") else None


def _as_pending(c: Candidate) -> PendingAttachment:
    return PendingAttachment(
        signal_id=c.signal_id,
        part_index=c.part_index,
        filename=c.filename,
        mime_type=c.mime_type,
        size_bytes=c.size_bytes,
        signal_url=c.signal_url,
    )


def decline_candidates(user_id: str, refs: list[tuple[str, int]]) -> int:
    """Record a terminal `declined` verdict. **No IMAP, no bytes.**

    Terminal by `uq_..._part`: the part is judged once, forever, so the review
    list never offers it again. Reversible only through `undo_decline`.
    """
    declined = 0
    index = _candidate_index(user_id)
    for signal_id, part_index in refs:
        c = index.get((signal_id, int(part_index)))
        if c is None:
            continue
        source_id = _source_for_signal(user_id, signal_id)
        if source_id is None:
            continue
        _record_skip(user_id=user_id, source_id=source_id, att=_as_pending(c), reason=SKIP_DECLINED)
        declined += 1
    return declined


def undo_decline(user_id: str, signal_id: str, part_index: int) -> bool:
    """Return a declined part to pending by DELETING its verdict row.

    Delete, not a flag flip — and this is the one place LUM-721's machinery does
    **not** transfer. A declined row never created a document, so there is
    nothing to restore and `reversed_at` has no meaning for it; the honest
    representation of "un-decline" is that the part has no verdict again.
    """
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        # SCOPE-EXEMPT: the caller's own verdict row, keyed on their own user_id.
        "DELETE FROM email_attachment_ingest_log "
        "WHERE user_id = %s AND signal_id = %s AND part_index = %s "
        "  AND skipped_reason = %s "
        "RETURNING id",
        (user_id, str(signal_id), int(part_index), SKIP_DECLINED),
    )
    return row is not None


def accept_candidates(user_id: str, refs: list[tuple[str, int]], *, limit: int = 25) -> dict:
    """File the picked attachments. **The only path that spends IMAP/embedding.**

    Refs are grouped by source before anything opens, because `_Session` is per
    (source, user): a flat loop spanning two mailboxes would open, close and
    reopen a session per ref and throw away LUM-722 entirely.

    Bounded, and honest about the bound — a bulk accept of 200 files is 200
    fetch+extract+embed cycles, so what it did not reach comes back in `skipped`
    rather than being silently truncated.
    """
    result = {"accepted": 0, "skipped": []}
    index = _candidate_index(user_id)
    by_source: dict[str, list[Candidate]] = {}
    for signal_id, part_index in refs[: int(limit)]:
        c = index.get((signal_id, int(part_index)))
        if c is None:
            result["skipped"].append(
                {"signal_id": signal_id, "part_index": int(part_index), "reason": "not_found"}
            )
            continue
        source_id = _source_for_signal(user_id, signal_id)
        if source_id is None:
            result["skipped"].append(
                {"signal_id": signal_id, "part_index": int(part_index), "reason": "not_found"}
            )
            continue
        by_source.setdefault(source_id, []).append(c)

    for ref in refs[int(limit) :]:
        result["skipped"].append(
            {"signal_id": ref[0], "part_index": int(ref[1]), "reason": "over_limit"}
        )

    for source_id, cands in by_source.items():
        _accept_for_source(user_id=user_id, source_id=source_id, candidates=cands, result=result)
    return result


def _accept_for_source(
    *,
    user_id: str,
    source_id: str,
    candidates: list,
    result: dict,
    rule_ids: Optional[dict] = None,
    tick: Optional[IngestTickResult] = None,
) -> None:
    extensions = ingestible_extensions()
    session = _Session(source_id=source_id, user_id=user_id)
    # `_fetch_parts` records transient-vs-terminal outcomes on this. When the
    # sweep is the caller it passes its own, so a mailbox that went unreachable
    # mid-tick is reported as FAILED rather than vanishing into a local object.
    tick = tick if tick is not None else IngestTickResult()
    try:
        by_signal: dict[str, list[Candidate]] = {}
        for c in candidates:
            by_signal.setdefault(c.signal_id, []).append(c)

        for signal_id, group in by_signal.items():
            atts = [_as_pending(c) for c in group]
            # The caps and the blocklist still apply. Picking a file is consent
            # to file it, not consent to bypass the executable refusal or the
            # size ceiling.
            wanted = []
            for att in atts:
                verdict = _pre_fetch_verdict(att, extensions)
                if verdict is not None:
                    _record_skip(user_id=user_id, source_id=source_id, att=att, reason=verdict)
                    result["skipped"].append(
                        {
                            "signal_id": att.signal_id,
                            "part_index": att.part_index,
                            "reason": verdict,
                        }
                    )
                    continue
                wanted.append(att)
            if not wanted:
                continue

            parts = _fetch_parts(
                source_id=source_id,
                user_id=user_id,
                signal_id=signal_id,
                atts=wanted,
                result=tick,
                session=session,
            )
            for att in wanted:
                part = (parts or {}).get(att.part_index)
                if part is None:
                    result["skipped"].append(
                        {
                            "signal_id": att.signal_id,
                            "part_index": att.part_index,
                            "reason": "unavailable",
                        }
                    )
                    continue
                # One failing attachment must never stop the batch — the
                # feed_monitor `break` regression, in a new place.
                try:
                    outcome = _file_one(
                        user_id=user_id,
                        source_id=source_id,
                        att=att,
                        data=part.data,
                        filed_by_rule_id=(rule_ids or {}).get((att.signal_id, att.part_index)),
                    )
                except Exception:
                    _log.exception(
                        "email_attachment_accept_failed signal=%s part=%s",
                        att.signal_id,
                        att.part_index,
                    )
                    result["skipped"].append(
                        {
                            "signal_id": att.signal_id,
                            "part_index": att.part_index,
                            "reason": "failed",
                        }
                    )
                    tick.failed += 1
                    continue
                if outcome == "filed":
                    result["accepted"] += 1
                else:
                    result["skipped"].append(
                        {
                            "signal_id": att.signal_id,
                            "part_index": att.part_index,
                            "reason": outcome,
                        }
                    )
    finally:
        session.close()


def unreachable_count(*, user_id: str, source_id: Optional[str] = None) -> int:
    """Attachments the mailbox can no longer produce (LUM-727 Phase 1c).

    ``message_gone`` is a **terminal** verdict, and correctly so — the part will
    never be fetchable again, so re-offering it would ask a question nobody can
    answer. But terminal was being conflated with *invisible*: the attachment
    simply stopped appearing among the candidates, with no trace anywhere the
    user was looking. Someone waiting to file an invoice would watch it never
    arrive and conclude the feature was broken.

    Migration 069 makes this rarer — the refetch now survives a move by any
    client — but not impossible: a message genuinely deleted upstream is gone.
    Counting it is what turns silence into an explanation.
    """
    ms = config.get_metadata_store()
    params: list = [user_id, SKIP_MESSAGE_GONE]
    clause = ""
    if source_id:
        clause = " AND source_id = %s::uuid"
        params.append(str(source_id))
    try:
        row = ms.fetch_one(
            # SCOPE-EXEMPT: the caller's own verdict rows, keyed on their own
            # user_id; email_attachment_ingest_log has no `scope` column (067).
            "SELECT COUNT(*) AS n FROM email_attachment_ingest_log "
            "WHERE user_id = %s AND skipped_reason = %s" + clause,
            tuple(params),
        )
    except Exception:  # noqa: BLE001 - a count must never break the review list
        _log.warning("unreachable_count: query failed", exc_info=True)
        return 0
    return int((row or {}).get("n") or 0)
