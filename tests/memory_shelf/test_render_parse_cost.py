"""``render.parse_blocks`` must read a record in time proportional to its size. It once searched the rest of the body for a
note/ref block after every turn (quadratic: 12 s for a 7 MB, 10,000-turn record, which made Ask re-reading one record
for a hit take seconds). The result must not change: an oracle with the old loop is compared on mixed bodies."""
import hashlib

import pytest

from core.memory_shelf import record, render
from core.memory_shelf.sessions import Parsed


def oracle(body: str):
    """The previous implementation, verbatim in behaviour: search for a block again after every frame."""
    data, pos = body.encode("utf-8"), 0
    turns, blocks = [], []
    while True:
        match, block = render._FRAME.search(data, pos), render._BLOCK.search(data, pos)
        if match and (not block or match.start() < block.start()):
            start, size = match.end(), int(match.group(4))
            chunk = data[start:start + size]
            if len(chunk) != size or hashlib.sha256(chunk).hexdigest() != match.group(5).decode():
                raise record.InvalidRecord("a turn does not match its frame")
            turns.append(render.Turn(int(match.group(1)), match.group(2).decode(), chunk.decode("utf-8"), int(match.group(3)),
                                     "shelf", render._attrs_of(match.group(6))))
            pos = start + size
        elif block:
            start, size = block.end(), int(block.group(3))
            chunk = data[start:start + size]
            if len(chunk) != size or hashlib.sha256(chunk).hexdigest() != block.group(4).decode():
                raise record.InvalidRecord("a block does not match its frame")
            blocks.append((block.group(1).decode(), int(block.group(2)), chunk.decode("utf-8"), render._attrs_of(block.group(5))))
            pos = start + size
        else:
            return turns, blocks


def body(n_turns, notes=0, refs=0, text=lambda i: f"turn text {i}"):
    parsed = Parsed("grok", "Grok", source_id="x", identity="source")
    for i in range(1, n_turns + 1):
        parsed.add("human" if i % 2 else "assistant", text(i), i, "test")
    parsed.notes = [{"note_id": f"n{i}", "author": "robert", "kind": "decision", "version": 1, "text": f"note {i}"} for i in range(notes)]
    parsed.references = [{"reference_id": f"r{i}", "kind": "file", "source_record_id": f"rec{i}"} for i in range(refs)]
    return render.render_body(parsed)


class Counting:
    """A stand-in for the compiled block pattern that counts searches."""
    def __init__(self, real):
        self.real, self.calls = real, 0

    def search(self, data, pos=0):
        self.calls += 1
        return self.real.search(data, pos)


@pytest.mark.parametrize("make", [
    lambda: body(0),
    lambda: body(5),
    lambda: body(40, notes=3, refs=2),
    lambda: body(6, notes=1),
    lambda: body(30, text=lambda i: f"<!-- note 9 | bytes 3 | sha256 {'0' * 64} -->\nabc inside turn {i}"),   # block-like text in a turn
    lambda: body(12, text=lambda i: "x\n" * i),
])
def test_the_parse_is_unchanged(make):
    text = make()
    got, want = render.parse_blocks(text), oracle(text)
    assert got == want


def test_a_body_with_no_blocks_searches_for_blocks_once_not_once_per_turn(monkeypatch):
    spy = Counting(render._BLOCK)
    monkeypatch.setattr(render, "_BLOCK", spy)
    turns, blocks = render.parse_blocks(body(3000))
    assert len(turns) == 3000 and blocks == []
    assert spy.calls <= 2


def test_blocks_are_searched_again_only_after_the_previous_one_is_passed(monkeypatch):
    spy = Counting(render._BLOCK)
    monkeypatch.setattr(render, "_BLOCK", spy)
    turns, blocks = render.parse_blocks(body(2000, notes=2, refs=1))
    assert len(turns) == 2000 and len(blocks) == 3
    assert spy.calls <= 6                                         # a handful, never one per turn


def test_a_lying_frame_still_raises():
    text = body(4)
    with pytest.raises(record.InvalidRecord):
        render.parse_blocks(text.replace("turn text 2", "turn text X", 1))
