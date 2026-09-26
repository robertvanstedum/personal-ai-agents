"""Opt-in real-Chrome checks for the package U preview (simulated fixtures only).

Not auto-collected (same convention as browser_checks.py). Run explicitly:
    ROOMS_UI_EVIDENCE_DIR=<dir> python -m pytest -q -p no:cacheprovider tests/browser_v2_checks.py
Starts the app with the preview flag on an ephemeral loopback port and a temporary data directory.
Screenshots go to ROOMS_UI_EVIDENCE_DIR (or a pytest temporary directory when unset).
"""
import json
import os
from pathlib import Path
import re
import socket
import sys
from threading import Thread

import pytest
from jsonschema import Draft202012Validator
from playwright.sync_api import expect, sync_playwright
from referencing import Registry, Resource
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import create_app

WIDTHS = [1280, 1024, 736, 390, 360, 320]
SCREENS = ["save", "continue", "collaborate", "work", "work-detail"]
ZERO_COUNT = re.compile(r"\b0\s+(open|exceptions?|agents?|assignments?|records?|requests?|running|items?|participants?)\b", re.IGNORECASE)


@pytest.fixture(scope="module")
def base_url(tmp_path_factory):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    app = create_app(tmp_path_factory.mktemp("preview-data") / "private", port=port, testing=True, ui_preview=True)
    server = make_server("127.0.0.1", port, app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        yield browser
        browser.close()


@pytest.fixture(scope="module")
def evidence_dir(tmp_path_factory):
    configured = os.environ.get("ROOMS_UI_EVIDENCE_DIR")
    path = Path(configured) if configured else tmp_path_factory.mktemp("u-evidence")
    path.mkdir(parents=True, exist_ok=True)
    return path


class Tab:
    """A page plus everything it requested, so every journey can assert it never reached the API."""

    counter = 0

    def __init__(self, browser, base_url, width=1280, height=900, **context_options):
        self.context = browser.new_context(viewport={"width": width, "height": height}, **context_options)
        self.page = self.context.new_page()
        self.base_url = base_url
        self.requests, self.errors = [], []
        self.page.on("request", lambda request: self.requests.append(request.url))
        self.page.on("pageerror", lambda error: self.errors.append(f"pageerror: {error}"))
        self.page.on("console", lambda message: self.errors.append(f"console: {message.text}") if message.type == "error" else None)

    def show(self, scenario, view, wait=True):
        Tab.counter += 1
        self.page.goto(f"{self.base_url}/preview/?scenario={scenario}&load={Tab.counter}#{view}")
        if wait:
            # A locator assertion, not wait_for_function: the page's CSP (no unsafe-eval) blocks string predicates.
            expect(self.page.locator("#view")).to_have_attribute("aria-busy", "false")
            expect(self.page.locator("#view-title")).to_be_visible()
        return self.page

    def close(self):
        api = [url for url in self.requests if "/api/" in url]
        http_adapter = [url for url in self.requests if "adapter_http" in url]
        self.context.close()
        assert not api, f"Preview reached the API: {api}"
        assert not http_adapter, f"Preview loaded the HTTP adapter: {http_adapter}"
        assert not self.errors, self.errors


@pytest.fixture
def tab(browser, base_url):
    opened = []

    def open_tab(width=1280, height=900, **options):
        created = Tab(browser, base_url, width, height, **options)
        opened.append(created)
        return created

    yield open_tab
    for created in opened:
        created.close()


def body_text(page):
    return page.locator("body").inner_text()


# ------------------------------------------------------------------ layout

@pytest.mark.parametrize("width", WIDTHS)
def test_widths_no_horizontal_overflow(tab, width, evidence_dir):
    t = tab(width, 900)
    cases = [("normal", screen) for screen in SCREENS] + [("recovery", "work-detail"), ("store_unavailable", "work"), ("stale", "continue")]
    failures = []
    for scenario, screen in cases:
        page = t.show(scenario, screen)
        banner = page.locator(".preview-banner")
        expect(banner).to_be_visible()
        expect(banner).to_contain_text("Preview · simulated data · actions change nothing")
        scroll_width, inner_width = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
        if scroll_width > inner_width:
            wide = page.evaluate("""() => [...document.querySelectorAll('body *')]
                .filter(e => e.getBoundingClientRect().right > innerWidth + 1).slice(0, 5)
                .map(e => e.tagName + '.' + e.className)""")
            failures.append(f"{scenario}#{screen} at {width}: scrollWidth {scroll_width} > {inner_width} {wide}")
        if width in (1280, 390):
            page.evaluate("window.scrollTo(0, 0)")
            name = f"u-{screen}-{width}.png" if scenario == "normal" else f"u-{scenario.replace('_', '-')}-{screen}-{width}.png"
            page.screenshot(path=str(evidence_dir / name), full_page=True)
    assert not failures, "\n".join(failures)


# ------------------------------------------------------------------ states

def test_normal_state_text(tab):
    page = tab().show("normal", "work")
    text = body_text(page)
    for expected in ("No open exceptions · checked by Operations monitor", "Current assignments", "Waiting · tool", "Running",
                     "Configured, not running", "Accepted · Robert · Sep 25, 10:05 CT", "Release not requested",
                     "Last contact: heartbeat · 10:41 CT", "Last progress: self-report · 10:41 CT"):
        assert expected in text, expected


def test_stale_state_text(tab):
    t = tab()
    assert "Stale: the monitor last ran at 10:12 CT" in body_text(t.show("stale", "work"))
    assert "Interpretation is stale: source rec-7f1c has revision 3; this view cites revision 2." in body_text(t.show("stale", "continue"))
    collaborate = body_text(t.show("stale", "collaborate"))
    assert "Not working now" in collaborate and "(stale)" in collaborate and "Working\n" not in collaborate
    detail = body_text(t.show("stale", "work-detail/att-room-021-2"))
    assert "Stale" in detail and "Last heartbeat received" in detail


def test_store_unavailable_says_unknown_never_zero(tab):
    t = tab()
    for screen in SCREENS[1:]:
        page = t.show("store_unavailable", screen)
        text = body_text(page)
        assert "Unknown" in text, screen
        assert not ZERO_COUNT.search(text), f"{screen}: {ZERO_COUNT.search(text).group(0)}"
        for claim in ("None open", "No open exceptions", "No work assigned", "No agents", "No records saved", "No requests", "No completed"):
            assert claim not in text, f"{screen} claims '{claim}' while the store is unreadable"
    page = t.show("store_unavailable", "save")
    page.locator("#save-text").fill("Synthetic text that cannot be stored right now.")
    page.get_by_role("button", name="Save · simulated").click()
    expect(page.locator("#announcer")).to_contain_text("Not saved · simulated")
    expect(page.locator("#save-text")).to_have_value("Synthetic text that cannot be stored right now.")


def test_empty_state_text(tab):
    t = tab()
    work = body_text(t.show("empty", "work"))
    for expected in ("No work assigned", "No completed work yet", "No open exceptions", "Configured, not running"):
        assert expected in work, expected
    assert "No records saved yet" in body_text(t.show("empty", "continue"))
    collaborate = body_text(t.show("empty", "collaborate"))
    assert "No requests in this session" in collaborate and "No context shared into this session yet" in collaborate
    assert "No work to show" in body_text(t.show("empty", "work-detail"))


def test_error_state_with_retry(tab):
    t = tab()
    for screen in SCREENS[1:]:
        page = t.show("error", screen)
        alert = page.get_by_role("alert")
        expect(alert).to_contain_text("Couldn't load")
        expect(alert).to_contain_text("Tried 1 time")
        page.get_by_role("button", name="Retry").click()
        expect(page.get_by_role("alert")).to_contain_text("Tried 2 times")
        expect(page.get_by_role("button", name="Retry")).to_be_focused()
    page = t.show("error", "save")
    page.locator("#save-text").fill("Synthetic text")
    page.get_by_role("button", name="Save · simulated").click()
    expect(page.locator(".action-error")).to_contain_text("Couldn't complete the simulated action")
    expect(page.locator(".action-error").get_by_role("button", name="Retry")).to_be_focused()


def test_recovery_state_text(tab):
    t = tab()
    work = body_text(t.show("recovery", "work"))
    assert "A result needs reconciliation" in work and "Outcome uncertain" in work
    page = t.show("recovery", "work-detail")
    text = body_text(page)
    for expected in ("Recovery: did the result arrive?", "Last confirmed state", "Known", "Unknown", "Master Craftsman (configured, not running)",
                     "Closure requires a matching receipt and a verified result. A timeout does not tell us whether the save failed.",
                     "Check saved result · simulated", "Pause recovery · simulated", "Response did not arrive"):
        assert expected in text, expected
    assert page.locator("h1").inner_text() == "Room handoff adapter"


def test_loading_state(tab):
    page = tab().show("loading", "work", wait=False)
    expect(page.locator(".panel--loading")).to_contain_text("Loading work overview")
    page.wait_for_timeout(800)
    expect(page.locator("#view")).to_have_attribute("aria-busy", "true")


# ------------------------------------------------------------------ keyboard, status text, marking

def tab_until(page, predicate_js, limit):
    for _ in range(limit):
        page.keyboard.press("Tab")
        if page.evaluate(predicate_js):
            return True
    return False


def test_keyboard_only_journey(tab, evidence_dir):
    page = tab().show("recovery", "work")
    assert tab_until(page, "document.activeElement.matches('.side-nav a') && document.activeElement.textContent.trim() === 'Work detail'", 15)
    page.keyboard.press("Enter")
    expect(page.locator("#view-title")).to_have_text("Room handoff adapter")
    expect(page.locator("#view-title")).to_be_focused()
    assert tab_until(page, "document.activeElement.textContent.trim() === 'Check saved result · simulated'", 60)
    page.keyboard.press("Enter")
    announcer = page.locator("#announcer")
    expect(announcer).to_have_attribute("aria-live", "polite")
    expect(announcer).to_contain_text("Receipt found · simulated")
    expect(page.locator(".receipt-status")).to_contain_text("Receipt found · simulated")
    expect(page.get_by_role("button", name="Check saved result · simulated")).to_be_disabled()
    assert page.evaluate("document.activeElement !== document.body")
    assert "Closure requires a matching receipt" in body_text(page)
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path=str(evidence_dir / "u-recovery-after-check-1280.png"), full_page=True)
    assert tab_until(page, "document.activeElement.textContent.trim() === 'Pause recovery · simulated'", 10)
    page.keyboard.press("Enter")
    expect(announcer).to_contain_text("Recovery paused · simulated")


