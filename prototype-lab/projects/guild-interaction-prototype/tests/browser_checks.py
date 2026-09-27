"""Browser checks (pytest + Playwright, installed Chrome, headless). Run explicitly:

    .venv/bin/python -m pytest prototype-lab/projects/guild-interaction-prototype/tests/browser_checks.py

Each test gets a fresh browser context (empty localStorage) against a loopback
server in sample mode, so results are deterministic.
"""
from __future__ import annotations

import hashlib
import json
import re
import socket
import threading

import pytest
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from app import create_app

ACTIVE_OVERLAY_KEY = "guild.overlay.v1"


def _serve(**kw):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    app = create_app(port=port, **kw)
    srv = make_server("127.0.0.1", port, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{port}"


@pytest.fixture(scope="module")
def base():
    srv, url = _serve(sources="sample")
    yield url
    srv.shutdown()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        yield b
        b.close()


@pytest.fixture
def ctx(browser):
    c = browser.new_context(viewport={"width": 1280, "height": 800})
    yield c
    c.close()


def go(page, url):
    page.goto(url)
    page.wait_for_selector("body[data-ready=true]")


def storage(page):
    return page.evaluate("JSON.stringify(Object.fromEntries(Object.entries(localStorage)))")


def overlay(page):
    return json.loads(page.evaluate(f"localStorage.getItem('{ACTIVE_OVERLAY_KEY}') || 'null'") or "null")


def say(page, text):
    page.fill("[data-mc-input]", text)
    page.press("[data-mc-input]", "Enter")


def open_mc(page):
    if page.get_attribute("[data-mc-panel]", "data-mode") == "pill":
        page.click("[data-mc-pill]")
    expect(page.locator("[data-mc-panel]")).not_to_have_attribute("data-mode", "pill")


def panel_order(page):
    return page.eval_on_selector_all("[data-bench] > [data-panel]", "els => els.map(e => e.dataset.panel)")


# 1 ─ arrival
def test_arrival_doors_signals_and_strip_dots(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild")
    doors = page.locator("[data-door]")
    expect(doors).to_have_count(4)
    for door_id in ["build", "operate", "improve", "experiment"]:
        door = page.locator(f'[data-door="{door_id}"]')
        expect(door.locator(".door-observed")).to_contain_text("observed")
        state = door.locator(".sig-dot").get_attribute("data-state")
        strip = page.locator(f'[data-section="{door_id}"] .sig-dot')
        assert strip.get_attribute("data-state") == state
        assert strip.get_attribute("data-signal") == door.locator(".sig-dot").get_attribute("data-signal")
        assert strip.inner_text().strip() != ""                      # text, not colour alone
    expect(page.locator('[data-door="build"]')).to_contain_text("2 need you")
    expect(page.locator("[data-continue]")).to_have_attribute("href", "/guild/build")
    page.click('[data-door="build"] a')                                   # Build → Shop floor (rev 3)
    page.wait_for_selector("body[data-ready=true]")
    assert page.url.endswith("/guild/build")
    expect(page.locator("[data-zone=status]")).to_be_visible()
    page.click("[data-open-bench]")                                      # detail one step away
    page.wait_for_selector("body[data-ready=true]")
    expect(page.locator('[data-panel="subject"]')).to_have_attribute("data-focused", "true")
    expect(page.locator('[data-panel="subject"] [data-focus-label]')).to_be_visible()
    go(page, base + "/guild/operate")
    go(page, base + "/guild")
    expect(page.locator("[data-continue]")).to_have_attribute("href", "/guild/operate")


# 2 ─ bench persistence and reset
def test_bench_arrangement_persists_across_navigation_and_reload(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/build/bench")
    page.click('[data-panel="since"] [data-act="up"]')                   # since above discussions
    page.click('[data-panel="needs"] [data-act="fold"]')
    page.click('[data-panel="motion"] [data-act="focus"]')
    page.drag_and_drop('[data-panel="postits"] [data-drag-handle]', '[data-panel="needs"]')
    expected = panel_order(page)
    assert expected[0] == "motion"
    assert expected.index("since") < expected.index("discussions")
    assert expected.index("postits") < expected.index("needs"), "drag reorder"
    for nav in ("operate", "reload"):
        if nav == "operate":
            go(page, base + "/guild/operate")
            go(page, base + "/guild/build/bench")
        else:
            page.reload(); page.wait_for_selector("body[data-ready=true]")
        assert panel_order(page) == expected
        expect(page.locator('[data-panel="needs"]')).to_have_attribute("data-folded", "true")
        expect(page.locator('[data-panel="needs"] [data-act="fold"]')).to_have_attribute("aria-expanded", "false")
        expect(page.locator('[data-panel="motion"] [data-act="focus"]')).to_have_attribute("aria-pressed", "true")


def test_bench_reset_to_mc_default(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/build/bench")
    default = panel_order(page)
    page.click('[data-panel="postits"] [data-act="fold"]')
    page.click('[data-panel="discussions"] [data-act="focus"]')
    page.click('[data-panel="needs"] [data-act="down"]')
    assert panel_order(page) != default
    page.click("[data-bench-reset]")
    assert panel_order(page) == default
    expect(page.locator('[data-panel="postits"]')).to_have_attribute("data-folded", "true")
    expect(page.locator('[data-panel="subject"]')).to_have_attribute("data-focused", "true")
    page.reload(); page.wait_for_selector("body[data-ready=true]")
    assert panel_order(page) == default


def test_empty_panel_autofolds_with_reason(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/build/bench")
    page.click('[data-panel="blocked"] [data-act="fold"]')               # unfold (folded by default)
    expect(page.locator('[data-panel="blocked"]')).to_have_attribute("data-empty", "false")
    page.click("[data-return-monday]")                                   # grant issued → nothing blocked
    blocked = page.locator('[data-panel="blocked"]')
    expect(blocked).to_have_attribute("data-empty", "true")
    expect(blocked.locator("[data-empty-reason]")).to_be_visible()
    expect(blocked.locator("[data-empty-reason]")).to_contain_text("nothing to show — grant issued r-4491")
    expect(blocked.locator("[data-panel-body]")).to_be_hidden()
    expect(blocked.locator("[data-panel-state]")).to_have_text("nothing to show")


# 3 ─ Operate
def test_operate_drilldown_counts_and_evidence_marks(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/operate")
    expect(page.locator('[data-tile="capabilities"]')).to_have_attribute("aria-pressed", "true")
    drill = page.locator('[data-drill-for="capabilities"]')
    counts = drill.locator(".count:visible")
    expect(counts).to_have_count(4)
    assert [c.split("\n")[:2] for c in counts.all_inner_texts()] == [["INTENDED", "3"], ["ENABLED", "2"], ["REACHED", "2"], ["ACTIVE", "1"]]
    expect(drill).to_contain_text("Adoption 1 of 2 enabled · access gap 1")
    marks = drill.locator("[data-evidence] li:visible .mark")
    assert sorted(set(m.lower() for m in marks.all_inner_texts())) == ["blocker", "reported", "unknown", "verified"]
    expect(drill).to_contain_text("each is a proposal you confirm".capitalize()[0:0] + "Each is a proposal you confirm")
    expect(page.locator("[data-mc-context]")).to_contain_text("Operate · Capabilities")
    page.click('[data-tile="disk"]')
    expect(drill).to_be_hidden()
    expect(page.locator("[data-drill-none]")).to_contain_text("No drill-down in this prototype for Disk")


def test_health_area_has_no_healthy_claim(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/operate")
    health = page.locator("[data-health]")
    text = health.inner_text()
    assert "sample" in text.lower() and "not instrumented" in text.lower()
    assert not re.search(r"\bhealthy\b|\bgreen\b|\bOK\b|\ball good\b", text, re.I)
    tags = health.locator(".htag").all_inner_texts()
    assert tags and all(t.lower() in ("sample", "not instrumented") for t in tags)
    greenish = page.evaluate("""() => [...document.querySelectorAll('[data-health] *')].filter(el => {
        const cs = getComputedStyle(el);
        return [cs.color, cs.backgroundColor, cs.borderTopColor].some(c => {
          const m = c.match(/\\d+/g); if (!m) return false;
          const [r, g, b] = m.map(Number); return g > r + 30 && g > b + 30; });
      }).length""")
    assert greenish == 0


def test_stale_tile_treat_as_unknown(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/operate")
    for tile_id in ("tickets", "systems"):
        tile = page.locator(f'[data-tile="{tile_id}"]')
        expect(tile).to_have_attribute("data-unknown", "true")
        expect(tile).to_contain_text("treat as unknown")
        assert tile.evaluate("e => getComputedStyle(e).borderTopStyle") == "dashed"
        value_color = tile.locator(".tile-value").evaluate("e => getComputedStyle(e).color")
        normal_color = page.locator('[data-tile="disk"] .tile-value').evaluate("e => getComputedStyle(e).color")
        assert value_color != normal_color                                  # grey number
    expect(page.locator('[data-tile="systems"]')).to_contain_text("not instrumented")
    expect(page.locator('[data-tile="tickets"]')).to_contain_text("stale")


# 4 ─ conversation continuity
def test_conversation_continuity_operate_to_build(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/operate")
    open_mc(page)
    say(page, "Why did usage stall here?")
    expect(page.locator(".msg[data-who=mc]").first).to_contain_text("Simulated reply")
    expect(page.locator("[data-mc-context]")).to_contain_text("Context: Operate · Capabilities → “file this” · recording on")
    go(page, base + "/guild/build/bench")                                # the Build strip link now opens the Shop floor
    expect(page.locator("[data-mc-panel]")).to_have_attribute("data-mode", "floating")
    thread = page.locator("[data-mc-thread]")
    expect(thread).to_contain_text("Why did usage stall here?")
    expect(thread).to_contain_text("from Operate · Capabilities")
    expect(thread).to_contain_text("Intended for three")
    expect(page.locator("[data-mc-context]")).to_contain_text("Context: Build · “file this” rollout · recording on")
    expect(page.locator("[data-mc-participants]")).to_contain_text("Robert · owner")


def test_minimize_dock_keep_participants_and_unread(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/build/bench")
    open_mc(page)
    say(page, "Bring Claude and Codex in.")
    page.click("[data-mc-min]")                                          # Claude joins while minimized
    expect(page.locator("[data-mc-unread]")).to_contain_text("unread", timeout=4000)
    go(page, base + "/guild/operate")
    expect(page.locator("[data-mc-unread]")).to_contain_text("unread")
    page.click("[data-mc-pill]")
    expect(page.locator("[data-mc-unread]")).to_be_hidden()
    page.click("[data-mc-dock]")
    expect(page.locator("body")).to_have_attribute("data-mc-mode", "docked")
    page.reload(); page.wait_for_selector("body[data-ready=true]")
    expect(page.locator("[data-mc-panel]")).to_have_attribute("data-mode", "docked")
    expect(page.locator('[data-participant="claude"]')).to_contain_text("joined")
    expect(page.locator('[data-participant="codex"]')).to_contain_text("invited")
    page.click("[data-mc-preset=left]")
    expect(page.locator("[data-mc-panel]")).to_have_attribute("data-pos", "left")


# 5 ─ queue round trip
def test_queue_round_trip_restores_arrangement_focus_conversation(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/build/bench")
    page.click('[data-panel="postits"] [data-act="fold"]')
    page.click('[data-panel="needs"] [data-act="focus"]')
    open_mc(page)
    say(page, "What's in build?")
    before = panel_order(page)
    # Needs-you row → Build Queue (highlighted) → item → Back → Back
    page.click('[data-panel="needs"] a[data-queue-link] >> nth=0')
    page.wait_for_selector("body[data-ready=true]")
    assert page.url.endswith("/guild/build/queue?focus=158")
    expect(page.locator("#card-158")).to_have_class(re.compile("is-highlight"))
    page.click("#card-158 .q-open a")
    page.wait_for_selector("body[data-ready=true]")
    assert page.url.endswith("/guild/build/items/158")
    expect(page.locator("[data-mc-context]")).to_contain_text("Build Queue · #158")
    page.click("[data-back]"); page.wait_for_selector("body[data-ready=true]")
    assert "/guild/build/queue" in page.url
    page.click("[data-back-bench]"); page.wait_for_selector("body[data-ready=true]")
    assert page.url.endswith("/guild/build/bench")
    assert panel_order(page) == before
    expect(page.locator('[data-panel="needs"]')).to_have_attribute("data-focused", "true")
    expect(page.locator('[data-panel="postits"]')).to_have_attribute("data-folded", "false")
    expect(page.locator("[data-mc-panel]")).to_have_attribute("data-mode", "floating")
    expect(page.locator("[data-mc-thread]")).to_contain_text("What's in build?")
    # Focus panel link → item → browser Back
    page.click('[data-panel="needs"] [data-act="focus"]')                # unfocus
    page.click('[data-panel="subject"] a[data-queue-link]')
    page.wait_for_selector("body[data-ready=true]")
    page.click("[data-back]"); page.wait_for_selector("body[data-ready=true]")
    assert page.url.endswith("/guild/build/bench")
    expect(page.locator("[data-mc-thread]")).to_contain_text("What's in build?")


# 6 ─ attach vs file this
def test_attach_for_discussion_creates_no_receipt(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/build/bench")
    open_mc(page)
    page.click("[data-mc-sample-attach='0']")
    att = page.locator(".msg-attach").last
    expect(att).to_contain_text("admin-s021-note.txt")
    expect(att).to_contain_text("shared for discussion · not filed")
    page.wait_for_timeout(300)
    assert overlay(page)["receipts"] == []
    expect(page.locator(".msg-receipt")).to_have_count(0)
    expect(page.locator(".msg-proposal")).to_have_count(0)
    expect(page.locator('[data-receipts-for="158"]')).to_contain_text("No receipts yet.")
    go(page, base + "/guild/build/items/158")
    expect(page.locator('[data-receipts-for="158"]')).to_contain_text("No receipts yet.")


def test_file_this_confirm_receipt_in_thread_and_item(ctx, base, tmp_path):
    page = ctx.new_page()
    go(page, base + "/guild/build/bench")
    open_mc(page)
    f = tmp_path / "field-notes.txt"
    f.write_bytes(b"notes from the s-021 session\n")
    page.set_input_files("[data-mc-file]", str(f))
    expect(page.locator(".msg-attach").last).to_contain_text("field-notes.txt")
    say(page, "file this")
    card = page.locator(".msg-proposal").last
    expect(card).to_contain_text("File this attachment")
    expect(card).to_contain_text("s-023")
    expect(card).to_contain_text("#158")
    expect(card).to_contain_text("sha256:" + hashlib.sha256(f.read_bytes()).hexdigest()[:12])
    expect(card).to_contain_text("stays in this browser; not uploaded")
    card.locator("[data-proposal-act=confirm]").click()
    label = "simulated filing · local receipt r-4486 · file not uploaded"
    expect(page.locator(".msg-receipt").last).to_contain_text(label)
    expect(page.locator(".msg-attach").last).to_contain_text(label)
    expect(page.locator('[data-receipts-for="158"]')).to_contain_text(label)
    assert "filed ·" not in page.locator("[data-mc-thread]").inner_text().replace("simulated filing ·", "")
    go(page, base + "/guild/build/items/158")
    expect(page.locator('[data-receipts-for="158"]')).to_contain_text(label)
    # reloaded state: metadata only, bytes gone
    expect(page.locator(".msg-attach").last).to_contain_text(label)
    expect(page.locator(".msg-attach").last).to_contain_text("file content not kept")
    stored = storage(page)
    assert "notes from the s-021 session" not in stored
    conv = json.loads(page.evaluate("localStorage.getItem('guild.conversation.v1')"))
    assert set(conv["attachments"][0]) <= {"id", "name", "size", "sha256", "origin", "state", "receipt", "at", "filed_as", "session"}


def test_file_this_cancel_and_edit(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/build/bench")
    open_mc(page)
    page.click("[data-mc-sample-attach='1']")
    say(page, "file this")
    card = page.locator(".msg-proposal").last
    card.locator("[data-proposal-act=edit]").click()
    page.locator(".msg-proposal").last.locator("[data-slot=editform] input[name=name]").fill("u02-evidence.txt")
    page.locator(".msg-proposal").last.locator("[data-slot=editform] select[name=session]").select_option("s-021")
    page.locator(".msg-proposal").last.locator("[data-slot=editform] button[type=submit]").click()
    card = page.locator(".msg-proposal").last
    expect(card).to_contain_text("u02-evidence.txt")
    expect(card).to_contain_text("s-021")
    expect(card).to_contain_text("Revised · awaiting your confirmation")
    card.locator("[data-proposal-act=cancel]").click()
    expect(page.locator(".msg-proposal").last).to_contain_text("Cancelled · no receipt")
    expect(page.locator(".msg-attach").last).to_contain_text("shared for discussion · not filed")
    assert overlay(page)["receipts"] == []
    say(page, "confirm")                                                 # nothing pending any more
    expect(page.locator(".msg[data-who=mc]").last).to_contain_text("Nothing is waiting for confirmation.")
    assert overlay(page)["receipts"] == []


# F2 ─ off the record
def test_off_record_disables_attach_invite_actions_and_persists_nothing(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/build/bench")
    open_mc(page)
    page.click("[data-mc-record]")
    expect(page.locator("[data-mc-context]")).to_contain_text("off the record since")
    for sel in ("[data-mc-attach]", "[data-mc-invite]", "[data-mc-sample-attach='0']", "[data-return-monday]"):
        expect(page.locator(sel)).to_be_disabled()
    expect(page.locator("[data-mc-refusal]")).to_contain_text("Off the record — go back on the record")
    say(page, "zebra-secret thinking about pricing")
    say(page, "Bring Claude and Codex in.")
    say(page, "file this")
    say(page, "Agreed. Grant Guest now.")
    page.evaluate("document.querySelector('[data-mc-attach]').disabled = false")   # force the control
    page.click("[data-mc-attach]", force=True)
    page.evaluate("document.querySelector('[data-mc-sample-attach=\"0\"]').disabled = false")
    page.click("[data-mc-sample-attach='0']", force=True)
    page.wait_for_timeout(1800)                                          # longer than a join delay
    stored = storage(page)
    conv = json.loads(page.evaluate("localStorage.getItem('guild.conversation.v1') || 'null'") or "null")
    assert "zebra-secret" not in stored and "Grant Guest now" not in stored
    if conv:
        assert conv["attachments"] == [] and conv["proposals"] == []
        assert conv["participants"]["claude"]["state"] == "none" and conv["participants"]["codex"]["state"] == "none"
    ov = overlay(page)
    assert ov is None or (ov["receipts"] == [] and ov["decision"] is None)
    page.reload(); page.wait_for_selector("body[data-ready=true]")
    assert "zebra-secret" not in page.locator("[data-mc-thread]").inner_text()
    assert page.locator("[data-participant=claude]").count() == 0
    expect(page.locator("[data-mc-context]")).to_contain_text("recording on")


def test_off_the_record_segment_not_persisted(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/operate")
    open_mc(page)
    say(page, "Why did usage stall here?")
    say(page, "off the record")
    expect(page.locator("[data-off-marker]")).to_contain_text("Off the record from")
    say(page, "just thinking aloud")
    off_msg = page.locator(".msg[data-offrecord=true]").last
    expect(off_msg).to_contain_text("just thinking aloud")
    assert off_msg.evaluate("e => getComputedStyle(e).borderTopStyle") == "dashed"
    say(page, "back on the record")
    expect(page.locator("[data-mc-thread]")).to_contain_text("Back on the record")
    page.reload(); page.wait_for_selector("body[data-ready=true]")
    thread = page.locator("[data-mc-thread]").inner_text()
    assert "just thinking aloud" not in thread and "Why did usage stall here?" in thread


# 7 ─ invite, decision, Monday
def test_invite_claude_joins_codex_stays_invited(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/build/bench")
    open_mc(page)
    say(page, "Bring Claude and Codex in.")
    expect(page.locator(".msg[data-who=mc]").last).to_contain_text("I'll show each here when they join, not before")
    expect(page.locator("[data-participant=claude]")).to_contain_text("invited")
    expect(page.locator("[data-participant=claude]")).to_have_attribute("data-state", "joined", timeout=4000)
    expect(page.locator("[data-participant=claude]")).to_contain_text("Claude · joined")
    expect(page.locator(".msg[data-who=claude]")).to_contain_text("Simulated reply")
    page.wait_for_timeout(2500)
    codex = page.locator("[data-participant=codex]")
    expect(codex).to_have_attribute("data-state", "invited")
    expect(codex).to_contain_text("Codex · invited")
    expect(codex).to_contain_text("not joined")
    assert "Codex joined" not in page.locator("[data-mc-thread]").inner_text()
    assert codex.evaluate("e => getComputedStyle(e).borderTopStyle") == "dashed"
    assert page.locator("[data-participant=claude]").evaluate("e => getComputedStyle(e).borderTopStyle") == "solid"
    page.reload(); page.wait_for_selector("body[data-ready=true]")
    expect(page.locator("[data-participant=codex]")).to_have_attribute("data-state", "invited")


def test_owner_decision_receipt_and_monday_return(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/build/bench")
    open_mc(page)
    say(page, "Bring Claude and Codex in.")
    expect(page.locator("[data-participant=claude]")).to_have_attribute("data-state", "joined", timeout=4000)
    say(page, "Agreed. Grant Guest now, mobile fix as an Idea, coach Admin after.")
    card = page.locator(".msg-proposal").last
    expect(card).to_contain_text("Record as owner decision")
    expect(card).to_contain_text("grant Guest → mobile fix (Idea) → coach Admin")
    card.locator("[data-proposal-act=confirm]").click()
    expect(page.locator(".msg-receipt").last).to_contain_text("r-4490")
    dc = page.locator("[data-decision-card]")
    expect(dc).to_be_visible()
    expect(dc).to_contain_text("r-4490")
    expect(page.locator('[data-panel="needs"]')).to_contain_text("Idea q-91")
    page.click("[data-return-monday]")
    expect(page.locator("[data-clock-label]").first).to_contain_text("Mon 8:00")
    expect(dc).to_contain_text("grant Guest → mobile fix (Idea) → coach Admin")
    expect(dc).to_contain_text("s-023")
    expect(dc).to_contain_text("Robert (grant, today), MC (follow-up Mon)")
    expect(dc).to_contain_text("Grant issued Sat 9:27 · r-4491")
    expect(dc.locator('[data-step="open"]')).to_contain_text("Idea q-91 “attach on mobile” · owner Claude Code")
    expect(dc).to_contain_text("MC follow-up Mon 8:00")
    expect(dc).to_contain_text("Codex never joined")
    expect(page.locator("[data-mc-thread]")).to_contain_text("Morning. Grant done")
    go(page, base + "/guild/operate")
    expect(page.locator('[data-drill-for="capabilities"]')).to_contain_text("Adoption 1 of 3 enabled · access gap 0")


def test_what_did_we_decide_resolves(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/build/bench")
    open_mc(page)
    say(page, "What did we decide about file this?")
    expect(page.locator(".msg[data-who=mc]").last).to_contain_text("No owner decision recorded")
    say(page, "Agreed. Grant Guest now.")
    page.locator(".msg-proposal").last.locator("[data-proposal-act=confirm]").click()
    page.click("[data-return-monday]")
    go(page, base + "/guild/operate")
    open_mc(page)
    say(page, "What did we decide about file this?")
    reply = page.locator(".msg[data-who=mc]").last
    expect(reply).to_contain_text("receipt r-4490")
    expect(reply).to_contain_text("grant Guest → mobile fix (Idea) → coach Admin")
    expect(reply).to_contain_text("Open: Idea q-91")
    reply.locator("a").click()
    page.wait_for_selector("body[data-ready=true]")
    expect(page.locator("#decision")).to_be_visible()


# queue overlay
def test_queue_status_overlay_pending_real_write(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/build/queue")
    expect(page.locator('[data-col="spec_ready"] [data-col-count]')).to_have_text("2")
    card = page.locator("#card-146")
    expect(card.locator("[data-save]")).to_be_hidden()
    card.locator("[data-status-select]").select_option("in_build")
    card.locator("[data-save]").click()
    expect(page.locator('[data-col="in_build"] #card-146')).to_be_visible()
    expect(page.locator("#card-146 [data-pending]")).to_have_text("pending real write · r-4486")
    expect(page.locator('[data-col="spec_ready"] [data-col-count]')).to_have_text("1")
    go(page, base + "/guild/build/items/146")
    expect(page.locator("[data-effective-status]")).to_have_text("in build")
    expect(page.locator("[data-history-local]")).to_contain_text("spec ready → in build · Robert · local only · pending real write · r-4486")
    expect(page.locator('[data-receipts-for="146"]')).to_contain_text("pending real write · local receipt r-4486")
    page.locator("[data-status-select]").select_option("done")
    page.locator("[data-save]").click()
    go(page, base + "/guild/build/queue")
    expect(page.locator("#card-146")).to_be_hidden()
    expect(page.locator("[data-queue-moved]")).to_contain_text("#146 moved to done locally (pending real write · r-4487)")


# 8 ─ phone
@pytest.mark.parametrize("width,height", [(390, 844), (360, 780)])
def test_phone_layout_390_and_360(browser, base, width, height):
    c = browser.new_context(viewport={"width": width, "height": height}, is_mobile=True, has_touch=True)
    page = c.new_page()
    for path in ("/guild", "/guild/build/bench", "/guild/operate", "/guild/build/queue", "/guild/build/items/158"):
        go(page, base + path)
        assert page.evaluate("document.documentElement.scrollWidth") <= width, path
        for sel in (".ps-needs", ".ps-numbers"):
            box = page.locator(sel).bounding_box()
            assert box and box["y"] + box["height"] <= height, (path, sel, box)
        expect(page.locator(".ps-num")).to_have_count(4)
        expect(page.locator(".mc-hold")).to_be_visible()
        expect(page.locator(".mc-hold")).to_contain_text("simulated · not speech recognition")
        page.click("[data-phone-open]")
        expect(page.locator("#main")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth") <= width, path + " (page open)"
    c.close()


def test_phone_essential_path_text_and_simulated_voice(browser, base):
    c = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = c.new_page()
    go(page, base + "/guild/build/bench")
    expect(page.locator(".mc-hints")).to_be_hidden()                    # composer hidden until Type
    page.click(".mc-hold")
    expect(page.locator(".msg[data-who=robert]").last).to_contain_text("voice (simulated)")
    expect(page.locator(".msg[data-who=robert]").last).to_contain_text("Why did usage stall here?")
    expect(page.locator(".msg[data-who=mc]").last).to_contain_text("Simulated reply")
    page.click("[data-mc-type]")
    expect(page.locator("[data-mc-input]")).to_be_visible()
    expect(page.locator(".mc-hints")).to_contain_text("“confirm” · “off the record” · “file this”")
    page.click("[data-mc-sample-attach='0']")
    page.click(".mc-hold")                                               # voice line: "file this"… or confirm pending
    page.click(".mc-hold")
    thread = page.locator("[data-mc-thread]")
    # First hold confirms the pending Needs-you proposal, second proposes filing; then confirm by text
    expect(thread).to_contain_text("posted to Needs you · local receipt r-4486")
    expect(page.locator(".msg-proposal").last).to_contain_text("File this attachment")
    say(page, "confirm")
    expect(thread).to_contain_text("simulated filing · local receipt r-4487 · file not uploaded")
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    c.close()


def test_phone_operate_keeps_conversation_controls_in_view(browser, base):
    c = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = c.new_page()
    go(page, base + "/guild/operate")
    page.click("[data-phone-open]")
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    for selector in ("[data-mc-voice]:visible", "[data-mc-type]"):
        box = page.locator(selector).bounding_box()
        assert box and 0 <= box["y"] and box["y"] + box["height"] <= 844, (selector, box)
    page.click("[data-mc-type]")
    expect(page.locator("[data-mc-input]")).to_be_focused()
    page.click("[data-mc-voice]:visible")
    expect(page.locator(".msg[data-who=mc]").last).to_contain_text("Simulated reply")
    c.close()


def test_live_mode_banner_identifies_mixed_sources(browser):
    srv, url = _serve(sources="live")
    c = browser.new_context()
    try:
        page = c.new_page()
        go(page, url + "/guild")
        expect(page.locator("[data-proto-banner]")).to_contain_text("live + sample")
        expect(page.locator("[data-proto-banner]")).not_to_contain_text("sample data")
    finally:
        c.close()
        srv.shutdown()


def test_new_turn_preserves_reader_position_in_thread(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/build/bench")
    open_mc(page)
    for _ in range(12):
        say(page, "What is the current status?")
    thread = page.locator("[data-mc-thread]")
    assert thread.evaluate("e => e.scrollHeight > e.clientHeight")
    thread.evaluate("e => { e.scrollTop = 0; }")
    say(page, "What changed?")
    assert thread.evaluate("e => e.scrollTop") == 0


def test_keyboard_only_open_and_confirm(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/operate")

    def tab_to(predicate_js, limit=120):
        for _ in range(limit):
            page.keyboard.press("Tab")
            if page.evaluate(predicate_js):
                return
        raise AssertionError("not reachable by Tab: " + predicate_js)

    tab_to("document.activeElement.matches('[data-mc-pill]')")
    assert page.evaluate("getComputedStyle(document.activeElement).outlineStyle") != "none"
    page.keyboard.press("Enter")
    expect(page.locator("[data-mc-panel]")).to_have_attribute("data-mode", "floating")
    assert page.evaluate("document.activeElement.matches('[data-mc-input]')")
    page.keyboard.type("Why did usage stall here?")
    page.keyboard.press("Enter")
    expect(page.locator(".msg-proposal")).to_have_count(1)
    page.focus("[data-mc-input]")
    tab_to("document.activeElement.matches('[data-proposal-act=confirm]')")
    tab_back = page.evaluate("document.activeElement.textContent")
    assert tab_back == "Confirm"
    page.keyboard.press("Enter")
    expect(page.locator(".msg-receipt")).to_contain_text("posted to Needs you · local receipt r-4486")
    page.keyboard.press("Escape")
    expect(page.locator("[data-mc-panel]")).to_have_attribute("data-mode", "pill")


# 9 / 10 ─ network, malformed state, microphone
def test_no_external_network_requests(ctx, base):
    page = ctx.new_page()
    urls = []
    page.on("request", lambda r: urls.append(r.url))
    for path in ("/guild", "/guild/build/bench", "/guild/operate", "/guild/build/queue", "/guild/build/items/158"):
        go(page, base + path)
    open_mc(page)
    say(page, "Bring Claude and Codex in.")
    page.click("[data-proto-reset]"); page.click("[data-proto-reset-yes]")
    page.wait_for_selector("body[data-ready=true]")
    page.wait_for_timeout(300)
    assert urls and all(u.startswith(base + "/") or u.startswith("data:") for u in urls), [u for u in urls if not u.startswith(base)]


def test_malformed_local_state_falls_back_to_defaults(browser, base):
    c = browser.new_context(viewport={"width": 1280, "height": 800})
    c.add_init_script("""
      for (const k of ['bench.v1','conversation.v1','overlay.v1','clock.v1','nav.v1'])
        localStorage.setItem('guild.' + k, k === 'bench.v1' ? '{"layout_version":1,"order":7}' : '{garbage');
    """)
    page = c.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    go(page, base + "/guild/build/bench")
    assert panel_order(page) == ["subject", "needs", "motion", "discussions", "since", "blocked", "postits"]
    expect(page.locator("[data-notice]")).to_contain_text("unreadable; defaults loaded")
    expect(page.locator("[data-clock-label]").first).to_contain_text("Sat 9:12")
    open_mc(page)
    say(page, "Why did usage stall here?")
    expect(page.locator(".msg[data-who=mc]").last).to_contain_text("Simulated reply")
    assert errors == []
    c.close()


def test_no_microphone_request(browser, base):
    c = browser.new_context(viewport={"width": 390, "height": 844})
    c.add_init_script("""
      window.__mic = 0;
      if (navigator.mediaDevices) navigator.mediaDevices.getUserMedia = () => { window.__mic += 1; return Promise.reject(new Error('blocked')); };
    """)
    page = c.new_page()
    go(page, base + "/guild/build/bench")
    page.click(".mc-hold"); page.click(".mc-hold")
    assert page.evaluate("window.__mic") == 0
    c.close()


def test_live_read_failure_shows_unknown_not_zero(browser, tmp_path_factory):
    bad = tmp_path_factory.mktemp("q") / "build_queue.json"
    bad.write_text("{broken")
    srv, url = _serve(sources="live", queue_path=bad, records_db="")
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        go(page, url + "/guild/operate")
        tile = page.locator('[data-tile="build_queue"]')
        expect(tile).to_contain_text("treat as unknown")
        expect(tile.locator(".src-badge")).to_have_text(re.compile(r"^live · .* · unknown$"))
        assert "0 active" not in tile.inner_text()
        go(page, url + "/guild/build/queue")
        expect(page.locator(".unknown-line")).to_contain_text("treat as unknown")
        expect(page.locator(".board")).to_have_count(0)
        meta = page.locator("[data-queue-meta]")
        expect(meta).to_contain_text("Active items: unknown · read failed")
        assert not re.search(r"\b\d+ active", meta.inner_text())
        go(page, url + "/guild/build/bench")
        motion = page.locator('[data-panel="motion"]')
        expect(motion).to_contain_text("treat as unknown")
        expect(motion.locator("circle")).to_have_count(0)
        go(page, url + "/guild")
        expect(page.locator('[data-door="build"]')).to_contain_text("queue: treat as unknown")
        page.close()
    finally:
        srv.shutdown()


# F1 ─ simulated filing labels, confirmed and after reload
def test_simulated_filing_labels_confirmed_and_after_reload(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/operate")
    open_mc(page)
    page.click("[data-mc-sample-attach='1']")
    say(page, "file this")
    page.locator(".msg-proposal").last.locator("[data-proposal-act=confirm]").click()
    label = "simulated filing · local receipt r-4486 · file not uploaded"
    chip = page.locator(".msg-attach").last
    expect(chip).to_contain_text(label)
    expect(chip).not_to_contain_text("file content not kept")      # bytes still in this page
    expect(page.locator(".msg-proposal").last).to_contain_text("Confirmed · " + label)
    page.reload(); page.wait_for_selector("body[data-ready=true]")
    chip = page.locator(".msg-attach").last
    expect(chip).to_contain_text(label)
    expect(chip).to_contain_text("file content not kept")
    expect(chip).to_contain_text("sha256:")
    thread = page.locator("[data-mc-thread]").inner_text()
    assert re.search(r"(?<!simulated )\bfiled\b", thread.replace("not filed", "")) is None
    assert "U02 check: attach control hidden" not in storage(page)


def test_mixed_queue_file_marks_unknown_status_rows(browser, tmp_path_factory):
    path = tmp_path_factory.mktemp("q") / "build_queue.json"
    path.write_text(json.dumps([
        {"id": 1, "status": "spec_ready", "spec_title": "Ready one", "last_transition_at": "2026-09-20T10:00:00Z"},
        {"id": 2, "status": "in_build", "spec_title": "Building two", "last_transition_at": "2026-09-21T10:00:00Z"},
        {"id": 136, "status": "open", "spec_title": "Open one-three-six"},
        {"id": 140, "spec_title": "No status"},
    ]))
    srv, url = _serve(sources="live", queue_path=path, records_db="")
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        go(page, url + "/guild/build/queue")
        expect(page.locator("[data-active-count]")).to_have_text("2")
        expect(page.locator("[data-queue-meta] .src-badge")).to_have_text(re.compile(r"^live · read \d+:\d\d$"))
        expect(page.locator("[data-queue-meta] [data-src-note]")).to_have_text("2 rows with unknown status")
        rows = page.locator("[data-unknown-row]")
        expect(rows).to_have_count(2)
        expect(rows.nth(0)).to_contain_text("unknown status 'open'")
        expect(rows.nth(1)).to_contain_text("unknown status (missing)")
        expect(page.locator('[data-col="spec_ready"] [data-col-count]')).to_have_text("1")
        expect(page.locator('[data-col="in_build"] [data-col-count]')).to_have_text("1")
        rows.nth(0).locator("a").click(); page.wait_for_selector("body[data-ready=true]")
        expect(page.locator("[data-effective-status]")).to_have_text("unknown status 'open'")
        expect(page.locator("[data-save]")).to_be_hidden()
        page.locator("[data-status-select]").select_option("idea")
        page.locator("[data-save]").click()
        expect(page.locator("[data-effective-status]")).to_have_text("idea")
        expect(page.locator("[data-history-local]")).to_contain_text("open → idea · Robert · local only · pending real write")
        go(page, url + "/guild/operate")
        expect(page.locator('[data-tile="build_queue"]')).to_contain_text("2 active")
        expect(page.locator('[data-tile="build_queue"]')).to_contain_text("2 rows with unknown status")
        assert errors == []
        page.close()
    finally:
        srv.shutdown()


# ── rev 3: Shop floor ──────────────────────────────────────────────────────

ZONES = ["status", "needs", "continue", "postits"]


def visible_lights(page):
    return page.eval_on_selector_all("[data-lights-full] > .light:not([hidden])",
                                     "els => Object.fromEntries(els.map(e => [e.dataset.light, e.dataset.lightState]))")


def test_shop_floor_zones_lights_reminders_postits(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/build")
    assert page.title().startswith("Shop floor · Master Craftsman")
    expect(page.locator("[data-panel]")).to_have_count(0)                # no bench panels on arrival
    expect(page.locator("[data-mc-panel]")).to_have_attribute("data-mode", "inpage")
    expect(page.locator("[data-mc-pill]")).to_be_hidden()
    expect(page.locator("[data-mc-input]")).to_be_visible()             # composer always visible
    assert page.locator("[data-mc-input]").bounding_box()["y"] < 800
    assert visible_lights(page) == {"build_queue": "green", "rollouts": "yellow", "systems": "unknown",
                                    "agents": "yellow", "usage": "yellow"}
    reminders = page.locator("[data-reminder]:not([hidden]):not(.is-capped)")
    assert 1 <= reminders.count() <= 3
    expect(page.locator("[data-note]")).to_have_count(4)
    expect(page.locator("[data-open-bench]")).to_be_visible()
    expect(page.locator("[data-proto-banner]")).to_contain_text("Prototype · simulated replies · some sample data")
    assert "drag" not in page.locator("#main").inner_text().lower()
    assert page.evaluate("parseFloat(getComputedStyle(document.querySelector('.msg-text') || document.body).fontSize)") >= 16
    # the cap holds when more rows are eligible (cap forced to 1, then any state change re-applies it)
    page.evaluate("document.querySelector('[data-reminders]').dataset.cap = '1'")
    page.click("[data-mc-record]"); page.click("[data-mc-record]")
    expect(page.locator("[data-reminder]:not([hidden]):not(.is-capped)")).to_have_count(1)
    expect(page.locator("[data-reminder-count]")).to_have_text("1")


def test_shop_floor_zones_are_bounded_sections(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/build")
    info = page.evaluate("""(zones) => {
      const bg = (el) => getComputedStyle(el).backgroundColor;
      const page = bg(document.body);
      const out = {};
      const add = (name, el, header) => {
        const cs = getComputedStyle(el);
        const h = header ? header.getBoundingClientRect() : null;
        out[name] = { bg: bg(el), page, edge: parseFloat(cs.borderTopWidth) + parseFloat(cs.borderLeftWidth),
                      header: header ? header.textContent.trim() : '', headerVisible: !!h && h.height > 0 && h.width > 0 };
      };
      for (const z of zones) { const el = document.querySelector(`[data-zone="${z}"]`); add(z, el, el.querySelector('.zone-h')); }
      const conv = document.querySelector('[data-mc-panel]');
      add('conversation', conv, conv.querySelector('.mc-title'));
      const ban = document.querySelector('[data-proto-banner]');
      add('honesty', ban, ban.querySelector('strong'));
      return out;
    }""", ZONES)
    bgs = set()
    for name, z in info.items():
        assert z["bg"] != z["page"] and z["bg"] not in ("rgba(0, 0, 0, 0)", "transparent"), name
        assert z["headerVisible"] and z["header"], name
        bgs.add(z["bg"])
        if name != "honesty":
            assert z["edge"] > 0, name
    assert len(bgs) >= 5                                                  # each zone reads as its own surface


def test_lights_not_colour_alone(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/build")
    expected = {"green": ("OK", "circle"), "yellow": ("Watch", "triangle"), "red": ("Problem", "square"), "unknown": ("Unknown", "ring")}
    rows = page.eval_on_selector_all("[data-lights-full] > .light:not([hidden])", """els => els.map(e => ({
        state: e.dataset.lightState, word: e.querySelector('[data-light-word]').textContent.trim(),
        shape: e.querySelector('svg').dataset.shape, name: e.querySelector('.light-name').textContent.trim(),
        fill: getComputedStyle(e.querySelector('svg')).fill, src: e.querySelector('.light-src').textContent.trim() }))""")
    assert len(rows) == 5
    green_fill = page.evaluate("getComputedStyle(document.querySelector('[data-light-state=green] svg')).fill")
    for r in rows:
        assert (r["word"], r["shape"]) == expected[r["state"]], r
        assert r["name"] and r["src"] in ("live", "sample", "not instrumented"), r
        if r["state"] == "unknown":
            assert r["fill"] != green_fill, r                                # unknown is never drawn green
    systems = next(r for r in rows if r["name"].startswith("Systems"))
    assert (systems["state"], systems["src"]) == ("unknown", "not instrumented")


def test_light_opens_detail_and_ask_inserts_prompt(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/build")
    page.click('[data-light="agents"] [data-ask]')
    expect(page.locator(".msg[data-who=robert]").last).to_contain_text("Tell me about Agents")
    expect(page.locator(".msg[data-who=mc]").last).to_contain_text("1 needs you")
    expect(page.locator(".msg[data-who=mc]").last).to_contain_text("Simulated reply")
    page.click('[data-light="systems"] [data-ask]')
    expect(page.locator(".msg[data-who=mc]").last).to_contain_text("grey (unknown), not green")
    page.click('[data-light="systems"] [data-light-link]')
    page.wait_for_selector("body[data-ready=true]")
    assert page.url.endswith("/guild/operate?tile=systems")
    expect(page.locator('[data-tile="systems"]')).to_have_attribute("aria-pressed", "true")
    expect(page.locator("[data-mc-thread]")).to_contain_text("Tell me about Agents")   # same thread in the floating panel
    go(page, base + "/guild/build")
    page.click('[data-light="build_queue"] [data-light-link]')
    page.wait_for_selector("body[data-ready=true]")
    assert page.url.endswith("/guild/build/queue")


def test_shop_floor_monday_changes_rollouts_light_and_first_reminder(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/build")
    assert visible_lights(page)["rollouts"] == "yellow"
    shown = page.locator("[data-reminder]:not([hidden]):not(.is-capped)")
    expect(shown.nth(0)).to_contain_text("Limit")                       # rev 3.1: a usage alert outranks Decide/Approve
    expect(shown.nth(1)).to_contain_text("reset window")                # rev 3.1: vendor reset mismatch
    expect(shown.nth(2)).to_contain_text("“file this” stalled")
    say(page, "Agreed. Grant Guest now, mobile fix as an Idea, coach Admin after.")
    page.locator(".msg-proposal").last.locator("[data-proposal-act=confirm]").click()
    expect(page.locator(".msg-receipt").last).to_contain_text("r-4490")
    go(page, base + "/guild/build?clock=mon")
    expect(page.locator('[data-light="rollouts"]:not([hidden])')).to_have_attribute("data-light-state", "green")
    assert visible_lights(page)["rollouts"] == "green"
    first = page.locator("[data-reminder]:not([hidden]):not(.is-capped)").first
    expect(first).to_contain_text("r-4490 recorded: grant Guest → mobile fix (Idea) → coach Admin")
    assert first.locator("a").get_attribute("href").endswith("/guild/build/bench#decision")
    expect(page.locator("[data-mc-thread]")).to_contain_text("Morning. Grant done")


def test_shop_floor_same_thread_as_floating_panel(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/operate")
    open_mc(page)
    say(page, "Why did usage stall here?")
    page.click('[data-section="build"]')
    page.wait_for_selector("body[data-ready=true]")
    assert page.url.endswith("/guild/build")
    expect(page.locator("[data-mc-panel]")).to_have_attribute("data-mode", "inpage")
    expect(page.locator("[data-mc-thread]")).to_contain_text("Why did usage stall here?")
    expect(page.locator("[data-mc-context]")).to_contain_text("Context: Build · Shop floor · recording on")
    page.click('[data-mc-sample-attach="0"]')
    say(page, "file this")
    page.locator(".msg-proposal").last.locator("[data-proposal-act=confirm]").click()
    expect(page.locator(".msg-attach").last).to_contain_text("simulated filing · local receipt r-4486 · file not uploaded")
    go(page, base + "/guild/operate")                                    # the saved floating mode is kept elsewhere
    expect(page.locator("[data-mc-panel]")).to_have_attribute("data-mode", "floating")
    expect(page.locator("[data-mc-thread]")).to_contain_text("simulated filing · local receipt r-4486")


@pytest.mark.parametrize("width,height", [(390, 844), (360, 780)])
def test_phone_shop_floor_first_viewport(browser, base, width, height):
    c = browser.new_context(viewport={"width": width, "height": height}, is_mobile=True, has_touch=True)
    page = c.new_page()
    go(page, base + "/guild/build")
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    expect(page.locator("[data-panel]")).to_have_count(0)
    strip = page.locator("[data-lights-toggle]").bounding_box()
    conv = page.locator("[data-mc-panel] .mc-head").bounding_box()
    dock = page.locator("[data-mc-dock-bottom]").bounding_box()
    hold = page.locator(".mc-hold").bounding_box()
    assert strip["y"] >= 0 and strip["y"] + strip["height"] <= height
    assert conv["y"] + conv["height"] <= height - dock["height"]        # conversation starts above the pinned bar
    assert abs(dock["y"] + dock["height"] - height) <= 1 and hold["y"] >= dock["y"]
    for word in ("OK", "WATCH", "UNKNOWN"):
        expect(page.locator("[data-lights-toggle]")).to_contain_text(word, ignore_case=True)
    expect(page.locator("[data-lights-full]")).to_be_hidden()
    page.click("[data-lights-toggle]")
    expect(page.locator("[data-lights-full]")).to_be_visible()
    expect(page.locator("[data-floor-urgent]")).to_contain_text("Needs you:")
    expect(page.locator("[data-floor-sheet]")).to_be_hidden()
    page.click("[data-sheet-toggle]")
    expect(page.locator("[data-floor-sheet]")).to_be_visible()
    assert page.locator("[data-reminder]:not([hidden]):not(.is-capped)").count() <= 3
    expect(page.locator("[data-note]")).to_have_count(4)
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    dock = page.locator("[data-mc-dock-bottom]").bounding_box()
    assert abs(dock["y"] + dock["height"] - height) <= 1                 # still pinned after scrolling
    page.click(".mc-hold")
    expect(page.locator(".msg[data-who=robert]").last).to_contain_text("voice (simulated)")
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    c.close()


def test_text_contrast_aa_on_zones(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/build")
    say(page, "Why did usage stall here?")
    worst = page.evaluate("""() => {
      const lum = (c) => { const [r, g, b] = c.match(/\\d+(\\.\\d+)?/g).slice(0, 3).map(Number).map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
      const bgOf = (el) => { for (let n = el; n; n = n.parentElement) { const b = getComputedStyle(n).backgroundColor; if (b && !b.startsWith('rgba(0, 0, 0, 0)') && b !== 'transparent') return b; } return 'rgb(255,255,255)'; };
      const sel = '.zone-h, .light-name, .light-word, .light-reason, .reminder a, .reminder-tag, .note, .zone-continue a, .msg-text, .msg-meta, .mc-context, [data-proto-banner] strong, .btn, .btn .small';
      let min = 99, at = '';
      for (const el of document.querySelectorAll(sel)) {
        if (!el.offsetParent) continue;
        const a = lum(getComputedStyle(el).color), b = lum(bgOf(el));
        const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
        if (ratio < min) { min = ratio; at = el.className + ' ' + el.textContent.slice(0, 30); }
      }
      return [min, at];
    }""")
    assert worst[0] >= 4.5, worst


# ── rev 3.1: Usage & limits ────────────────────────────────────────────────

def test_usage_ask_proposal_confirm_receipt(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/build")
    page.click('[data-light="usage"] [data-ask]')
    reply = page.locator(".msg[data-who=mc]").last
    expect(reply).to_contain_text("Tightest limit: ChatGPT / Codex plan at 72 %")
    expect(reply).to_contain_text("around Sun 13:15, before it resets Mon 6:00")          # cites the projected time
    expect(reply).to_contain_text("Codex itself reported")                                # and the vendor's own warning
    expect(reply).to_contain_text("Simulated reply")
    card = page.locator(".msg-proposal").last
    expect(card).to_contain_text("Shift work before a limit")
    expect(card).to_contain_text("Route reviews to Claude Code")
    expect(card).to_contain_text("limit projected Sun 13:15")
    expect(card).to_contain_text("simulated · no routing changed")
    page.click('[data-light="usage"] [data-ask]')                                       # no duplicate proposal
    expect(page.locator(".msg[data-who=mc]").last).to_contain_text("already waiting")
    expect(page.locator(".msg-proposal")).to_have_count(1)
    card.locator("[data-proposal-act=edit]").click()
    page.locator(".msg-proposal").last.locator("input[name=action]").fill("Route reviews and specs to Claude Code")
    page.locator(".msg-proposal").last.locator("[data-slot=editform] button[type=submit]").click()
    page.locator(".msg-proposal").last.locator("[data-proposal-act=confirm]").click()
    receipt = page.locator(".msg-receipt").last
    expect(receipt).to_contain_text("Shift work before a limit · local receipt r-4486 · simulated · no routing changed")
    expect(receipt).to_contain_text("Route reviews and specs to Claude Code")
    assert overlay(page)["receipts"][0]["kind"] == "usage_shift"


def test_usage_needs_you_item_when_yellow(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/build")
    assert visible_lights(page)["usage"] == "yellow"
    shown = page.locator("[data-reminder]:not([hidden]):not(.is-capped)")
    expect(shown).to_have_count(3)                                                      # cap holds
    expect(shown.nth(0)).to_contain_text("Limit")
    expect(shown.nth(0)).to_contain_text("Codex plan 72 %")
    expect(shown.nth(1)).to_contain_text("Check")
    expect(shown.nth(1)).to_contain_text("Codex says resets Mon 6:00 · we assumed Mon 7:00")
    assert "PR #212" not in page.locator("[data-reminders]").evaluate(
        "el => [...el.querySelectorAll('[data-reminder]:not([hidden]):not(.is-capped)')].map(e => e.textContent).join(' ')")  # Approve ranks below the cap
    shown.nth(0).locator("a").click()
    page.wait_for_selector("body[data-ready=true]")
    assert page.url.endswith("/guild/operate?tile=usage")
    expect(page.locator('[data-tile="usage"]')).to_have_attribute("aria-pressed", "true")
    expect(page.locator('[data-usage-clock="sat"] [data-usage-row]')).to_have_count(6)


def test_usage_light_monday_state(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/build?clock=mon")
    lights = visible_lights(page)
    assert lights["usage"] == "unknown"                                                 # Codex reset; Grok still not instrumented
    usage = page.locator('[data-light="usage"]:not([hidden])')
    expect(usage.locator("[data-light-word]")).to_have_text("Unknown")
    expect(usage).to_contain_text("1 source not instrumented or stale: Grok plan")
    expect(page.locator("[data-reminder]:not([hidden]):not(.is-capped)").filter(has_text="Limit")).to_have_count(0)
    usage.locator("[data-ask]").click()
    expect(page.locator(".msg[data-who=mc]").last).to_contain_text("stays grey rather than green")
    expect(page.locator(".msg-proposal")).to_have_count(0)                              # nothing to shift
    go(page, base + "/guild/operate?tile=usage")
    mon = page.locator('[data-usage-clock="mon"]')
    expect(mon).to_be_visible()
    expect(mon.locator('[data-agent="Codex"] [data-projection]')).to_have_text("no projection — only 1 h of data (need 6 h)")


# ── rev 3.1: capture of vendor warnings and refill receipts ────────────────

def _confirm_and_reload(page):
    page.locator(".msg-proposal").last.locator("[data-proposal-act=confirm]").click()
    page.wait_for_load_state("load")
    page.wait_for_timeout(900)                                                          # confirm reloads so the server re-renders
    page.wait_for_selector("body[data-ready=true]")


def test_capture_parsers(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/build")
    got = page.evaluate("""async () => {
      const m = await import('/guild/ui-assets/js/capture.js');
      const bal = [{id: 'xai_api', label: 'xAI API balance'}, {id: 'anthropic_api', label: 'Anthropic API balance'}];
      return {
        receipt: m.parseReceipt('Receipt · xAI · Amount paid $25.00 · Sat 9:20 · Visa ending in 4242', bal),
        noAmount: m.parseReceipt('Receipt from xAI, thank you for your payment', bal),
        warning: m.parseWarning('Codex: you are approaching your usage limit. Resets Mon 6:00. 20% left', ['Claude Code', 'Codex', 'Grok CLI']),
        reached: m.parseWarning('Claude: usage limit reached', ['Claude Code', 'Codex', 'Grok CLI']),
        strip: m.stripPayment('Paid with Mastercard - 1234, billing me@example.com'),
      };
    }""")
    assert got["receipt"] == {"source": "xai_api", "vendor": "xAI", "amount": 25.0, "paid_at": 560}
    assert got["noAmount"]["amount"] is None
    assert got["warning"] == {"tool": "Codex", "kind": "warning", "stated_reset_at": 3240, "stated_remaining_pct": 20}
    assert got["reached"]["kind"] == "limit_reached" and got["reached"]["tool"] == "Claude Code"
    assert "1234" not in got["strip"] and "Mastercard" not in got["strip"] and "example.com" not in got["strip"]


def test_capture_vendor_warning_file_this_updates_row(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/build")
    say(page, "Claude Code says: you are approaching your usage limit. Resets Sat 11:00.")
    say(page, "file this")
    card = page.locator(".msg-proposal").last
    expect(card).to_contain_text("Record vendor warning: Claude Code")
    expect(card).to_contain_text("reported · pasted by Robert")
    expect(card).to_contain_text("Sat 11:00")
    _confirm_and_reload(page)
    expect(page.locator(".msg-receipt").last).to_contain_text("vendor warning recorded · local receipt r-4486 · simulated — Claude Code")
    go(page, base + "/guild/operate?tile=usage")
    row = page.locator('[data-usage-clock="sat"] [data-usage-row][data-agent="Claude Code"]')
    expect(row.locator("[data-vendor-fresh]")).to_contain_text("approaching your usage limit")
    expect(row).to_contain_text("filed here")
    expect(row).to_have_attribute("data-light-state", "yellow")                         # a fresh warning raises it
    mism = page.locator('[data-usage-clock="sat"] [data-usage-mismatch][data-agent="Claude Code"]')
    expect(mism).to_contain_text("Claude Code says resets Sat 11:00 · we assumed Sat 14:00")


def test_capture_refill_receipt_strips_payment_details(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/build")
    say(page, "Receipt from xAI. Amount paid $30.00 on Sat 9:30. Paid with Mastercard - 1234. Billing: robert.sample@example.com")
    thread = page.locator("[data-mc-thread]")
    expect(thread).to_contain_text("original receipt text not stored")
    say(page, "file this")
    card = page.locator(".msg-proposal").last
    expect(card).to_contain_text("Record refill: xAI API balance +$30.00 · Sat 9:30")
    expect(card).to_contain_text("payment details removed")
    _confirm_and_reload(page)
    expect(page.locator(".msg-receipt").last).to_contain_text("refill recorded · local receipt r-4486 · simulated — xAI API balance +$30.00")
    stored = storage(page) + page.evaluate("document.cookie")
    dom = page.evaluate("document.documentElement.outerHTML")
    for secret in ("1234", "Mastercard", "robert.sample@example.com", "example.com"):
        assert secret not in stored, secret
        assert secret not in dom, secret
    go(page, base + "/guild/operate?tile=usage")
    sat = page.locator('[data-usage-clock="sat"]')
    expect(sat.locator('[data-usage-row][data-agent="CoS Agent A"]')).to_contain_text("$44.20 (after refill)")
    expect(sat.locator('[data-usage-row][data-agent="CoS Agent A"] [data-refill]')).to_contain_text("+$30.00 · xAI · paid Sat 9:30")
    expect(sat.locator("[data-usage-overall]")).to_contain_text("refills recorded: $30.00")
    assert "1234" not in page.evaluate("document.documentElement.outerHTML")


def test_capture_receipt_never_keeps_ach_details(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/build")
    say(page, "Receipt from xAI. Amount paid $30.00 on Sat 9:30 by ACH, routing 021000021, account 123456789.")
    thread = page.locator("[data-mc-thread]")
    expect(thread).to_contain_text("Receipt · xAI · $30.00 · Sat 9:30 · original receipt text not stored")
    for secret in ("021000021", "123456789", "ACH"):
        assert secret not in storage(page) + page.evaluate("document.cookie")
        assert secret not in page.evaluate("document.documentElement.outerHTML")
    say(page, "file this")
    expect(page.locator(".msg-proposal").last).to_contain_text("Record refill: xAI API balance +$30.00 · Sat 9:30")


def test_generic_payment_question_remains_conversation(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/build")
    say(page, "How do payments work?")
    expect(page.locator("[data-mc-thread]")).to_contain_text("How do payments work?")
    say(page, "How did payments work in 2026?")
    expect(page.locator("[data-mc-thread]")).to_contain_text("How did payments work in 2026?")
    expect(page.locator("[data-mc-thread]")).not_to_contain_text("original receipt text not stored")


def test_ordinary_talk_mentioning_payment_words_stays_as_typed(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/build")
    thread = page.locator("[data-mc-thread]")
    for line in ("The invoice module needs a spec before Monday.",
                 "I paid attention to the Codex review, looks fine.",
                 "Payment failed for the Codex plan, what now?"):
        say(page, line)
        expect(thread).to_contain_text(line)
    expect(thread).not_to_contain_text("original receipt text not stored")


def test_receipt_without_amount_but_with_account_detail_is_summarised(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/build")
    say(page, "Receipt from xAI, paid by ACH from account 123456789.")
    thread = page.locator("[data-mc-thread]")
    expect(thread).to_contain_text("original receipt text not stored")
    for secret in ("123456789", "ACH"):
        assert secret not in storage(page) + page.evaluate("document.cookie")
        assert secret not in page.evaluate("document.documentElement.outerHTML")


def test_receipt_without_amount_asks_instead_of_guessing(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/build")
    say(page, "Receipt from xAI — thank you for your payment.")
    say(page, "file this")
    expect(page.locator(".msg[data-who=mc]").last).to_contain_text("I can't read an amount in that receipt, so I won't guess")
    expect(page.locator(".msg-proposal")).to_have_count(0)


def test_capture_off_record_not_recorded(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/build")
    page.click("[data-mc-record]")
    say(page, "Receipt from xAI. Amount paid $40.00 on Sat 9:30. Visa ending in 5555.")
    say(page, "file this")
    expect(page.locator(".msg[data-who=mc]").last).to_contain_text("Off the record")
    expect(page.locator(".msg-proposal")).to_have_count(0)
    stored = storage(page) + page.evaluate("document.cookie")
    assert "40.00" not in stored and "5555" not in stored and "evidence_v1" not in page.evaluate("document.cookie")
    page.reload(); page.wait_for_selector("body[data-ready=true]")
    assert "$40.00" not in page.locator("[data-mc-thread]").inner_text()


def _thread_at_bottom(page):
    return page.evaluate("(() => { const t = document.querySelector('[data-mc-thread]');"
                         " return t.scrollHeight - t.scrollTop - t.clientHeight < 40; })()")


def test_thread_follows_newest_after_reload_and_pill_open(ctx, base):
    page = ctx.new_page()
    go(page, base + "/guild/build")
    for _ in range(8):
        say(page, "What is the current status?")
    page.reload(); page.wait_for_selector("body[data-ready=true]")
    assert _thread_at_bottom(page)                                   # was stuck at the top after a reload
    say(page, "and now?")
    assert _thread_at_bottom(page)
    go(page, base + "/guild/operate")                                # floating panel starts as a pill
    page.click("[data-mc-pill]")
    expect(page.locator("[data-mc-panel]")).to_have_attribute("data-mode", "floating")
    assert _thread_at_bottom(page)


def test_phone_usage_table_scrolls_inside_its_box(browser, base):
    c = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = c.new_page()
    go(page, base + "/guild/operate?tile=usage")
    page.click("[data-phone-open]")
    wrap = page.locator('[data-usage-clock="sat"] .table-wrap')
    expect(wrap).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    assert wrap.evaluate("e => e.scrollWidth > e.clientWidth")        # the table scrolls sideways, not the page
    width = page.locator('[data-usage-clock="sat"] [data-usage-row][data-agent="Codex"] th').bounding_box()["width"]
    assert width > 60                                                # columns stay readable
    c.close()
