"""Waiting messages in a real browser (issue #346 P3): the real portal and page, the real routes and the scripted
streaming relay from browser_checks_b1, and an in-memory turn ledger with the store's queue rules (the real-Postgres
rules are test_mc_turn_queue_postgres.py). Covers: Send is free while an answer runs; each message keeps its own
files; one at a time, in order; Cancel; a pause and its Continue; reload; two tabs; desktop and phone widths."""
from __future__ import annotations

import threading
import time

import pytest
from playwright.sync_api import expect

from browser_checks_b1 import (browser, server, streaming_mc, fresh_queue, floor,  # noqa: F401
                               _context, go, _script, FINISH)
from minimoi_portal.guild_ui.mc import ledger_gate
from minimoi_portal.guild_ui.mc.turn_ledger import QUEUE_MAX, Admission, Claim, _clean_outcome


class BrowserLedger:
    """The store's queue rules in memory, thread-safe (the browser's requests arrive on server threads)."""

    def __init__(self):
        self.rows, self.barrier, self.lock = [], None, threading.Lock()

    def _waiting(self, principal):
        return [r for r in self.rows if r["state"] == "queued" and r["principal"] == principal]

    def _pos(self, row):
        return [r["id"] for r in self._waiting(row["principal"])].index(row["id"]) + 1

    def _same(self, kw):
        return next((r for r in self.rows if r["note_request_id"] == kw["note_request_id"]
                     or r["turn_request_id"] == kw["turn_request_id"]), None)

    def admit(self, **kw):
        with self.lock:
            if self._same(kw):
                row = self._same(kw)
                return Admission("queued" if row["state"] == "queued" else "done", entry=row,
                                 position=self._pos(row) if row["state"] == "queued" else None)
            if self.barrier:
                return Admission("paused", barrier=self.barrier)
            if any(r["state"] == "claimed" for r in self.rows):
                return Admission("busy", entry=next(r for r in self.rows if r["state"] == "claimed"))
            if self._waiting(kw["principal"]):
                return Admission("queue_pending")
            row = {"id": len(self.rows) + 1, "state": "claimed", "queued_at": None, "outcome": {}, **kw}
            self.rows.append(row)
            return Admission("claimed", entry=dict(row), claim=Claim(row["id"], "tok", 1))

    def enqueue(self, **kw):
        with self.lock:
            same = self._same(kw)
            if same:
                return Admission("queued", entry=same, position=self._pos(same), barrier=self.barrier)
            if len(self._waiting(kw["principal"])) >= QUEUE_MAX:
                return Admission("queue_full")
            row = {"id": len(self.rows) + 1, "state": "queued", "queued_at": "2026-10-19T22:00:00+00:00",
                   "claim_generation": 0, "outcome": {}, "kind": "typed", **kw}
            self.rows.append(row)
            return Admission("queued", entry=dict(row), position=self._pos(row), barrier=self.barrier)

    def claim_queued(self, principal, ledger_id):
        with self.lock:
            if self.barrier:
                return Admission("paused", barrier=self.barrier)
            if any(r["state"] == "claimed" for r in self.rows):
                return Admission("busy")
            row = next((r for r in self.rows if r["id"] == ledger_id and r["principal"] == principal), None)
            if row is None or row["state"] != "queued":
                return Admission("gone", entry=row)
            if self._waiting(principal)[0]["id"] != ledger_id:
                return Admission("not_next", entry=row, position=self._pos(row))
            row["state"] = "claimed"
            row["claim_generation"] += 1
            return Admission("claimed", entry=dict(row), claim=Claim(row["id"], "tok", row["claim_generation"]))

    def requeue(self, claim):
        with self.lock:
            row = self.rows[claim.ledger_id - 1]
            if row["state"] != "claimed" or not row.get("queued_at"):
                return False
            row["state"] = "queued"
            return True

    def cancel(self, principal, ledger_id):
        with self.lock:
            row = next((r for r in self.rows if r["id"] == ledger_id and r["principal"] == principal), None)
            if row is not None and row["state"] == "queued":
                row["state"] = "cancelled"
                return {"result": "cancelled", "ledger_id": ledger_id}
            return {"result": "not_queued", "ledger_id": ledger_id, "state": row["state"] if row else None}

    def held(self, claim, interval_s=30):
        class Hold:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return Hold()

    def finish(self, claim, state, outcome=None):
        clean = _clean_outcome(outcome)
        with self.lock:
            row = self.rows[claim.ledger_id - 1]
            if row["state"] != "claimed":
                return False
            row["state"], row["outcome"] = state, clean
            if state in ("failed", "unknown") and self.barrier is None:
                self.barrier = {"principal": row["principal"], "ledger_id": row["id"], "reason": state,
                                "created_at": "2026-10-19T22:00:00+00:00"}
            return True

    def status(self, principal, recent=10):
        with self.lock:
            mine = [dict(r) for r in self.rows if r["principal"] == principal]
            pause = dict(self.barrier) if self.barrier else None
        return {"running": next((r for r in mine if r["state"] == "claimed"), None), "pause": pause,
                "queued": [r for r in mine if r["state"] == "queued"],
                "recent": list(reversed(mine))[:recent], "released": []}

    def continue_after(self, principal, ledger_id):
        with self.lock:
            if not self.barrier or self.barrier["ledger_id"] != ledger_id:
                return {"result": "not_paused"}
            self.barrier = None
            return {"result": "continued", "ledger_id": ledger_id, "reason": "unknown", "message": "Continuing."}