# ------------------------------------------------------------------ malformed links (Codex review of #226)

@pytest.mark.parametrize("fragment", ["%E0%A4%A", "%", "constructor", "__proto__", "toString", "does-not-exist", "work%2Fdetail"])
def test_malformed_fragment_falls_back_to_work_overview(tab, fragment):
    page = tab().show("normal", fragment)
    expect(page.locator("#view-title")).to_have_text("Work in progress")
    expect(page).to_have_url(re.compile(r"#work$"))
    expect(page.locator("#announcer")).to_contain_text("did not match a preview screen")
    assert page.locator(".panel--error").count() == 0


@pytest.mark.parametrize("fragment,title", [
    ("work-detail/constructor", "Work detail"),
    ("work-detail/toString", "Work detail"),
    ("continue/constructor/r1/s1", "Continue where you left off"),
    ("continue/__proto__", "Continue where you left off"),
    ("continue/rec-7f1c/r99", "Continue where you left off"),
    ("collaborate/constructor", "Collaborate"),
])
def test_unknown_ids_show_not_found_not_an_error(tab, fragment, title):
    page = tab().show("normal", fragment)
    expect(page.locator("#view-title")).to_have_text(title)
    expect(page.locator('[data-status="not_found"]').first).to_be_visible()
    assert page.locator(".panel--error").count() == 0


