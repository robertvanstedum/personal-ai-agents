"""B4: the shared ledger reader rejects duplicate keys and reads a bounded amount (#305).

A JSON object with a repeated key would otherwise keep only the LAST value, so
accepted:0 followed by accepted:3 could read as a full acknowledgement. The
reader must also never allocate an arbitrarily large malformed file. Local files
and fake Telegram only.
"""

import json
import os
from datetime import datetime, timezone

import pytest

from core.telegram import delivery_ledger as dl

TODAY = "2026-10-16"

ACK = ('{"format": "%s", "date": "%s", "state": "acknowledged", "started_at": "2026-10-16T13:00:00+00:00", '
       '"finished_at": "2026-10-16T13:00:05+00:00", "planned": 3, %s "briefing_sha256": "%s"}')


def _ack_text(accepted_fields):
    return ACK % (dl.FORMAT, TODAY, accepted_fields, "a" * 64)


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    folder = tmp_path / "data" / "curator" / "telegram_delivery"
    folder.mkdir(parents=True)
    monkeypatch.setenv("CURATOR_DELIVERY_DIR", str(folder))
    monkeypatch.chdir(tmp_path)
    return folder / f"{TODAY}.json"


def test_a_well_formed_record_still_reads(ledger):
    ledger.write_text(_ack_text('"accepted": 3,'))
    state, record = dl.read_record(ledger, TODAY)
    assert state == "acknowledged" and record["accepted"] == 3
    assert dl.cron_state(TODAY) == "delivered"


@pytest.mark.parametrize("fields", [
    '"accepted": 0, "accepted": 3,',            # the reviewed case: last value would win
    '"accepted": 3, "accepted": 3,',            # even an identical repeat is refused
])
def test_duplicate_keys_never_prove_delivery(ledger, fields):
    ledger.write_text(_ack_text(fields))
    json.loads(ledger.read_text())               # plain json.loads would accept it...
    assert dl.read_record(ledger, TODAY) == ("invalid", None)   # ...the shared reader does not
    assert dl.cron_state(TODAY) == "hold"


def test_a_nested_duplicate_key_is_refused_too(ledger):
    ledger.write_text('{"format": "x", "date": {"a": 1, "a": 2}}')
    assert dl.read_record(ledger, TODAY) == ("invalid", None)


def test_the_reader_requests_a_bounded_amount_and_refuses_oversize(ledger, monkeypatch):
    ledger.write_bytes(b"{" + b" " * (5 * dl._MAX_BYTES) + b"}")
    sizes, real = [], os.read
    monkeypatch.setattr(dl.os, "read", lambda fd, n: (sizes.append(n), real(fd, n))[1])
    assert dl.read_record(ledger, TODAY) == ("invalid", None)
    assert sizes == [dl._MAX_BYTES + 1]          # one bounded read, never the whole file


def test_exactly_the_limit_is_read_and_one_byte_over_is_refused(ledger):
    body = _ack_text('"accepted": 3,')
    ledger.write_text(body + " " * (dl._MAX_BYTES - len(body)))
    assert dl.read_record(ledger, TODAY)[0] == "acknowledged"
    ledger.write_text(body + " " * (dl._MAX_BYTES - len(body) + 1))
    assert dl.read_record(ledger, TODAY) == ("invalid", None)


def test_a_non_regular_or_symlinked_record_is_refused(ledger, tmp_path):
    ledger.mkdir()
    assert dl.read_record(ledger, TODAY) == ("invalid", None)
    ledger.rmdir()
    real = tmp_path / "elsewhere.json"
    real.write_text(_ack_text('"accepted": 3,'))
    ledger.symlink_to(real)
    assert dl.read_record(ledger, TODAY) == ("invalid", None)


def test_the_bot_holds_a_duplicate_key_record_and_sends_nothing(ledger, monkeypatch, tmp_path):
    from core.telegram import telegram_bot as tb
    posts = []
    monkeypatch.setattr(tb, "DELIVERY_DIR", ledger.parent)
    monkeypatch.setattr(tb.requests, "post", lambda *a, **k: posts.append(1))
    out = tmp_path / "curator_output.txt"
    out.write_text("unused")
    monkeypatch.setenv("CURATOR_OUTPUT_PATH", str(out))
    ledger.write_text(_ack_text('"accepted": 0, "accepted": 3,'))
    assert tb.deliver_briefing("t", "c", today=TODAY) == 3 and posts == []


def _bounded(fn, seconds=5):
    import threading
    box = {}
    worker = threading.Thread(target=lambda: box.setdefault("value", fn()), daemon=True)
    worker.start()
    worker.join(seconds)
    assert not worker.is_alive(), "reader blocked on a non-regular file"
    return box["value"]


def test_a_fifo_ledger_is_refused_without_blocking(ledger):
    os.mkfifo(ledger)
    assert _bounded(lambda: dl.read_record(ledger, TODAY)) == ("invalid", None)
    assert _bounded(lambda: dl.cron_state(TODAY)) == "hold"


def test_a_fifo_curator_latest_is_refused_without_blocking(ledger, monkeypatch, tmp_path):
    os.mkfifo(tmp_path / "data" / "curator" / "curator_latest.json")
    out = tmp_path / "curator_output.txt"
    out.write_text("today")
    monkeypatch.setenv("CURATOR_OUTPUT_PATH", str(out))
    assert _bounded(lambda: dl.cron_state(TODAY)) == "none"   # not provably generated