@pytest.fixture
def queued(server, streaming_mc):
    ext = server["app"].extensions["guild_ui_next"]
    services = ext["services"]
    saved = (getattr(services, "turn_ledger", None), dict(ext.get("feature_environ") or {}))
    ledger = BrowserLedger()
    services.turn_ledger = ledger
    ext["feature_environ"] = {**saved[1], ledger_gate.FLAG: "1"}
    yield {"ledger": ledger, "runtime": streaming_mc["runtime"]}
    streaming_mc["runtime"].gate.set()
    services.turn_ledger = saved[0]
    ext["feature_environ"] = saved[1]


def _chat_posts(runtime):
    return [p for p in runtime.posts if p["url"].endswith("/chat/completions")]


def _say(page, text):
    page.fill("#mc-input", text)
    page.click("[data-mc-send]")


def _wait(cond, timeout=20, page=None):
    """Poll ``cond``. With ``page``, Playwright's events keep flowing meanwhile (route handlers run only then)."""
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        if page is not None:
            page.wait_for_timeout(100)
        else:
            time.sleep(0.1)
    return False


BUILD = "/guild-next/guild/build"


class _Held(list):
    """A relay script that holds before its first line until the test sets the runtime's gate (up to 60 s; the
    fixture's own WAIT gives up after 5 s). Once released, every later answer runs straight through."""

    def __init__(self, gate, items):
        super().__init__(items)
        self.gate = gate

    def __iter__(self):
        self.gate.wait(60)
        return super().__iter__()


def _held(runtime, reply="First answer."):
    runtime.script = _Held(runtime.gate, _script(reply, *FINISH))     # the first answer waits until the test lets it go