def test_status_chips_have_text(tab):
    t = tab()
    for scenario in ("normal", "recovery", "stale", "store_unavailable", "empty"):
        for screen in SCREENS:
            page = t.show(scenario, screen)
            chips = page.locator(".chip")
            empty = page.evaluate("[...document.querySelectorAll('.chip')].filter(c => !c.innerText.trim()).length")
            assert empty == 0, f"{scenario}#{screen}: {empty} chips without text"
            if screen != "save" and scenario != "empty":
                assert chips.count() > 0, f"{scenario}#{screen} has no status chips"


def test_simulated_marking(tab):
    t = tab()
    for scenario in ("normal", "recovery", "store_unavailable"):
        for screen in SCREENS:
            page = t.show(scenario, screen)
            unmarked = page.evaluate("[...document.querySelectorAll('[data-item]')].filter(e => !e.querySelector('.sim-tag')).length")
            assert unmarked == 0, f"{scenario}#{screen}: {unmarked} data items without a Simulated tag"
            labels = page.evaluate("[...document.querySelectorAll('.sim-action')].map(e => e.textContent.trim())")
            assert all(label.endswith("· simulated") for label in labels), labels
            buttons = page.evaluate("[...document.querySelectorAll('#view button')].map(b => b.textContent.trim())")
            actions = [b for b in buttons if b not in {"Retry"}]
            assert all(b.endswith("· simulated") for b in actions), actions


