# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``attachment_ids`` through the four send paths (LUM-695).

The load-bearing property: **attachment bytes never enter the action payload.**
``action_proposals.payload`` is JSONB, is returned verbatim by
``PendingProposalItem``, and is rendered on the approval card — a base64 blob
there would bloat the row, cross the wire twice, and land in a UI that expects a
summary. So the payload carries references and the handlers resolve them at
execute time.

That resolution can fail, and where it fails decides how it fails. Ownership is
checked at **propose** time, in our own route, before ``create_proposal`` — a
Pydantic model cannot check DB ownership, and ``create_proposal`` runs only
``model.model_validate``. Anything that changes between propose and execute is a
``PermanentActionError``, not an HTTP status: the handler runs via the executor
after approval, where there is no HTTP surface at all.
"""

from __future__ import annotations

import pytest
from actions.errors import PermanentActionError
from actions.handlers import smtp_send
from adapters.email_smtp_sender import AttachmentPart
from pydantic import ValidationError

from services import email_attachment_staging as staging

# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------


def test_every_send_input_model_accepts_attachment_ids() -> None:
    assert smtp_send.EmailReplyInput(
        signal_id="s", body="b", attachment_ids=["a"]
    ).attachment_ids == ["a"]
    assert smtp_send.EmailComposeInput(
        source_id="src", to=["x@y.com"], subject="s", body="b", attachment_ids=["a"]
    ).attachment_ids == ["a"]
    assert smtp_send.EmailForwardInput(
        signal_id="s", to=["x@y.com"], body="b", attachment_ids=["a"]
    ).attachment_ids == ["a"]
    assert smtp_send.EmailSaveDraftInput(
        signal_id="s", body="b", attachment_ids=["a"]
    ).attachment_ids == ["a"]


def test_attachment_ids_default_to_empty_so_existing_payloads_still_validate() -> None:
    """Every payload written before LUM-695 must keep validating unchanged."""
    assert smtp_send.EmailReplyInput(signal_id="s", body="b").attachment_ids == []
    assert (
        smtp_send.EmailComposeInput(
            source_id="src", to=["x@y.com"], subject="s", body="b"
        ).attachment_ids
        == []
    )


def test_attachment_ids_are_capped() -> None:
    with pytest.raises(ValidationError):
        smtp_send.EmailReplyInput(
            signal_id="s", body="b", attachment_ids=[f"id-{i}" for i in range(11)]
        )


def test_attachment_bytes_never_appear_in_the_action_payload() -> None:
    """The payload carries ids only — ``extra="forbid"`` refuses anything else."""
    with pytest.raises(ValidationError):
        smtp_send.EmailComposeInput(
            source_id="src",
            to=["x@y.com"],
            subject="s",
            body="b",
            attachments=[{"filename": "a.pdf", "data": "QUJD"}],
        )


# ---------------------------------------------------------------------------
# Resolution at execute time
# ---------------------------------------------------------------------------


class _Resolved:
    def __init__(self, name: str, data: bytes) -> None:
        self.id = name
        self.filename = name
        self.mime_type = "application/pdf"
        self.path = _FakePath(data)


class _FakePath:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read_bytes(self) -> bytes:
        return self._data

    def exists(self) -> bool:
        return True


def test_resolve_attachment_parts_reads_the_staged_bytes(monkeypatch) -> None:
    monkeypatch.setattr(staging, "resolve_all", lambda uid, ids: [_Resolved("a.pdf", b"AAA")])

    parts = smtp_send._resolve_attachment_parts(["id-1"], user_id="u1")

    assert parts == [AttachmentPart(filename="a.pdf", mime_type="application/pdf", data=b"AAA")]


def test_no_attachment_ids_resolves_to_no_parts_without_touching_staging(monkeypatch) -> None:
    def _boom(*a, **k):
        raise AssertionError("staging must not be consulted for an empty list")

    monkeypatch.setattr(staging, "resolve_all", _boom)

    assert smtp_send._resolve_attachment_parts([], user_id="u1") == []


def test_execute_time_attachment_loss_is_a_permanent_action_error(monkeypatch) -> None:
    """Dead-letters with a retry affordance — NOT an HTTP 422.

    The handler runs via the executor after approval. There is no HTTP surface
    there, so a status code would be unreachable code pretending to be a contract.
    """

    def _gone(uid, ids):
        raise staging.StagedAttachmentUnavailable("swept")

    monkeypatch.setattr(staging, "resolve_all", _gone)

    with pytest.raises(PermanentActionError) as exc:
        smtp_send._resolve_attachment_parts(["id-1"], user_id="u1")

    assert exc.value.code == "attachment_unavailable"


def test_swept_staged_file_refuses_the_send_rather_than_half_sending(monkeypatch) -> None:
    """A sweep mid-send must refuse outright — nothing partially sent."""

    def _partial(uid, ids):
        raise staging.StagedAttachmentUnavailable("one of them is gone")

    monkeypatch.setattr(staging, "resolve_all", _partial)

    with pytest.raises(PermanentActionError):
        smtp_send._resolve_attachment_parts(["ok-1", "swept-2"], user_id="u1")


def test_read_failure_on_a_staged_file_is_also_permanent(monkeypatch) -> None:
    class _Unreadable(_Resolved):
        def __init__(self):
            super().__init__("a.pdf", b"")
            self.path = self

        def read_bytes(self):
            raise OSError("disk gone")

        def exists(self):
            return True

    monkeypatch.setattr(staging, "resolve_all", lambda uid, ids: [_Unreadable()])

    with pytest.raises(PermanentActionError) as exc:
        smtp_send._resolve_attachment_parts(["id-1"], user_id="u1")

    assert exc.value.code == "attachment_unavailable"


# ---------------------------------------------------------------------------
# The approval-card summary
# ---------------------------------------------------------------------------


def test_annotate_send_reports_a_file_count_and_total_size(monkeypatch) -> None:
    """The user must SEE that they are about to send files."""
    monkeypatch.setattr(smtp_send, "_is_first_contact", lambda uid, addrs: False)
    monkeypatch.setattr(staging, "total_size", lambda uid, ids: 1_258_291)

    ann = smtp_send._annotate_send({"to": ["x@y.com"], "attachment_ids": ["a", "b", "c"]}, "u1")

    assert ann["attachment_count"] == 3
    assert ann["attachment_bytes"] == 1_258_291


def test_annotate_send_omits_the_summary_when_there_are_no_attachments(monkeypatch) -> None:
    monkeypatch.setattr(smtp_send, "_is_first_contact", lambda uid, addrs: False)

    ann = smtp_send._annotate_send({"to": ["x@y.com"]}, "u1")

    assert "attachment_count" not in ann


def test_annotate_send_still_fails_closed_on_first_contact(monkeypatch) -> None:
    """The LUM-650 fail-closed contract must survive the new keys.

    The invariant is ``first_contact: True`` on any uncertainty — that is the key
    ``executor._resolve_force_ask`` reads. The attachment summary riding alongside
    is additive and does not weaken it; asserting the whole dict were equal to
    ``{"first_contact": True}`` would pin the *absence* of the summary, which is
    a bug (the approver still needs to see they are sending files) rather than
    a contract.
    """

    def _boom(*a, **k):
        raise RuntimeError("lookup down")

    monkeypatch.setattr(smtp_send, "_is_first_contact", _boom)

    ann = smtp_send._annotate_send({"to": ["x@y.com"], "attachment_ids": ["a"]}, "u1")

    assert ann["first_contact"] is True


def test_the_attachment_summary_survives_the_fail_closed_paths(monkeypatch) -> None:
    """ "The user must SEE that they are about to send files" has no exceptions.

    ``_annotate_send`` fails closed to ``first_contact: True`` when it cannot
    resolve recipients or the lookup raises. Those are exactly the paths where an
    approver is being asked to look hardest — dropping the file count there is
    the wrong place to lose it.
    """
    # Path 1: no resolvable recipient.
    monkeypatch.setattr(staging, "total_size", lambda uid, ids: 4096)
    ann = smtp_send._annotate_send({"attachment_ids": ["a", "b"]}, "u1")
    assert ann["first_contact"] is True
    assert ann["attachment_count"] == 2
    assert ann["attachment_bytes"] == 4096

    # Path 2: the first-contact lookup itself raises.
    def _boom(*a, **k):
        raise RuntimeError("lookup down")

    monkeypatch.setattr(smtp_send, "_is_first_contact", _boom)
    ann = smtp_send._annotate_send({"to": ["x@y.com"], "attachment_ids": ["a"]}, "u1")
    assert ann["first_contact"] is True
    assert ann["attachment_count"] == 1


def test_fail_closed_annotation_still_omits_the_summary_with_no_attachments(monkeypatch) -> None:
    def _boom(*a, **k):
        raise RuntimeError("lookup down")

    monkeypatch.setattr(smtp_send, "_is_first_contact", _boom)

    assert smtp_send._annotate_send({"to": ["x@y.com"]}, "u1") == {"first_contact": True}


def test_the_card_counts_re_attached_originals_too(monkeypatch) -> None:
    """LUM-712: a forwarded original's files are files the user is sending.

    Counting only staged uploads would under-report exactly the case where
    someone is passing another party's document onward.
    """
    monkeypatch.setattr(smtp_send, "_is_first_contact", lambda uid, addrs: False)
    monkeypatch.setattr(staging, "total_size", lambda uid, ids: 1024)

    ann = smtp_send._annotate_send(
        {"to": ["x@y.com"], "attachment_ids": ["a"], "forward_part_indexes": [0, 1]}, "u1"
    )

    assert ann["attachment_count"] == 3


def test_a_forward_only_of_originals_still_reports_a_count(monkeypatch) -> None:
    monkeypatch.setattr(smtp_send, "_is_first_contact", lambda uid, addrs: False)

    ann = smtp_send._annotate_send({"to": ["x@y.com"], "forward_part_indexes": [0, 1]}, "u1")

    assert ann["attachment_count"] == 2
    # No staged bytes to total, and the originals' sizes are only known at
    # execute — so no byte figure is claimed rather than a wrong one shown.
    assert "attachment_bytes" not in ann


def test_a_mixed_send_marks_its_byte_total_partial(monkeypatch) -> None:
    monkeypatch.setattr(smtp_send, "_is_first_contact", lambda uid, addrs: False)
    monkeypatch.setattr(staging, "total_size", lambda uid, ids: 2048)

    ann = smtp_send._annotate_send(
        {"to": ["x@y.com"], "attachment_ids": ["a"], "forward_part_indexes": [0]}, "u1"
    )

    assert ann["attachment_bytes"] == 2048
    assert ann["attachment_bytes_partial"] is True
