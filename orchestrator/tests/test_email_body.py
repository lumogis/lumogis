# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Body text extraction for the read view (LUM-810).

`extract_text` is the single body-text authority: the read view, the
classifier and the summariser all go through it. Two properties matter more
than any individual fixture.

**HTML-only mail must yield text.** The shipped `_body_to_text` collected
`text/plain` parts only, so an HTML-only multipart produced `""` and an
HTML-only single-part produced raw markup. Both are defects this file pins.

**It consumes attacker-controlled bytes and must never raise.** A malformed
message costs its own body and nothing else.
"""

from __future__ import annotations

import pytest
from services.email_body import annotate_control_chars
from services.email_body import extract_text


def _multipart(plain: str | None, html: str | None) -> bytes:
    """A `multipart/alternative` carrying whichever parts are given."""
    lines = [
        "From: a@example.com",
        "Subject: test",
        'Content-Type: multipart/alternative; boundary="BOUND"',
        "",
    ]
    for ctype, body in (("text/plain", plain), ("text/html", html)):
        if body is None:
            continue
        lines += ["--BOUND", f"Content-Type: {ctype}; charset=utf-8", "", body]
    lines += ["--BOUND--", ""]
    return "\r\n".join(lines).encode("utf-8")


def _single(ctype: str, body: str) -> bytes:
    return "\r\n".join(
        [
            "From: a@example.com",
            "Subject: test",
            f"Content-Type: {ctype}; charset=utf-8",
            "",
            body,
        ]
    ).encode("utf-8")


# ---------------------------------------------------------------------------
# Extraction — the six-fixture corpus
# ---------------------------------------------------------------------------


def test_extract_when_both_parts_present_then_prefers_text_plain():
    raw = _multipart("The plain version.", "<p>The <b>HTML</b> version.</p>")
    out = extract_text(raw)
    assert "The plain version." in out
    assert "HTML" not in out


def test_extract_when_html_only_multipart_then_yields_text_not_empty():
    """The shipped defect: HTML-only multipart produced an empty body."""
    raw = _multipart(None, "<html><body><p>Your order has shipped.</p></body></html>")
    assert "Your order has shipped." in extract_text(raw)


def test_extract_when_html_only_single_part_then_yields_text_not_markup():
    """The shipped defect's twin: raw markup was returned as if it were text."""
    out = extract_text(_single("text/html", "<div><p>Hello there.</p></div>"))
    assert "Hello there." in out
    assert "<div" not in out
    assert "<p>" not in out


def test_extract_when_html_table_then_cells_are_separated():
    """`Pro£129` is unreadable; a tab keeps the columns apart."""
    html = (
        "<table><tr><td>Trail Runner Pro</td><td>£129.00</td></tr>"
        "<tr><td>Shipping</td><td>£4.99</td></tr></table>"
    )
    out = extract_text(_single("text/html", html))
    assert "Trail Runner Pro\t£129.00" in out
    assert "Pro£129" not in out


def test_extract_when_short_two_line_reply_then_both_sentences_survive():
    """Regression guard for the trafilatura failure the spike measured.

    trafilatura silently dropped the *first* sentence of a short reply — a
    truncated message the reader cannot tell is truncated.
    """
    html = "<html><body><p>Yes, that works for me.</p><p>See you at 3.</p></body></html>"
    out = extract_text(_single("text/html", html))
    assert "Yes, that works for me." in out
    assert "See you at 3." in out


def test_extract_when_newsletter_then_headline_survives():
    html = "<body><h2>This week in running</h2><p>Three new routes.</p></body>"
    out = extract_text(_single("text/html", html))
    assert "This week in running" in out
    assert "Three new routes." in out


# ---------------------------------------------------------------------------
# Extraction — hardening
# ---------------------------------------------------------------------------


def test_extract_when_single_part_is_not_text_then_returns_empty():
    """A single-part `image/jpeg` must not be decoded into mojibake "text"."""
    raw = b"\r\n".join(
        [
            b"From: a@example.com",
            b"Content-Type: image/jpeg",
            b"Content-Transfer-Encoding: base64",
            b"",
            b"/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsL",
        ]
    )
    assert extract_text(raw) == ""