@pytest.mark.parametrize("width", [1440, 390])
def test_send_is_free_while_an_answer_runs_and_the_next_message_waits_with_its_own_files(browser, server, queued, width, tmp_path):
    runtime = queued["runtime"]
    _held(runtime)
    attachment = tmp_path / "only-first.txt"
    attachment.write_text("Synthetic source for the first message only.")
    ctx, page = _context(browser, server, width, 900)
    go(page, f"{server['url']}{BUILD}")
    expect(page.locator("[data-mc-queue-bar]")).to_be_hidden()
    page.set_input_files("[data-mc-file]", str(attachment))
    expect(page.locator('.att[data-state="ok"]')).to_have_count(1)
    _say(page, "First question, with the file")
    assert _wait(lambda: len(_chat_posts(runtime)) == 1)
    expect(page.locator("[data-mc-send]")).to_be_enabled()          # Send is free while the first answer runs
    _say(page, "Second question, no file")
    chip = page.locator("[data-queued-chip]")
    expect(chip).to_have_count(1)
    expect(chip).to_contain_text("Waiting")
    expect(chip).to_contain_text("not sent yet")
    expect(page.locator("[data-mc-queue-bar]")).to_contain_text("1 message waiting")
    expect(page.locator("[data-mc-queue-bar]")).to_contain_text("after the current answer")
    time.sleep(1.0)
    assert len(_chat_posts(runtime)) == 1                           # nothing is sent while the first runs
    if width < 641:
        box = page.locator("[data-queued-cancel]").bounding_box()
        assert box["height"] >= 44
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    runtime.gate.set()
    assert _wait(lambda: len(_chat_posts(runtime)) == 2)
    first, second = (p["body"]["messages"][0]["content"] for p in _chat_posts(runtime))
    assert "First question" in first and "Synthetic source for the first message only." in first
    assert "Second question" in second and "Synthetic source" not in second      # the second never borrowed the first's file
    expect(chip).to_have_count(0)
    expect(page.locator("[data-mc-queue-bar]")).to_be_hidden()
    states = [r["state"] for r in queued["ledger"].rows]
    assert states == ["answered", "answered"]
    ctx.close()


def test_cancel_a_waiting_message(browser, server, queued):
    runtime = queued["runtime"]
    _held(runtime)
    ctx, page = _context(browser, server, 1280, 900)
    go(page, f"{server['url']}{BUILD}")
    _say(page, "First question")
    assert _wait(lambda: len(_chat_posts(runtime)) == 1)
    _say(page, "Second question, then cancelled")
    page.locator("[data-queued-cancel]").click()
    expect(page.locator("[data-not-sent]")).to_have_text("Not sent · cancelled")
    expect(page.locator("[data-queued-chip]")).to_have_count(0)
    runtime.gate.set()
    assert _wait(lambda: queued["ledger"].rows[0]["state"] == "answered")
    time.sleep(1.5)
    assert len(_chat_posts(runtime)) == 1
    ctx.close()


def test_a_pause_holds_waiting_messages_until_continue(browser, server, queued):
    runtime = queued["runtime"]
    runtime.script = _script("Answered after Continue.", *FINISH)
    queued["ledger"].barrier = {"principal": "robert", "ledger_id": 99, "reason": "unknown", "created_at": "x"}
    ctx, page = _context(browser, server, 390, 900)
    go(page, f"{server['url']}{BUILD}")
    bar = page.locator("[data-mc-queue-bar]")
    expect(bar).to_contain_text("outcome is unknown")
    _say(page, "Held behind the pause")
    expect(bar).to_contain_text("1 message waits until you continue")
    time.sleep(1.0)
    assert _chat_posts(runtime) == []
    cont = page.locator("[data-mc-queue-continue]")
    assert cont.bounding_box()["height"] >= 44
    cont.click()
    assert _wait(lambda: len(_chat_posts(runtime)) == 1)
    expect(bar).to_be_hidden()
    ctx.close()


def test_reload_restores_waiting_messages_and_sends_them_afterwards(browser, server, queued):
    runtime = queued["runtime"]
    _held(runtime)
    ctx, page = _context(browser, server, 1280, 900)
    go(page, f"{server['url']}{BUILD}")
    _say(page, "First question")
    assert _wait(lambda: len(_chat_posts(runtime)) == 1)
    _say(page, "Second question survives reload")
    expect(page.locator("[data-queued-chip]")).to_have_count(1)
    go(page, page.url)
    expect(page.locator("[data-queued-chip]")).to_have_count(1)
    expect(page.locator("[data-mc-queue-bar]")).to_contain_text("1 message waiting")
    runtime.gate.set()
    assert _wait(lambda: len(_chat_posts(runtime)) == 2, timeout=30)    # the reloaded page looks again and sends it
    assert "Second question survives reload" in _chat_posts(runtime)[1]["body"]["messages"][0]["content"]
    ctx.close()