def test_touch_targets_and_focus_ring(tab):
    for width in (390, 1280):
        t = tab(width)
        for scenario, screen in [("normal", s) for s in SCREENS] + [("recovery", "work-detail")]:
            page = t.show(scenario, screen)
            small = page.evaluate("""() => [...document.querySelectorAll(
                'button, select, input, textarea, .side-nav a, .btn, .cite, .row-link, .link-button, .switcher a, .record-link a, .table td > a, .card-head h3 a')]
                .filter(e => e.offsetParent !== null)
                .map(e => [e.tagName + ' ' + (e.textContent || e.id).trim().slice(0, 40), e.getBoundingClientRect()])
                .filter(([, r]) => r.width < 24 || r.height < 24).map(([n, r]) => `${n} ${Math.round(r.width)}x${Math.round(r.height)}`)""")
            assert not small, f"{scenario}#{screen} at {width}: {small}"
    page = tab().show("normal", "work")
    assert tab_until(page, "document.activeElement.matches('.side-nav a')", 10)
    outline = page.evaluate("(() => { const s = getComputedStyle(document.activeElement); return [s.outlineStyle, parseFloat(s.outlineWidth)]; })()")
    assert outline[0] != "none" and outline[1] >= 2, outline


def test_reduced_motion_honoured(tab):
    reduced = tab(reduced_motion="reduce").show("normal", "work")
    assert reduced.evaluate("getComputedStyle(document.querySelector('.btn')).transitionDuration") == "0s"
    assert reduced.evaluate("getComputedStyle(document.documentElement).scrollBehavior") == "auto"
    normal = tab(reduced_motion="no-preference").show("normal", "work")
    assert normal.evaluate("getComputedStyle(document.querySelector('.btn')).transitionDuration") != "0s"


# ------------------------------------------------------------------ journeys

def test_save_flow_shows_receipt_stages_and_keeps_submitter_apart(tab):
    page = tab().show("normal", "save")
    expect(page.locator("#coverage-complete")).to_be_disabled()  # excerpt is never complete
    page.locator("#save-title").fill("Synthetic planning note")
    page.locator("#save-text").fill("Robert: Keep the first build local.\n\nCodex: Agreed, production hosting later.")
    page.locator("#save-missing").fill("attachment: diagram.png")
    page.locator("#save-speakers").fill("Robert, Codex")
    page.get_by_role("button", name="Save · simulated").click()
    expect(page.locator("#announcer")).to_contain_text("Saved · simulated")
    receipt = page.locator(".receipt")
    for expected in ("Original: committed", "Extraction: not applicable", "Indexing: done",
                     "Central availability: not connected — local proof", "Robert (authenticated)",
                     "Robert, Codex — declared, unverified", "Missing: attachment: diagram.png", "rcpt-sim1-1"):
        expect(receipt).to_contain_text(expected)
    page.locator(".receipt").get_by_role("link", name="rec-sim1@1").click()
    expect(page.locator("#reader-h")).to_have_text("Synthetic planning note")
    expect(page.locator(".segment").first).to_contain_text("declared, unverified")


def test_citation_link_focuses_the_segment_and_decisions_stay_honest(tab):
    page = tab().show("normal", "continue")
    owner = page.locator(".where-block", has=page.get_by_role("heading", name="Owner decisions"))
    reported = page.locator(".where-block", has=page.get_by_role("heading", name="Reported, unverified"))
    expect(owner).to_contain_text("Decided by Robert · Sep 25, 09:58 CT")
    expect(owner).not_to_contain_text("Robert approved deployment")
    expect(reported).to_contain_text("Robert approved deployment")
    expect(reported).to_contain_text("uploaded by Robert")
    expect(page.locator(".where")).to_contain_text("test adapter")
    reported.get_by_role("link", name="rec-2b90@1#4").click()
    expect(page.locator("#reader-h")).to_have_text("Synthetic go/no-go transcript")
    expect(page.locator("#seg-rec-2b90-1-4")).to_be_focused()
    expect(page.locator("#seg-rec-2b90-1-4")).to_contain_text("declared, unverified")