def test_extract_when_script_and_style_present_then_they_are_dropped():
    html = (
        "<html><head><style>p{color:red}</style><title>Ignore me</title></head>"
        "<body><script>alert(1)</script><p>Real content.</p></body></html>"
    )
    out = extract_text(_single("text/html", html))
    assert "Real content." in out
    assert "color:red" not in out
    assert "alert(1)" not in out
    assert "Ignore me" not in out


def test_extract_when_head_carries_a_meta_tag_then_the_body_survives():
    """`meta` and `link` are VOID — they have no end tag.

    They were in the drop set, which is a depth counter, so one `<meta charset>`
    incremented suppression forever and emptied the entire message. Virtually
    every real HTML email carries one, and the original fixtures missed it
    because `<style>` and `<title>` both close.
    """
    html = (
        "<html><head>"
        '<meta charset="utf-8">'
        '<link rel="stylesheet" href="x.css">'
        "<title>Ignore me</title>"
        "</head><body><p>Real body text</p></body></html>"
    )
    out = extract_text(_single("text/html", html))
    assert "Real body text" in out
    assert "Ignore me" not in out


def test_extract_when_forwarded_message_is_attached_then_the_outer_body_wins():
    """`walk()` descends into `message/rfc822`.

    The attached message's `text/plain` would otherwise be preferred over the
    real HTML body — the reader shown an attachment's contents as if it were
    the message they opened.
    """
    from email.message import EmailMessage

    inner = EmailMessage()
    inner["From"] = "orig@example.com"
    inner.set_content("INNER attached message text")

    outer = EmailMessage()
    outer["From"] = "a@example.com"
    outer.set_content("<p>THE ACTUAL BODY</p>", subtype="html")
    outer.add_attachment(inner, disposition="attachment")

    out = extract_text(outer.as_bytes())
    assert "THE ACTUAL BODY" in out
    assert "INNER attached message text" not in out


def test_extract_when_charset_is_unknown_then_the_message_is_not_discarded():
    """`errors="replace"` covers bad bytes, not an unregistered codec NAME.

    `iso-8859-8-i` and friends raise `LookupError`, which used to take the whole
    message down — including sibling parts that decode fine.
    """
    raw = b"Content-Type: text/plain; charset=iso-8859-8-i\r\n\r\nHello there"
    assert "Hello there" in extract_text(raw)


def test_extract_when_table_is_pretty_printed_then_cells_stay_separated():
    """The tab the receipt fixture relies on must survive real-world markup.

    Inter-tag newlines put the `\\t` next to a newline, where the whitespace
    collapse ate it — reproducing exactly the run-together output the cell
    separator exists to prevent, on the formatting most senders actually emit.
    """
    html = "<table>\n  <tr>\n    <td>Trail Runner Pro</td>\n    <td>£129.00</td>\n  </tr>\n</table>"
    out = extract_text(_single("text/html", html))
    assert "Trail Runner Pro\t£129.00" in out


def test_extract_when_pre_block_then_its_whitespace_is_preserved():
    html = "<body><pre>line one\n  indented two</pre></body>"
    out = extract_text(_single("text/html", html))
    assert "line one\n  indented two" in out


