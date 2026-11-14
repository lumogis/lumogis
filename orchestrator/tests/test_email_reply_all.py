# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Reply-all address arithmetic (LUM-705).

Pure function, so every case is exact. The two worth naming:

* **A display name containing a comma is ONE recipient.** ``split(",")`` turns
  ``"Doe, Jane" <jane@x>`` into two malformed addresses, on a send path where the
  mistake is irreversible and visible to the whole thread.
* **"Is this me?" must use the same normaliser as "have I contacted them?"** —
  otherwise the self-filter and the first-contact gate can disagree about the
  same address, and the disagreement is silent.
"""

from __future__ import annotations

from services.email_reply_all import reply_all_recipients

from services import email_reply_all

SENDER = "sender@example.com"
ME = "me@example.com"


def _h(to: str = "", cc: str = "") -> dict[str, str]:
    out = {}
    if to:
        out["To"] = to
    if cc:
        out["Cc"] = cc
    return out


def test_cc_is_the_originals_to_plus_cc_in_order():
    r = reply_all_recipients(
        _h(to=f"{ME}, alice@x.com", cc="bob@x.com, carol@x.com"),
        own_addr=ME,
        sender_addr=SENDER,
    )
    assert r.cc == ["alice@x.com", "bob@x.com", "carol@x.com"]
    assert r.to == [SENDER]


def test_the_users_own_address_is_removed():
    r = reply_all_recipients(_h(to=f"{ME}, alice@x.com"), own_addr=ME, sender_addr=SENDER)
    assert r.cc == ["alice@x.com"]
    assert r.dropped_self is True


def test_own_address_matching_is_case_and_display_name_insensitive():
    r = reply_all_recipients(
        _h(to='"Me, Myself" <ME@Example.COM>, alice@x.com'),
        own_addr="me@example.com",
        sender_addr=SENDER,
    )
    assert r.cc == ["alice@x.com"]
    assert r.dropped_self is True


def test_the_sender_is_not_duplicated_into_cc():
    """They are already in `to`; a duplicate is one the user deletes by hand."""
    r = reply_all_recipients(_h(to=f"{SENDER}, alice@x.com"), own_addr=ME, sender_addr=SENDER)
    assert r.cc == ["alice@x.com"]


def test_duplicates_between_to_and_cc_collapse():
    r = reply_all_recipients(
        _h(to='"Alice Smith" <alice@x.com>', cc="alice@x.com"),
        own_addr=ME,
        sender_addr=SENDER,
    )
    assert r.cc == ["alice@x.com"]


def test_a_display_name_containing_a_comma_is_one_recipient():
    """The split(',') regression."""
    r = reply_all_recipients(
        _h(to='"Doe, Jane" <jane@x.com>, bob@x.com'), own_addr=ME, sender_addr=SENDER
    )
    # ONE recipient, and returned BARE — the display form would be rejected by
    # the send path's _ADDR_RE, which permits neither spaces nor angle brackets.
    assert r.cc == ["jane@x.com", "bob@x.com"]


def test_group_syntax_yields_no_empty_recipients():
    r = reply_all_recipients(
        _h(to="undisclosed-recipients:;, alice@x.com"), own_addr=ME, sender_addr=SENDER
    )
    assert r.cc == ["alice@x.com"]


def test_a_folded_header_is_parsed_as_one_list():
    r = reply_all_recipients(
        _h(to="alice@x.com,\r\n bob@x.com,\r\n\tcarol@x.com"),
        own_addr=ME,
        sender_addr=SENDER,
    )
    assert r.cc == ["alice@x.com", "bob@x.com", "carol@x.com"]


def test_over_the_cap_truncates_and_reports_the_count():
    many = ", ".join(f"u{i}@x.com" for i in range(email_reply_all.MAX_CC + 10))
    r = reply_all_recipients(_h(to=many), own_addr=ME, sender_addr=SENDER)
    assert len(r.cc) == email_reply_all.MAX_CC
    assert r.truncated == 10


def test_large_thread_is_flagged_but_not_truncated():
    n = email_reply_all.LARGE_THREAD_CC + 5
    r = reply_all_recipients(
        _h(to=", ".join(f"u{i}@x.com" for i in range(n))), own_addr=ME, sender_addr=SENDER
    )
    assert len(r.cc) == n
    assert r.truncated == 0
    assert r.large_thread is True


def test_missing_to_and_cc_headers_yield_an_empty_cc_not_an_error():
    r = reply_all_recipients({}, own_addr=ME, sender_addr=SENDER)
    assert r.cc == []
    assert r.to == [SENDER]
    assert r.large_thread is False


def test_a_reply_to_yourself_yields_an_empty_cc():
    """Every other recipient is the user — correct, not a failure."""
    r = reply_all_recipients(_h(to=ME), own_addr=ME, sender_addr=SENDER)
    assert r.cc == []
    assert r.dropped_self is True


def test_normalisation_is_the_same_function_first_contact_uses():
    """A future divergence between "is this me?" and "have I contacted them?"
    must be a test failure, not a subtle bug."""
    from actions.handlers import smtp_send

    assert email_reply_all._normalize_addr is smtp_send._normalize_addr


def test_an_unparseable_address_is_dropped_rather_than_sent():
    r = reply_all_recipients(_h(to="not-an-address, alice@x.com"), own_addr=ME, sender_addr=SENDER)
    assert "alice@x.com" in r.cc
    assert all("@" in c for c in r.cc)


def test_every_returned_address_is_accepted_by_the_send_path():
    """The contract with the consumer, not just with this function.

    The composer joins these with ", " into a bare-address field, the web splits
    them back on ",", and the send path validates each token with ``_ADDR_RE``,
    which permits no whitespace and no angle brackets. Returning ``Name <addr>``
    passed every unit test on both sides and 422'd at send time on essentially
    every real thread, because most participants have a display name. This test
    asserts the seam, which is where that defect lived.
    """
    from actions.handlers.smtp_send import _validate_recipient

    r = reply_all_recipients(
        _h(
            to='"Doe, Jane" <jane@x.com>, Alice Smith <alice@x.com>',
            cc="BOB@X.COM, carol@x.com",
        ),
        own_addr=ME,
        sender_addr=SENDER,
    )

    assert r.cc, "nothing to assert on"
    # Survives the composer round-trip: join, split, validate.
    for token in [s.strip() for s in ", ".join(r.cc).split(",") if s.strip()]:
        _validate_recipient(token)  # raises ValueError if the send path would refuse
