# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Reply-all recipient arithmetic (LUM-705).

Pure: headers in, ``(to, cc)`` out. No DB, no IMAP, no config. Every interesting
case in this feature is an address-set case — a display name containing a comma,
a folded header, group syntax, the user's own address under a different casing —
and they belong somewhere a test can reach without a mailbox.

Parsing is :func:`email.utils.getaddresses`, not ``split(",")``. RFC 5322 lets a
display name contain a comma (``"Doe, Jane" <jane@x>``), and a ``split`` turns one
recipient into two malformed ones — on a *send* path, where the mistake is
irreversible and visible to everyone on the thread.
"""

from __future__ import annotations

from dataclasses import dataclass
from email.utils import getaddresses

# Imported, never reimplemented. "Is this me?" here must not be able to disagree
# with "have I contacted them?" in `_is_first_contact`, and "is this a usable
# address?" here must not be able to disagree with what the send path will accept.
from actions.handlers.smtp_send import _normalize_addr
from actions.handlers.smtp_send import _validate_recipient

# Above this the UI warns prominently rather than just listing them.
LARGE_THREAD_CC = 25
# Hard cap, leaving headroom under smtp_send._MAX_RECIPIENTS (100) for To + Bcc.
MAX_CC = 90


@dataclass(frozen=True)
class ReplyAllRecipients:
    """What a Reply-all should pre-fill. A proposal, not a decision — the user
    sees and can edit every one of these before anything is sent."""

    to: list[str]
    cc: list[str]
    dropped_self: bool
    truncated: int

    @property
    def large_thread(self) -> bool:
        return len(self.cc) > LARGE_THREAD_CC


# BARE addresses only — never `Name <addr>`.
#
# The composer's Cc field is a comma-separated BARE-address field: the web splits
# it on "," (`splitAddrs`) and the send path validates each token against
# `_ADDR_RE = ^[^@\s]+@[^@\s]+\.[^@\s]+$`, which permits no whitespace and no
# angle brackets. So a display form fails at send time — and not only the
# comma-bearing kind: plain `Alice Smith <alice@x.com>` fails too, because of the
# space. Returning display forms would have made Reply-all 422 on essentially
# every real thread, since most participants have a display name.
#
# The first draft did exactly that, and each half was individually correct: the
# service preserved display names faithfully, the composer split on commas
# faithfully. The defect lived in the CONTRACT between them, which is why unit
# tests on either side passed. Display names are only worth carrying if the whole
# chain carries them, and this one does not.


def reply_all_recipients(
    headers: dict[str, str],
    *,
    own_addr: str,
    sender_addr: str,
) -> ReplyAllRecipients:
    """Cc = the original's ``To`` + ``Cc``, minus the user and minus the sender.

    Returns **bare, normalised** addresses (see the note above the function) — not
    ``Name <addr>`` — because that is the only form the composer's Cc field and the
    send path's ``_ADDR_RE`` both accept.

    Exclusions, in order, each for a stated reason:

    1. **Empty parsed address** — group syntax like ``undisclosed-recipients:;``
       yields one; it is not a person.
    2. **The user's own address** — Cc-ing yourself on every reply is the single
       most visible way to get this feature wrong. Matched normalised, so
       ``Tom <TOM@Example.COM>`` is recognised as ``tom@example.com``.
    3. **The sender** — already in ``to``; leaving them in ``cc`` produces a
       duplicate the user has to delete by hand every time.
    4. **Duplicates** — first occurrence wins. Order follows the original thread
       rather than a re-sorted set.
    5. **Over** :data:`MAX_CC` — truncated, and the count is reported so the
       composer can say so *before* the send rather than after.
    """
    raw = getaddresses([headers.get("To", "") or "", headers.get("Cc", "") or ""])
    own = _normalize_addr(own_addr)
    sender = _normalize_addr(sender_addr)

    cc: list[str] = []
    seen: set[str] = set()
    dropped_self = False

    for _name, addr in raw:
        key = _normalize_addr(addr)
        if not key:
            continue
        # `getaddresses` PARSES, it does not validate — a bare token like
        # `not-an-address` comes back as a recipient with an empty display name.
        # Dropping it here rather than at send time matters: the send path would
        # refuse it anyway, but only after the user had seen it in the composer,
        # approved it, and watched the send fail for a reason nothing explains.
        # Same validator the send path uses, so the two cannot disagree.
        try:
            _validate_recipient(addr)
        except ValueError:
            continue
        if own and key == own:
            dropped_self = True
            continue
        if sender and key == sender:
            continue
        if key in seen:
            continue
        seen.add(key)
        # `key`, not `addr`: normalised (lowercased, display name stripped) so what
        # reaches the composer is exactly what the send path will accept.
        cc.append(key)

    truncated = max(0, len(cc) - MAX_CC)
    if truncated:
        cc = cc[:MAX_CC]

    return ReplyAllRecipients(
        to=[sender_addr] if sender_addr else [],
        cc=cc,
        dropped_self=dropped_self,
        truncated=truncated,
    )