def test_extract_when_markup_grows_then_the_cap_truncates_to_the_same_prefix():
    """The parser input is capped, proven **without** a clock.

    Two inputs that differ only in how much repeated markup follows the cap
    must produce the *same* text, because both are truncated to the same
    256 KiB prefix. If the cap ever stops applying, the 16x output grows and
    this fails — deterministically, on any machine, under any load.
    """
    from services.email_body import _MAX_PARSE_BYTES

    unit = "<p>filler</p>"
    reps = (_MAX_PARSE_BYTES // len(unit)) + 1  # first size already exceeds the cap

    at_cap = extract_text(_single("text/html", unit * reps))
    well_over = extract_text(_single("text/html", unit * reps * 16))

    assert at_cap == well_over, "16x the input changed the output — the cap did not apply"
    assert at_cap, "the truncated prefix should still yield text"


def test_extract_when_markup_is_pathological_then_parse_cost_does_not_track_input_size():
    """Cost must not track sender-chosen size.

    Measured 2026-08-06: 1 MiB of bare "<" cost 2.2s in `html.parser` — the
    whole of the route's 2.5s p95 budget before any network time. Linear, so
    not the CVE-2025-6069 blowup, but linear-and-slow is still an input a
    sender picks. Capped, it is flat.

    **This asserts a RATIO, not a duration.** The previous version asserted
    `worst < 2.0s`, and its own comment claimed that was "not a machine-specific
    timing" — which was false: a wall-clock threshold is exactly that. It
    measured 4.51s on the shared CI runner (three runners plus Docker, Qdrant
    and Postgres on one laptop) against ~0.55s here, and because the suite runs
    with `-x` it took ~2700 unrelated tests down with it for four merges.

    A ratio survives contention: load inflates both measurements together.
    Uncapped the growth is linear, so 16x the input is ~16x the time; capped it
    is ~1x. The bound below sits between those with 4x of margin either side.
    """
    import time

    from services.email_body import _MAX_PARSE_BYTES

    def _fastest(size: int) -> float:
        """Best of three. Contention only ever ADDS time, so the minimum is the
        estimator least polluted by whatever else the runner is doing."""
        payload = _single("text/html", "<" * size)
        best = float("inf")
        for _ in range(3):
            start = time.perf_counter()
            extract_text(payload)
            best = min(best, time.perf_counter() - start)
        return best

    baseline = _fastest(_MAX_PARSE_BYTES)
    sixteen_x = _fastest(_MAX_PARSE_BYTES * 16)
    ratio = sixteen_x / baseline if baseline > 0 else 0.0

    assert ratio <= 4.0, (
        f"parse cost tracks input size: 16x input took {ratio:.1f}x the time "
        f"({baseline:.3f}s → {sixteen_x:.3f}s). Uncapped this is ~16x."
    )


def test_extract_when_body_is_huge_but_legitimate_then_text_still_survives():
    """The cap must not eat real mail — the character cap is what truncates."""
    html = "<p>Opening line.</p>" + ("<p>filler filler filler</p>" * 20_000)
    out = extract_text(_single("text/html", html))
    assert "Opening line." in out
    assert len(out) > 20_000, "a long newsletter should still produce a long body"


def test_a_short_message_carrying_a_large_attachment_is_still_readable():
    """The parse cap bounds the PARSER, not the download.

    Capping the whole-message size instead would refuse an ordinary one-line
    email with a photo on it — measured at 1.2 MB and 0.05s to parse. Two
    different objects, two different bounds.
    """
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"] = "mum@example.com"
    msg.set_content("<p>Here's the photo from Sunday.</p>", subtype="html")
    msg.add_attachment(
        b"\xff\xd8" + b"P" * 900_000, maintype="image", subtype="jpeg", filename="sunday.jpg"
    )

    assert "Here's the photo from Sunday." in extract_text(msg.as_bytes())


def test_extract_when_sender_emits_the_internal_pre_fences_then_nothing_breaks():
    """The verbatim fences are an implementation detail, not a trust boundary.

    A sender who plants them in their own text must not be able to smuggle a
    region past the tidy pass, crash the parse, or leave the markers visible.
    """
    hostile = "before  middle  after"
    out = extract_text(_single("text/html", f"<body><p>{hostile}</p></body>"))
    assert "" not in out
    assert "" not in out
    assert "before" in out and "after" in out


def test_extract_when_sender_doubles_the_close_fence_then_none_leaks_through():
    """The security review's finding: the exit strip paired each open with the
    FIRST close in its chunk, so a second U+F8F1 rode through into the rendered
    body. Fences are now removed from sender text on the way IN, which is what
    makes "every fence in the stream is ours, and balanced" actually true.
    """
    hostile = "a\uf8f0b\uf8f1c\uf8f1d"
    out = extract_text(_single("text/html", f"<body><p>{hostile}</p></body>"))
    assert "\uf8f0" not in out
    assert "\uf8f1" not in out
    assert "abcd" in out


def test_extract_when_sender_fences_plain_text_then_tidying_is_not_skipped():
    """The same in-band trick on the PLAIN path, where there is no `<pre>` at all.

    A balanced pair leaked nothing, but it did exempt the sender's own text from
    whitespace tidying — a formatting channel they should not have.
    """
    raw = _single("text/plain", "one\uf8f0keep     these     spaces\uf8f1 end")
    out = extract_text(raw)
    assert "\uf8f0" not in out and "\uf8f1" not in out
    assert "     " not in out, "sender-planted fences must not skip the tidy pass"


def test_extract_when_text_wraps_across_source_lines_then_it_reflows():
    """Source newlines inside a paragraph are not line breaks in HTML."""
    html = "<body><p>One sentence\n   split across\n   source lines.</p></body>"
    out = extract_text(_single("text/html", html))
    assert "One sentence split across source lines." in out


def test_extract_when_text_plain_is_whitespace_only_then_html_is_used():
    raw = _multipart("   \r\n  \r\n", "<p>The real message.</p>")
    assert "The real message." in extract_text(raw)


def test_extract_when_nested_multipart_related_then_text_is_found():
    """`multipart/related` inside `multipart/alternative` — a very common shape."""
    raw = "\r\n".join(
        [
            "From: a@example.com",
            'Content-Type: multipart/alternative; boundary="OUTER"',
            "",
            "--OUTER",
            'Content-Type: multipart/related; boundary="INNER"',
            "",
            "--INNER",
            "Content-Type: text/html; charset=utf-8",
            "",
            "<p>Nested but readable.</p>",
            "--INNER--",
            "--OUTER--",
            "",
        ]
    ).encode("utf-8")
    assert "Nested but readable." in extract_text(raw)


def test_extract_when_headers_are_missing_then_payload_is_still_read():
    """A headerless blob parses as `text/plain` — reading it is the faithful answer.

    Deliberately not an empty-string assertion: the error contract for
    malformed MIME is "body with replacement chars, never a crash", not
    "silently nothing". Dropping recoverable text would be the same
    silent-truncation failure the trafilatura rejection was about.
    """
    assert extract_text(b"this is not a MIME message at all") == "this is not a MIME message at all"


def test_extract_when_charset_is_undecodable_then_replaces_and_does_not_raise():
    raw = b"\r\n".join(
        [
            b"Content-Type: text/plain; charset=utf-8",
            b"",
            b"caf\xff\xfe and more",
        ]
    )
    out = extract_text(raw)
    assert "and more" in out


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"\x00\x01\x02\xff\xfe",
        b'Content-Type: multipart/mixed; boundary="x"\r\n\r\n--x\r\n',
        bytes(range(256)) * 4,
    ],
)
def test_extract_when_fuzzed_bytes_then_never_raises(payload):
    """The trust boundary. Any exception here is a route 500 on hostile mail."""
    assert isinstance(extract_text(payload), str)


