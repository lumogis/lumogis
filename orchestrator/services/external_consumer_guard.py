# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-729 — do not let Lumogis quietly starve an external consumer of the mailbox.

Organize moves classified mail out of `INBOX` into `Lumogis/<label>`. A paperless
mail rule watches **one** folder, default `INBOX`. Both poll on their own clock,
so whichever arrives first wins the message — and modelling the two defaults
(Lumogis 900s, paperless 600s) with arbitrary phase, roughly **one message in
three** is taken before paperless ever sees it.

Nothing reports this. Paperless finds nothing to consume; Lumogis records a
successful organize. The archive of record develops holes at a rate slow enough
never to be noticed.

**A consent gate, not a prohibition.** Both systems are the user's, and a product
that refuses to organize a mailbox because the user also runs paperless is
overreaching. The failure is that the consequence was invisible, not that the
choice was wrong.

Two things live here rather than in `email_organize`, and both are the reason
this is a module at all:

* **The predicate has two arming clauses, and either alone looks complete.**
  Turning folder-sort on arms the trap going forward. `apply_to_existing` arms it
  *retroactively* over the whole mailbox, and an already-sorting user who ticks
  it never crosses the OFF→ON transition. A gate written against only the
  transition — which is what the first draft of the plan specified — lets the
  single most destructive request straight through.
* **Three callers, in two services.** The organize route,
  `email_cleanup.approve()` and the paperless source-create are all moments of
  consent, and gating some and not others fixes the door while leaving the window
  open. That is exactly how LUM-724's three-share-doors problem happened — and
  the third one here was missed on the first pass for the same reason: the whole
  plan reasoned about arming organize *with paperless present*, and never about
  arming paperless *with organize present*. Same harm, mirrored, and if anything
  the likelier ordering.

