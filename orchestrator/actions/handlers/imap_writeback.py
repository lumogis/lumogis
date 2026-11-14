# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""IMAP mailbox write-back — the first mutating connector action (LUM-623).

Reversible, per-message act-tier actions that apply a classification-driven
mark-read / flag / move / label to a specific IMAP message, under Ask/Do
approval (ADR-164). The client passes only a ``signal_id`` (never a raw
UID/source) so it can act only on a message the system already attributed to it;
the handler recovers ``(source_id, uidvalidity, uid)`` from the signal's
``imap://…`` url and enforces that the **source is OWNED by the executor-supplied
``user_id``** — ownership, not visibility, so a member cannot write to another
member's mailbox via a shared signal.

The handler receives ``user_id`` from the executor (never from the payload).
Failures that a retry cannot fix (stale UID, non-owned source, unsupported
server capability) raise :class:`PermanentActionError` so the proposal queue
dead-letters on the first failure instead of retrying.

Registration runs at import (``main.py`` imports this module at startup). This
handler orchestrates mechanical IMAP writes only — no proprietary intelligence.
"""

from __future__ import annotations

import logging
from typing import Optional

from actions.errors import PermanentActionError
from actions.registry import register_action
from models.actions import ActionResult
from models.actions import ActionSpec
from pydantic import BaseModel
from pydantic import ConfigDict

import config

_log = logging.getLogger(__name__)

_CONNECTOR = "imap"
_ACTION_TYPE = "email_writeback"  # reversible; NOT hard-limited
_REVERSE_ACTION = "imap_writeback_reverse"

# op -> forward action name
_OPS = {
    "mark_read": "imap_mark_read",
    "flag": "imap_flag",
    "move": "imap_move",
    "label": "imap_label",
}


class ImapWritebackInput(BaseModel):
    """Create payload for an IMAP write-back proposal.

    ``extra="forbid"`` rejects a smuggled ``user_id``/``source_id``/``uid`` at the
    422 layer — identity comes only from the executor, never the payload.
    """

    model_config = ConfigDict(extra="forbid")

    signal_id: str
    target: Optional[str] = None  # folder (move) or label; required for move/label


def _parse_imap_url(url: str) -> Optional[tuple[str, int, int]]:
    """``imap://{source_id}/{uidvalidity}/{uid}`` -> (source_id, uidvalidity, uid)."""
    if not url or not url.startswith("imap://"):
        return None
    rest = url[len("imap://") :]
    parts = rest.split("/")
    if len(parts) != 3:
        return None
    source_id, uv_s, uid_s = parts
    try:
        return (source_id, int(uv_s), int(uid_s))
    except (ValueError, TypeError):
        return None


def _load_owned_source(source_id: str, user_id: str):
    """Fetch the source ONLY if it is owned by ``user_id`` (the write-isolation
    gate). Returns a SourceConfig, or raises PermanentActionError (not-found).
    ``sources.id`` is UUID and the url carries it as text, so compare via
    ``id::text`` (avoids a cast error on non-UUID values).
    """
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "SELECT * FROM sources WHERE id::text = %s AND user_id = %s",
        (source_id, user_id),
    )
    if not row:
        raise PermanentActionError("not_found", "signal source not found or not owned by caller")
    from signals.feed_monitor import _row_to_source

    return _row_to_source(row)


def _signal_url(signal_id: str) -> Optional[str]:
    ms = config.get_metadata_store()
    row = ms.fetch_one("SELECT url FROM signals WHERE signal_id = %s LIMIT 1", (signal_id,))
    return str(row["url"]) if row and row.get("url") is not None else None