def test_two_tabs_send_each_waiting_message_once(browser, server, queued):
    runtime = queued["runtime"]
    _held(runtime)
    ctx, one = _context(browser, server, 1280, 900)
    go(one, f"{server['url']}{BUILD}")
    two = ctx.new_page()
    go(two, one.url)
    _say(one, "First question")
    assert _wait(lambda: len(_chat_posts(runtime)) == 1)
    _say(one, "Second question")
    expect(one.locator("[data-queued-chip]")).to_have_count(1)         # recorded as waiting before the second tab looks
    go(two, two.url)                                                   # the second tab sees it waiting
    expect(two.locator("[data-queued-chip]")).to_have_count(1)
    runtime.gate.set()
    assert _wait(lambda: len(_chat_posts(runtime)) == 2, timeout=30)
    time.sleep(6)                                                      # both tabs have looked again by now
    assert len(_chat_posts(runtime)) == 2
    assert [r["state"] for r in queued["ledger"].rows] == ["answered", "answered"]
    ctx.close()


def test_a_lost_queued_answer_is_asked_again_with_the_same_ids_and_never_called_not_sent(browser, server, queued):
    import json as _json
    runtime = queued["runtime"]
    runtime.script = _script("Answered once.", *FINISH)
    ctx, page = _context(browser, server, 1280, 900)
    go(page, f"{server['url']}{BUILD}")
    dropped, bodies = [], []

    def lose_first(route):
        body = _json.loads(route.request.post_data or "{}")
        if body.get("queue") is True:
            bodies.append(body)
            if not dropped:
                route.fetch()                       # the server records it ...
                dropped.append(True)
                route.abort()                       # ... and the answer never arrives
                return
        route.continue_()
    page.route("**/api/v1/mc/turns", lose_first)
    _say(page, "Answer lost on the way back")
    assert _wait(lambda: len(_chat_posts(runtime)) == 1, page=page)
    assert len(bodies) == 2 and bodies[0] == bodies[1]                 # asked again with exactly the same ids
    assert len(queued["ledger"].rows) == 1                              # recorded once
    expect(page.locator("[data-turn-failure]")).to_have_count(0)        # never "nothing was sent"
    page.wait_for_timeout(1500)
    assert len(_chat_posts(runtime)) == 1
    ctx.close()


def test_choices_made_while_a_note_saves_stay_for_the_next_message(browser, server, queued, tmp_path):
    import json as _json
    runtime = queued["runtime"]
    runtime.script = _script("Answered.", *FINISH)
    first, later = tmp_path / "first.txt", tmp_path / "later.txt"
    first.write_text("File chosen at Send.")
    later.write_text("File chosen while the note saved.")
    ctx, page = _context(browser, server, 1280, 900)
    go(page, f"{server['url']}{BUILD}")
    page.set_input_files("[data-mc-file]", str(first))
    expect(page.locator('.att[data-state="ok"]')).to_have_count(1)
    held, sent = [], []
    page.route("**/api/v1/notes", lambda route: held.append(route) if route.request.method == "POST" else route.continue_())
    page.on("request", lambda r: sent.append(_json.loads(r.post_data or "{}"))
            if r.method == "POST" and r.url.endswith("/api/v1/mc/turns") else None)
    _say(page, "Carries only what was chosen at Send")
    assert _wait(lambda: bool(held), page=page)
    # While the note is saving: a topic item and another file are chosen, for the NEXT message.
    page.evaluate("() => { window.guildTopicContext = [{topic_id: 't-aaaaaaaaaaaa', item_id: 'i-aaaaaaaaaaaa'}]; }")
    page.set_input_files("[data-mc-file]", str(later))
    expect(page.locator('.att[data-state="ok"]')).to_have_count(1)
    held[0].continue_()                          # the note save finishes only now
    page.unroute("**/api/v1/notes")
    assert _wait(lambda: bool(sent), page=page)
    queued_body = sent[0]
    assert queued_body["queue"] is True and "topic_context" not in queued_body
    assert len(queued_body.get("attachment_ids", [])) == 1
    assert _wait(lambda: len(_chat_posts(runtime)) == 1, page=page)
    content = _chat_posts(runtime)[0]["body"]["messages"][0]["content"]
    assert "File chosen at Send." in content and "File chosen while the note saved." not in content
    assert page.evaluate("() => window.guildTopicContext.length") == 1                     # the later choice is still there
    expect(page.locator('.att[data-state="ok"]')).to_have_count(1)                         # and so is the later file
    ctx.close()