def test_collaborate_answer_and_share(tab):
    page = tab().show("normal", "collaborate")
    text = body_text(page)
    assert "Robert's CoS has standing read access" in text and "not membership" in text
    assert "Access ready, not joined" in text and "No join receipt" in text
    page.locator("#answer-REQ-32").fill("Proceed with the excerpt; keep the gaps listed.")
    page.get_by_role("button", name="Answer · simulated").click()
    expect(page.locator("#announcer")).to_contain_text("Answer sent · simulated")
    expect(page.locator("#inbox-h")).to_be_focused()
    expect(page.locator("section[aria-labelledby='inbox-h']")).to_contain_text("Nothing waiting for you")
    page.locator("#share-record").select_option(label="Synthetic go/no-go transcript (rec-2b90@1)")
    page.get_by_role("button", name="Share into session · simulated").click()
    expect(page.locator("#announcer")).to_contain_text("Shared · simulated under disclosure grant dg-sim-1")
    expect(page.locator("section[aria-labelledby='context-h']")).to_contain_text("under disclosure grant dg-sim-1")


# ------------------------------------------------------------------ contract

def schema_validators():
    schemas = [json.loads(path.read_text()) for path in (ROOT / "contracts").glob("*.schema.json")]
    by_name = {path.name: json.loads(path.read_text()) for path in (ROOT / "contracts").glob("*.schema.json")}
    registry = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in schemas)
    index = json.loads((ROOT / "contracts" / "index.json").read_text())["contracts"]
    return {sid: Draft202012Validator(by_name[name], registry=registry, format_checker=Draft202012Validator.FORMAT_CHECKER)
            for sid, name in index.items()}


def test_adapter_outputs_match_schemas(tab):
    page = tab().show("normal", "work")
    result = page.evaluate("""async () => {
        const { createFixtureAdapter, SCENARIOS, DEFAULT_SESSION_ID } = await import('/static/v2/adapter_fixture.js');
        const out = [];
        for (const scenario of SCENARIOS) {
            if (scenario === 'error' || scenario === 'loading') continue;
            const a = createFixtureAdapter({ scenario });
            out.push(await a.getOverview());
            for (const id of ['att-room-021-2', 'att-c2c-003-1', 'att-capture-014-1', 'att-export-009-1', 'att-missing']) out.push(await a.getAttempt(id));
            out.push(await a.listRecords());
            for (const id of ['rec-7f1c', 'rec-5d31', 'rec-2b90', 'rec-9a04', 'rec-3e77', 'rec-missing']) {
                out.push(await a.getRecord(id)); out.push(await a.getInterpretation(id));
            }
            out.push(await a.getRecord('rec-7f1c', 2));
            out.push(await a.getSession(DEFAULT_SESSION_ID)); out.push(await a.getSession('ses-missing'));
            out.push(await a.act({ type: 'check_saved_result', target: 'att-room-021-2' }));
            out.push(await a.getAttempt('att-room-021-2'));
            out.push(await a.act({ type: 'pause_recovery', target: 'att-room-021-2' }));
            out.push(await a.getAttempt('att-room-021-2'));
            out.push(await a.act({ type: 'answer_request', target: 'REQ-32', session: DEFAULT_SESSION_ID, answer: 'Proceed.' }));
            out.push(await a.act({ type: 'share_selected_context', target: DEFAULT_SESSION_ID, source: { source_id: 'rec-2b90', revision: 1 } }));
            const saved = await a.saveRecord({ title: 'Synthetic', text: 'Robert: Keep this.\\n\\nCodex: Agreed.', source_application: 'Codex',
                fidelity: 'excerpt', coverage: { state: 'partial', missing: ['attachment: diagram.png'], note: null }, declared_speakers: ['Robert', 'Codex'] });
            out.push(saved);
            if (saved.record) { out.push(await a.getRecord(saved.record.source_id)); }
            out.push(await a.act({ type: 'save_record', target: 'workspace:robert', draft: { text: 'x', fidelity: 'excerpt', coverage: { state: 'complete', missing: [] } } }));
            out.push(await a.listRecords()); out.push(await a.getOverview()); out.push(await a.getSession(DEFAULT_SESSION_ID));
        }
        let threw = false;
        try { await createFixtureAdapter({ scenario: 'error' }).getOverview(); } catch (e) { threw = e.name === 'FixtureAdapterError'; }
        return { out, threw };
    }""")
    validators = schema_validators()
    assert result["threw"]
    assert len(result["out"]) > 150
    for document in result["out"]:
        assert document["simulated"] is True
        problems = list(validators[document["schema"]].iter_errors(document))
        assert not problems, (document.get("schema"), [p.message for p in problems[:3]])
    saved = [d for d in result["out"] if d["schema"] == "minimoi.ui.action_result/1" and d["outcome"] == "saved"]
    assert saved and all(d["record"]["submitter"]["authenticated"] and d["record"]["stages"]["central"] == "not_connected" for d in saved)
