# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Quoted-reply stripping for the search index (LUM-826).

The asymmetry that shapes every test here: duplicate hits are annoying, a
message that cannot be found is a broken feature. So the cases that matter
most are not "does it strip" — they are the ones where stripping must
**refuse**.

`strip_quotes` returns ``None`` for "index the original", which the schema
turns into ``text_stripped IS NULL`` and
``to_tsvector('english', COALESCE(text_stripped, text))``. A test asserting
``is None`` is therefore asserting a real, observable indexing behaviour, not
an implementation detail.
"""

from __future__ import annotations

import pytest
from services.email_quote_strip import strip_quotes

_GMAIL_REPLY = """Sounds good, Tuesday works.

On Mon, 4 Aug 2026 at 09:12, Alice <alice@example.com> wrote:
> Can we move the radiator service to Tuesday?
> The plumber said either day is fine.
>
> Alice
"""

_OUTLOOK_REPLY = """Approved, go ahead.

-----Original Message-----
From: Alice <alice@example.com>
Sent: Monday, 4 August 2026 09:12
Subject: Radiator service

Can we move the radiator service to Tuesday?
"""


# ---------------------------------------------------------------------------
# Stripping the thing it exists to strip
# ---------------------------------------------------------------------------


def test_a_gmail_style_quote_block_is_removed():
    out = strip_quotes(_GMAIL_REPLY)

    assert out is not None
    assert "Sounds good, Tuesday works." in out
    assert "radiator" not in out.lower(), "the quoted history must not be indexed"
    assert "wrote:" not in out, "the attribution line goes with the quote"


def test_an_outlook_divider_cuts_the_copied_message():
    out = strip_quotes(_OUTLOOK_REPLY)

    assert out is not None
    assert "Approved, go ahead." in out
    assert "radiator" not in out.lower()
    assert "alice@example.com" not in out, "the copied headers go too"


@pytest.mark.parametrize(
    "attribution",
    [
        "On Mon, 4 Aug 2026 at 09:12, Alice <a@b.example> wrote:",
        "Am 04.08.2026 um 09:12 schrieb Alice:",
        "Le 4 août 2026 à 09:12, Alice a écrit :",
    ],
)
def test_attribution_lines_in_several_languages(attribution):
    """A German household is the whole reason 'english' + prefix was chosen."""
    out = strip_quotes(f"Passt, bis Dienstag.\n\n{attribution}\n> Heizung am Dienstag?\n")

    assert out is not None
    assert "Heizung" not in out
    assert "Dienstag" in out, "the reply's own text survives"


def test_nested_quote_depth_is_all_removed():
    out = strip_quotes("Fine by me.\n\n> > original question\n> intermediate reply\n")

    assert out is not None
    assert "original question" not in out
    assert "intermediate reply" not in out


# ---------------------------------------------------------------------------
# Refusing to strip -- the cases that make this hard
# ---------------------------------------------------------------------------


def test_a_reply_written_inside_a_quoted_block_survives():
    """The case the ticket names as the reason this was not done in LUM-819.

    Interleaved answers sit BETWEEN quoted lines. Cutting everything after the
    first quote marker -- the obvious implementation -- loses them silently,
    and the message becomes unfindable by its own content.
    """
    text = (
        "On Mon, 4 Aug 2026, Alice wrote:\n"
        "> Shall we replace the radiator?\n"
        "Yes, the plumber quoted 400 euro.\n"
        "> And the boiler?\n"
        "That one can wait until spring.\n"
    )

    out = strip_quotes(text)

    assert out is not None
    assert "plumber quoted 400 euro" in out
    assert "can wait until spring" in out
    assert "Shall we replace" not in out


def test_a_message_that_is_entirely_quoted_is_not_stripped_to_nothing():
    """A forward whose whole substance is the quoted part must stay findable.

    Stripping would leave an empty index entry, so the stripper declines and
    the full text is indexed instead.
    """
    text = "On Mon, Alice wrote:\n> The radiator invoice is attached, 400 euro.\n"

    assert strip_quotes(text) is None


def test_a_forwarded_message_is_left_alone():
    """``--- Forwarded message ---`` introduces content, not history."""
    text = (
        "FYI\n\n"
        "---------- Forwarded message ----------\n"
        "From: Plumbers Ltd <office@plumbers.example>\n"
        "Subject: Invoice 4021\n\n"
        "Your radiator service is booked for Tuesday.\n"
    )

    out = strip_quotes(text)

    # Either untouched (None) or stripped while KEEPING the forwarded substance.
    assert out is None or "radiator service is booked" in out


def test_plain_mail_with_no_quotes_is_declined_rather_than_duplicated():
    """No markers means no benefit -- storing a second identical copy is waste."""
    assert strip_quotes("The radiator service is booked for Tuesday.") is None


def test_prose_containing_the_word_wrote_is_not_mistaken_for_an_attribution():
    """``On Tuesday she wrote:`` is ordinary text, and the pattern must not eat it."""
    text = "On Tuesday she wrote:\nthe cheque is in the post, and the radiator works.\n"

    out = strip_quotes(text)

    # The attribution pattern may match this line, but the content below is not
    # ``>``-quoted, so it must survive either way.
    assert out is None or "cheque is in the post" in out


def test_a_terse_reply_is_still_indexable():
    """ "Yes, agreed." is a real message; the substantive-content floor is a
    guard against stripping to nothing, not a judgement about brevity."""
    out = strip_quotes("Yes, agreed.\n\n> Shall we book the radiator service?\n")

    assert out is not None
    assert "Yes, agreed." in out


def test_empty_and_none_are_handled():
    assert strip_quotes("") is None
    assert strip_quotes(None) is None


# ---------------------------------------------------------------------------
# The amplification the ticket asks to be re-measured
# ---------------------------------------------------------------------------


def test_amplification_on_a_thread_is_reduced_and_recorded():
    """LUM-826 asks for the ratio to be re-measured, so measure it in a test.

    A four-deep thread where each reply adds one short line and quotes
    everything before it -- the shape that produced ~8.6x in the original
    measurement. Asserting a floor rather than an exact number keeps this from
    being a brittle golden value while still failing if stripping regresses.
    """
    new_line = "Confirmed, Tuesday suits us.\n"
    thread, stored_full, stored_stripped = "", 0, 0
    for depth in range(4):
        message = new_line + "\n" + "".join(f"> {ln}\n" for ln in thread.splitlines())
        thread = message
        stored_full += len(message)
        out = strip_quotes(message)
        stored_stripped += len(out if out is not None else message)

    amplification_before = stored_full / (len(new_line) * 4)
    amplification_after = stored_stripped / (len(new_line) * 4)

    assert amplification_before > 2.0, "the test corpus must actually amplify"
    assert amplification_after < amplification_before / 2, (
        f"stripping should at least halve amplification; "
        f"before={amplification_before:.1f}x after={amplification_after:.1f}x"
    )