def _apply(op: str, input: dict, *, user_id: str) -> ActionResult:
    parsed = ImapWritebackInput.model_validate(input)  # extra=forbid
    if op in ("move", "label") and not parsed.target:
        raise PermanentActionError("target_required", f"{op} requires a target")

    url = _signal_url(parsed.signal_id)
    if url is None:
        raise PermanentActionError("not_found", "signal not found")
    recovered = _parse_imap_url(url)
    if recovered is None:
        raise PermanentActionError("not_imap_signal", "signal is not an IMAP message")
    source_id, uidvalidity, uid = recovered

    # Ownership gate BEFORE any connection is opened (no load_connection for a
    # non-owned source — the write-isolation invariant).
    source = _load_owned_source(source_id, user_id)

    from adapters.email_imap_source import EmailImapSource

    adapter = EmailImapSource(source)
    reverse = adapter.apply_writeback(uidvalidity=uidvalidity, uid=uid, op=op, target=parsed.target)
    reverse_input = {"source_id": source_id, **reverse}
    return ActionResult(success=True, output=f"imap:{op}", reverse_input=reverse_input)


def _reverse(input: dict, *, user_id: str) -> ActionResult:
    op = input.get("op")
    source_id = input.get("source_id")
    if not op or not source_id:
        raise PermanentActionError("reverse_bad_input", "reverse_input missing op/source_id")
    source = _load_owned_source(str(source_id), user_id)  # re-check ownership on reverse

    from adapters.email_imap_source import EmailImapSource

    adapter = EmailImapSource(source)
    adapter.reverse_writeback(
        op=op,
        mailbox=input.get("mailbox", "INBOX"),
        message_id=input.get("message_id"),
        target=input.get("target"),
        flag=input.get("flag"),
    )
    return ActionResult(success=True, output=f"imap:reverse:{op}")


def _make_forward(op: str):
    def handler(input: dict, *, user_id: str) -> ActionResult:
        return _apply(op, input, user_id=user_id)

    return handler


def _annotate_move(payload: dict, user_id: str) -> dict:
    """LUM-729 — a Do-mode `imap_move` can starve paperless, so it must be Asked.

    LUM-729 gated the two *standing policies* that take mail out of `INBOX`
    (folder-sort and the cleanup window) and then the mirror (connecting
    paperless). `imap_move` is the remaining path and it is a different shape:
    one message, per-message, chosen by the agent — so a standing consent dialog
    is the wrong instrument. The right one is the mechanism this codebase already
    has for "you may have set this connector to Do, but this particular call has
    to be seen": a create-time annotation that forces an explicit Ask.

    **Only `move`.** `mark_read`, `flag` and `label` leave the message in `INBOX`,
    so paperless still consumes it. Annotating them would train people to click
    through the one that matters.

    **Deliberately not the fail-open helper.** `has_external_consumer` swallows
    its errors so a broken guard cannot block a user configuring their mailbox.
    Here the failure direction is the opposite — an unanswerable question must
    put the action in front of a person — and `_compute_annotation` already
    fails closed on a raised exception. Calling the fail-open variant would have
    quietly turned that into fail-open.
    """
    from services import external_consumer_guard as ecg

    starves = ecg.has_external_consumer_strict(user_id)
    return {"force_ask": starves, "external_consumer": starves}


_REGISTERED = False


def register_all() -> None:
    """Register the four forward write-back actions + the shared reverse action.

    Idempotent — safe to call from startup more than once.
    """
    global _REGISTERED
    if _REGISTERED:
        return
    for op, name in _OPS.items():
        register_action(
            ActionSpec(
                name=name,
                connector=_CONNECTOR,
                action_type=_ACTION_TYPE,
                is_write=True,
                is_reversible=True,
                handler=_make_forward(op),
                reverse_action_name=_REVERSE_ACTION,
                input_model=ImapWritebackInput,
                definition={"op": op},
                # LUM-729: only the op that actually takes the message out of INBOX.
                annotate=_annotate_move if op == "move" else None,
            )
        )
    register_action(
        ActionSpec(
            name=_REVERSE_ACTION,
            connector=_CONNECTOR,
            action_type=_ACTION_TYPE,
            is_write=True,
            is_reversible=False,  # never chain-reverse a reversal
            handler=_reverse,
            input_model=None,  # only reached via attempt_reverse, never the producer
        )
    )
    _REGISTERED = True
    _log.info("imap_writeback: registered %d write-back actions + reverse", len(_OPS))


# Register at import so a startup `import actions.handlers.imap_writeback` wires
# the actions into the registry the executor reads.
register_all()