The copy lives here too, so the two call sites cannot drift into telling the user
two different stories about the same consequence.
"""

from __future__ import annotations

import logging
from typing import Optional

import config

_log = logging.getLogger(__name__)

#: The machine-readable refusal key. 422 rather than 409 at both call sites, to
#: match LUM-702's `full_ingest_required` precondition gate — this is a
#: precondition on the request, not a conflict with server state.
STARVATION_CODE = "external_consumer_may_starve"

#: Turning folder-sort on: mail classified from now on leaves INBOX.
ARM_ENABLE = "enable"
#: `apply_to_existing`: `organize_since` is set to epoch, which widens the
#: standing reconcile sweep across all history. A retroactive bulk move.
ARM_RETROACTIVE = "retroactive"
#: A cleanup window approving at least one moving action.
ARM_BULK_MOVE = "bulk_move"
#: Connecting paperless while a mailbox is ALREADY folder-sorting. The mirror
#: image, and the one the first pass missed: the plan reasoned entirely about
#: arming organize with paperless present, and never about arming paperless with
#: organize present. The harm is identical and the ordering is at least as
#: likely — Lumogis first, paperless later.
ARM_CONNECT_PAPERLESS = "connect_paperless"


def organize_arming(
    *,
    currently_on: bool,
    requested_on: bool,
    apply_to_existing: bool,
) -> Optional[str]:
    """Which arming event, if any, this organize request represents.

    `EmailOrganizeUpdate` is a **full replace** — `organize_folder` is sent on
    every save — so reading the requested value alone would re-gate a user who
    already folder-sorts every time they edit their category list. Consent is for
    arming the trap, not a toll on every subsequent edit.

    But the transition is not the only arming event. Ordered so that
    `apply_to_existing` wins: it is strictly the larger action, and its copy has
    to say so.
    """
    if not requested_on:
        # Turning folder-sort OFF, or leaving it off. Nothing moves; reducing the
        # harm must never be obstructed. Labels alone never move mail either.
        return None
    if apply_to_existing:
        return ARM_RETROACTIVE
    if not currently_on:
        return ARM_ENABLE
    return None


def has_folder_sorting_mailbox(user_id: str) -> bool:
    """Does this user already have a mailbox moving mail out of `INBOX`?

    The mirror of `has_external_consumer`, for the arming event the first pass
    missed: connecting paperless while folder-sort is already on. Same harm, same
    coarseness, same fail-open — and the same WARNING, for the same reason.
    """
    try:
        ms = config.get_metadata_store()
        # SCOPE-EXEMPT: `sources` has no `scope` column (plan §2.10), so
        # `visible_filter` cannot apply. Scoping to the caller is the point: one
        # household member's folder-sort must not gate another's paperless.
        row = ms.fetch_one(
            "SELECT 1 AS present FROM sources "
            "WHERE user_id = %s AND source_type = 'imap' AND active = TRUE "
            "AND organize_folder_enabled = TRUE LIMIT 1",
            (user_id,),
        )
        return row is not None
    except Exception:  # pragma: no cover - exercised via a raising fake store
        _log.warning(
            "external consumer guard failed; proceeding UNGATED for user_id=%s",
            user_id,
            exc_info=True,
        )
        return False


def has_external_consumer(user_id: str) -> bool:
    """Does this user have a paperless source (paused included — LUM-844)?

    Its mail rules may be starved by a move out of the inbox.

    **Deliberately coarse.** It does not ask whether paperless watches *this*
    mailbox — reading paperless's mail rules is LUM-728, which is deferred. So
    some users acknowledge a warning that did not apply to them.

    That is the right error direction. A false positive costs one click; a false
    negative costs documents, silently, at about one in three.

    **Fails OPEN, and says so in the log.** A broken guard must not stop a user
    configuring their own mailbox. But fail-open is not free: on a real database
    outage the caller's own UPDATE fails immediately anyway, so this branch only
    ever absorbs guard-*specific* bugs — and would absorb them invisibly. The
    WARNING is what makes it a failure rather than a silence.
    """
    try:
        return has_external_consumer_strict(user_id)
    except Exception:  # pragma: no cover - exercised via a raising fake store
        _log.warning(
            "external consumer guard failed; proceeding UNGATED for user_id=%s",
            user_id,
            exc_info=True,
        )
        return False


def has_external_consumer_strict(user_id: str) -> bool:
    """The same question, **raising** instead of failing open.

    Same query, opposite failure direction, and the difference is deliberate.

    `has_external_consumer` guards a **configuration write**: if it breaks, the
    right answer is to let the user configure their own mailbox, because the bug
    it prevents is latent while the outage it would cause is immediate.

    This one guards an **autonomous mailbox mutation** — a Do-mode `imap_move`
    that no human is going to see. There the right answer is the opposite: an
    unanswerable question must force the action in front of a person.
    `_compute_annotation` and `_resolve_force_ask` both fail **closed** on a
    raised exception, so raising here is what wires this into that posture.
    Calling the fail-open variant from an annotate hook would silently defeat it.
    """
    ms = config.get_metadata_store()
    # Paused is not gone (LUM-844, amends ADR 192). paperless-ngx runs its own
    # mail rules on its own schedule whether or not Lumogis polls its REST API,
    # so a paused paperless source starves exactly as an active one does. A user
    # who no longer uses paperless deletes the source. This reverses the intent
    # recorded in the test this replaced ("a source they disabled must not gate
    # them at all"), which was written while `active` could never be false.
    #
    # SCOPE-EXEMPT: `sources` has no `scope` column (plan §2.10), so
    # `visible_filter` cannot apply. The user_id predicate is the whole point
    # here — a household member's paperless source must not gate another
    # member's mailbox.
    row = ms.fetch_one(
        "SELECT 1 AS present FROM sources WHERE user_id = %s AND source_type = 'paperless' LIMIT 1",
        (user_id,),
    )
    return row is not None


_MESSAGES = {
    ARM_ENABLE: (
        "You also use paperless. Sorting mail into folders moves it out of your "
        "inbox, and a paperless mail rule that watches the inbox will miss "
        "whatever Lumogis takes first — silently, on both sides."
    ),
    ARM_RETROACTIVE: (
        "You also use paperless. This will move mail you have already received, "
        "not just new mail, out of your inbox. A paperless mail rule that watches "
        "the inbox will never see any of it."
    ),
    ARM_BULK_MOVE: (
        "You also use paperless. Approving this cleanup moves the selected mail "
        "out of your inbox, and a paperless mail rule that watches the inbox will "
        "miss whatever it has not already consumed."
    ),
    ARM_CONNECT_PAPERLESS: (
        "One of your mailboxes already sorts classified mail into folders, which "
        "moves it out of your inbox. If you point a paperless mail rule at that "
        "inbox it will miss whatever Lumogis takes first — silently, on both "
        "sides. Turn folder sorting off for that mailbox, or point paperless "
        "somewhere else."
    ),
}

#: Said in the same breath as the warning, because the alternative is real and
#: available to everyone. `_apply_label` and `_apply_move` are separate IMAP
#: operations, read from two independent booleans, so labels-only leaves the
#: message in INBOX on **every** provider, Gmail or not.
ALTERNATIVE = (
    "Labels alone do not move mail, so paperless still receives everything. "
    "You can also add a paperless mail rule for each Lumogis/<category> folder, "
    "though that is one rule per category and goes stale when you rename one."
)

#: Undo is a real recovery path and the warning may say so — but it must not
#: overclaim. Putting messages back in INBOX does not hand paperless the
#: documents it missed while they were gone.
RECOVERY_CAVEAT = (
    "Undo puts messages back in your inbox. It does not give paperless the "
    "documents it missed while they were away."
)


def message_for(arming: str) -> str:
    """The consequence in the user's terms. Never a bare error code — the whole
    point of the gate is that the consequence was invisible."""
    return _MESSAGES.get(arming, _MESSAGES[ARM_ENABLE])


def refusal_detail(arming: str) -> dict:
    """The 422 body, shared by both call sites so they cannot drift.

    `code` is the stable machine key the web branches on, matching this router's
    convention (`detail={"code": ...}`); `message`, `alternative` and `recovery`
    are copy and may change without a contract break.
    """
    return {
        "code": STARVATION_CODE,
        "message": message_for(arming),
        "alternative": ALTERNATIVE,
        "recovery": RECOVERY_CAVEAT,
    }