@pytest.mark.parametrize("state", ["private", "unknown"])
def test_off_the_record_or_unknown_sends_nothing_and_does_not_spin(browser, server, queued, state):
    runtime = queued["runtime"]
    runtime.script = _script("Answered after the record came back.", *FINISH)
    ledger = queued["ledger"]
    ledger.rows.append({"id": 1, "state": "claimed", "queued_at": None, "outcome": {}, "principal": "robert",
                        "conversation_id": "c-elsewhere", "note_request_id": "n-running-0001", "turn_request_id": "t-running-0001",
                        "kind": "typed", "claim_generation": 1})
    ledger.rows.append({"id": 2, "state": "queued", "queued_at": "2026-10-19T22:00:00+00:00", "outcome": {}, "principal": "robert",
                        "conversation_id": "c-elsewhere", "note_request_id": "n-waiting-0001", "turn_request_id": "t-waiting-0001",
                        "kind": "typed", "claim_generation": 0})
    ctx, page = _context(browser, server, 1280, 900)
    reads, writes = [], []
    page.on("request", lambda r: (reads if r.method == "GET" else writes).append(r.url) if "/mc/turns" in r.url else None)
    go(page, f"{server['url']}{BUILD}")
    expect(page.locator("[data-mc-queue-bar]")).to_contain_text("1 message waiting")
    if state == "private":
        page.evaluate("() => document.querySelector('[data-mc-record]').click()")              # go Private
        expect(page.locator("body")).to_have_attribute("data-off-record", "true")
    else:
        page.evaluate("async () => { const s = await import(document.querySelector('link[href*=\"/js/state.js\"]').href);"
                      " s.live.known = false; s.changed(); }")
        expect(page.locator("body")).to_have_attribute("data-record-known", "false")
    ledger.rows[0]["state"] = "answered"                                 # the earlier answer ends: the head could go now
    reads.clear()
    page.wait_for_timeout(12000)
    assert [u for u in writes if "/dispatch" in u] == []                  # nothing was sent from this tab
    assert len(reads) <= 3                                                # and it did not spin
    if state == "private":
        page.evaluate("() => document.querySelector('[data-mc-record]').click()")              # back on the record
    else:
        page.evaluate("async () => { const s = await import(document.querySelector('link[href*=\"/js/state.js\"]').href);"
                      " s.live.known = true; s.changed(); }")
    assert _wait(lambda: any("/dispatch" in u for u in writes), page=page)   # it carries on
    ctx.close()


def test_with_the_ledger_off_nothing_waits_and_send_is_unchanged(browser, server, streaming_mc):
    streaming_mc["runtime"].script = _script("Plain answer.", *FINISH)
    ctx, page = _context(browser, server, 1280, 900)
    go(page, f"{server['url']}{BUILD}")
    _say(page, "Plain question")
    assert _wait(lambda: len(_chat_posts(streaming_mc["runtime"])) == 1)
    expect(page.locator("[data-mc-queue-bar]")).to_be_hidden()
    expect(page.locator("[data-queued-chip]")).to_have_count(0)
    ctx.close()
