# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Per-mailbox outgoing signature (LUM-817).

Lumogis sends real mail from the user's own address. Every client they arrived
from appends a signature; Lumogis did not, so mail sent through it was visibly
different from mail they send anywhere else.

**Where the signature is applied, and why it is only one place.**
The composer seeds it into the editable body at mount, so the user *sees* it
before sending and can delete it for one message. The send path deliberately
does **not** append it. That is the decision the ticket left open, resolved
toward visible, and the reason is not only honesty: with both an invisible
server-side append and a visible composer seed, every ordinary send would carry
the signature twice. One application point cannot double.

The cost of that choice, stated rather than discovered: a send created without
going through the composer — a future agentic path (LUM-654), or a direct
proposal via the API — carries no signature. Acceptable today because the
composer is the only way to send, and cheap to revisit: the append would move
here, and the composer seed would become a preview.

**Never on `email_unsubscribe`.** That message is a machine-read `mailto:`
command with a hardcoded body, not correspondence. A signature on it is noise
to a list operator at best, and hands them the user's name, role and phone
number at worst. It gets one for free by construction — the unsubscribe path
never touches the composer — and `test_unsubscribe_carries_no_signature` pins
that rather than trusting it.

Plain text only. HTML would inherit LUM-697's render-side scheme allowlist,
because a stored signature is user-supplied content that reaches an outbound
message; that is a larger decision and is deliberately not taken here.
"""

from __future__ import annotations

import logging

import config

_log = logging.getLogger(__name__)

MAX_SIGNATURE_BYTES = 2000
"""Generous for a signature, small enough that it cannot be used as storage.

Four or five lines of contact details is ~200 bytes. The cap exists so the
column cannot quietly become an unbounded per-mailbox text field appended to
every outgoing message.
"""


class SignatureTooLong(ValueError):
    """Raised when a submitted signature exceeds :data:`MAX_SIGNATURE_BYTES`."""


def get_signature(source_id: str, user_id: str) -> str:
    """Return the mailbox's signature, or ``""``.

    Ownership-scoped: a caller may only read the signature of a mailbox they
    own. A signature is not secret, but it carries a person's name and often
    their phone number, so it follows the same rule as everything else on the
    row rather than a looser one.
    """
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        # SCOPE-EXEMPT: the caller's own mailbox row, user_id-scoped on the same
        # predicate. `visible_filter` is not used — a household-shared mailbox
        # must not hand its signature to every member.
        "SELECT signature FROM sources WHERE id::text = %s AND user_id = %s",
        (str(source_id), user_id),
    )
    if row is None:
        return ""
    return str(row.get("signature") or "")


def set_signature(source_id: str, user_id: str, signature: str) -> str:
    """Store ``signature`` for a mailbox the caller owns. Returns what was stored.

    Raises:
        SignatureTooLong: over :data:`MAX_SIGNATURE_BYTES` when encoded.
    """
    text = (signature or "").strip()
    if len(text.encode("utf-8")) > MAX_SIGNATURE_BYTES:
        raise SignatureTooLong(f"signature exceeds {MAX_SIGNATURE_BYTES} bytes")

    ms = config.get_metadata_store()
    ms.execute(
        # SCOPE-EXEMPT: ownership is the WHERE clause. A non-owned source_id
        # updates zero rows rather than raising — the caller learns nothing
        # about whether the mailbox exists.
        "UPDATE sources SET signature = %s WHERE id::text = %s AND user_id = %s",
        (text, str(source_id), user_id),
    )
    return text