# ---------------------------------------------------------------------------
# Control characters — marked, never stripped
# ---------------------------------------------------------------------------


def test_annotate_when_bidi_override_then_marked_and_raw_char_removed():
    """Trojan Source. The sentinel format is exact, not illustrative."""
    out = annotate_control_chars("pay ‮alice‬ now")
    assert "␎U+202E␏" in out
    assert "‮" not in out
    assert "alice" in out


def test_annotate_when_bidi_isolate_then_marked():
    out = annotate_control_chars("a⁦b⁩c")
    assert "␎U+2066␏" in out
    assert "␎U+2069␏" in out


def test_annotate_when_zero_width_then_left_unmarked():
    """ZWJ is structural in emoji; ZWNJ is orthographically required in Persian.

    Annotating them renders a family emoji or a Farsi sentence as sentinel
    soup — inverting the faithfulness principle in the name of upholding it,
    and training the reader to ignore the one sentinel that matters.
    """
    family = "\U0001f468‍\U0001f469‍\U0001f467"
    persian = "می‌خواهم"
    out = annotate_control_chars(f"{family} {persian}﻿")
    assert out == f"{family} {persian}﻿"
    assert "U+200D" not in out
    assert "U+FEFF" not in out


def test_annotate_when_no_control_chars_then_text_is_unchanged():
    assert annotate_control_chars("Ordinary text.") == "Ordinary text."
