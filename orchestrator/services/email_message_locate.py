# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Resolve where a signal's message actually is, so a moved one is still found.

**One implementation, because there were two and they disagreed** (LUM-813).
`email_attachment_ingest` read `signals.email_message_id` first and fell back to
`email_organize_log`; the copy in `routes/api_v1/email_attachments.py` read the
log **only**, and the attachment-download and send paths imported that one.

The log has a row only when *Lumogis's own* organize did the moving. Mail the
user filed in their own client, or a paperless-ngx rule moved, or a server-side
filter moved, has no row — so the log-only lookup returned `None`, the
Message-ID search never ran, and the message was declared gone while sitting
right there. That is precisely the hole migration `069` was written to close.

Neither copy was wrong in isolation; the trap was that they looked
interchangeable and the incomplete one sat next to the route an implementer
opens first. This module exists so there is nothing to choose between.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

import config

_log = logging.getLogger(__name__)


def _lookup_ids(signal_id: str, user_id: str) -> list[str]:
    """``[signal_id, published_from]`` when the row is a shared projection.

    Projection INSERTs omit ``email_message_id`` (it lives on the personal
    original only). ``collapseProjections`` can surface the projection row when
    the user triaged only that copy, so every locate helper must walk one hop
    to the original before giving up.
    """
    sid = str(signal_id)
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        # SCOPE-EXEMPT: the caller's own signal, keyed on their own user_id.
        "SELECT published_from FROM signals WHERE signal_id::text = %s AND user_id = %s LIMIT 1",
        (sid, user_id),
    )
    parent = (row or {}).get("published_from")
    if parent and str(parent) != sid:
        return [sid, str(parent)]
    return [sid]


def message_id_for(signal_id: str, user_id: str) -> Optional[str]:
    """The stored ``Message-ID``, used to relocate a message that has moved.

    **Two sources, and the order is the fix.** ``signals.email_message_id``
    (migration 069) is written at ingest for every email signal, so it covers a
    move by ANY cause — the user in their mail client, a paperless-ngx mail
    rule, a server-side filter. ``email_organize_log`` only ever had a row when
    *Lumogis's own* organize did the moving, which meant every external mover
    produced a terminal ``message_gone`` skip and the attachment was never
    offered again.

    The log is still consulted second, for signals ingested before 069 whose
    column is ``''``. Absent from both, callers fall back to the uid.

    Shared projections omit the column; :func:`_lookup_ids` walks to the personal
    original before the organize-log fallback runs.
    """
    ms = config.get_metadata_store()
    for sid in _lookup_ids(signal_id, user_id):
        row = ms.fetch_one(
            # SCOPE-EXEMPT: the caller's own signal, keyed on their own user_id;
            # this is a per-user read path, not a household read.
            "SELECT email_message_id FROM signals "
            "WHERE signal_id::text = %s AND user_id = %s LIMIT 1",
            (sid, user_id),
        )
        stored = str((row or {}).get("email_message_id") or "").strip()
        if stored:
            return stored

    # SCOPE-EXEMPT: email_organize_log is per-user operational history with no
    # scope column (migration 060); this read is owner-scoped by design.
    row = ms.fetch_one(
        "SELECT reverse_token FROM email_organize_log "
        "WHERE user_id = %s AND signal_url = ("
        "  SELECT url FROM signals WHERE signal_id = %s LIMIT 1"
        ") AND reversed_at IS NULL ORDER BY id DESC LIMIT 1",
        (user_id, signal_id),
    )
    token = (row or {}).get("reverse_token") or {}
    if isinstance(token, str):
        try:
            token = json.loads(token)
        except ValueError:
            return None
    mid = token.get("message_id") if isinstance(token, dict) else None
    return str(mid) if mid else None


def organize_folder_for(signal_id: str, user_id: str) -> Optional[str]:
    """Where organize/cleanup moved the message, if it did. Best-effort hint.

    Only ever answers for Lumogis's own moves — that is the whole contract, and
    why it is a *hint*: absent, the caller sweeps folders rather than giving up.
    Unlike :func:`message_id_for`, the two former copies of this were identical,
    so nothing behavioural changes by sharing it.
    """
    ms = config.get_metadata_store()
    for sid in _lookup_ids(signal_id, user_id):
        # SCOPE-EXEMPT: as above — email_organize_log has no scope column.
        row = ms.fetch_one(
            "SELECT target FROM email_organize_log "
            "WHERE user_id = %s AND op = 'move' AND signal_url = ("
            "  SELECT url FROM signals WHERE signal_id = %s LIMIT 1"
            ") AND reversed_at IS NULL ORDER BY id DESC LIMIT 1",
            (user_id, sid),
        )
        target = (row or {}).get("target")
        if target:
            return str(target)
    return None
