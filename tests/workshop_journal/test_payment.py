"""Payment and identity numbers are refused in event text and removed from kept documents."""
from __future__ import annotations

import pytest

from workshop_journal.conftest import WORKSHOP, new_id, progress
from core.workshop_journal import artifacts as art
from core.workshop_journal import payment
from core.workshop_journal.journal import Journal

CARD = "4242 4242 4242 4242"            # a published test number
IBAN = "GB82 WEST 1234 5698 7654 32"   # the standard example IBAN


@pytest.mark.parametrize("text", [
    f"card {CARD} ok", "4242424242424242", "4242-4242-4242-4242", "3782 822463 10005", "6011111111111117 end",
    f"pay to {IBAN}", "DE89370400440532013000", "ssn 123-45-6789 here", "routing number 021000021", "Account no: 12345678", "acct #: 000123456789",
])
def test_payment_and_identity_numbers_are_found(text):
    assert payment.found(text)
    cleaned, count = payment.scrub(text)
    assert count >= 1 and payment.REMOVED in cleaned and not payment.found(cleaned)


@pytest.mark.parametrize("text", [
    "4242 4242 4242 4243",                                    # fails the check digit
    "10000000-0000-4000-8000-000000000001", f"event {new_id()} done", "sha " + "1234567890123456" * 4,
    "2026-10-08T20:00:00Z", "version 1.2.3", "1700000000000000", "call 555-12-3456x", "000-12-3456", "666-12-3456",
    "ticket 1234567", "GB00 0000 0000 0000 0000 00", "build 20261008-1630", "pin " + "a" * 64,
])
def test_ordinary_numbers_are_left_alone(text):
    assert not payment.found(text), text


def test_event_text_with_a_payment_number_is_refused_everywhere_text_is_taken(root):
    j = Journal(root, WORKSHOP, lock_timeout=0.3)
    refused = j.append(progress(f"the card is {CARD}"))
    assert (refused.status, refused.reason) == ("invalid_input", "text:payment_detail_shaped")
    assert j.append(progress("a normal sentence with 12345 in it")).committed


def test_a_kept_document_has_payment_numbers_removed_and_counted():
    item = art.prepare(f"Invoice for {IBAN}, paid by {CARD}. SSN 123-45-6789.\n".encode())
    assert item.redactions == 3 and CARD.encode() not in item.data and IBAN.encode() not in item.data and b"123-45-6789" not in item.data
    assert item.original_sha256 != item.retained_sha256


def test_payment_numbers_in_a_dropped_handoff_never_reach_the_workshop_folder(root, tmp_path):
    from pathlib import Path
    from core.workshop_journal import inbox as ib
    from workshop_journal.test_inbox import HEADER, NAME
    j = Journal(root, WORKSHOP, lock_timeout=0.3)
    j.append(progress("seed"))
    folder = Path(root) / WORKSHOP / "inbox"
    folder.mkdir(mode=0o700)
    (folder / NAME).write_text(HEADER + f"Pay with {CARD}\n")
    out = ib.Inbox(j, settle=0).scan(apply=True)[0]
    assert out.status == "ingested" and out.redactions == 1
    for path in (Path(root) / WORKSHOP).rglob("*"):
        if path.is_file() and path.parent != folder:
            assert CARD.encode() not in path.read_bytes() and b"4242424242424242" not in path.read_bytes(), path
