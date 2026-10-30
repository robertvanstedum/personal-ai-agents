"""Explicit offline Chromium acceptance for Scan interactions.

Run explicitly with installed Chrome and Playwright. All requests are intercepted;
no server, owner state, credentials, jobs or external provider is used.
"""
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright

from domains.curator.research.scan_render import render_scan_page

ROOT = Path(__file__).resolve().parents[1]
TITLE = 'Evidence &quot;quoted&quot; <literal> & café'
ITEM = {"title": TITLE, "author": "Synthetic Author", "url": "https://example.invalid/source?a=1&b=2",
        "has_url": True, "raw": TITLE, "bracket_note": ""}


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        yield browser
        browser.close()


@pytest.fixture
def scan(browser):
    context = browser.new_context(viewport={"width": 1100, "height": 900}, service_workers="block")
    page = context.new_page()
    calls, errors = [], []
    replies = {"/api/research/inbox/add": {"ok": True},
               "/api/research/spawn-thread": {"ok": False, "error": "Synthetic refusal"},
               "/api/research/scan-to-dive": {"ok": False, "error": "Synthetic refusal"}}
    document = {"title": "Synthetic scan", "metadata": {"interest": "Synthetic interest"},
                "analysis_html": "<p>Synthetic findings only.</p>", "bibliography": [ITEM],
                "cost": None, "tokens_in": None, "tokens_out": None}
    html = render_scan_page(document, "abc12")
    page.on("pageerror", lambda error: errors.append(str(error)))

    def route(request_route):
        request = request_route.request
        url = urlsplit(request.url)
        if url.hostname != "renewal.invalid":
            request_route.abort()
            return
        path = url.path
        if path == "/scan":
            request_route.fulfill(status=200, content_type="text/html", body=html)
        elif path.startswith("/research/static/"):
            relative = path.removeprefix("/research/static/")
            if relative not in {"js/annotations.js", "css/annotations.css", "js/nav.js", "css/nav.css"}:
                errors.append("Unexpected static path: " + relative)
                request_route.abort()
                return
            source = ROOT / "domains/curator/research/web/static" / relative
            request_route.fulfill(status=200, content_type="text/javascript" if relative.endswith(".js") else "text/css", body=source.read_text())
        elif path == "/api/research/scans/abc12/bibliography":
            request_route.fulfill(status=200, json={"ok": True, "items": [ITEM]})
        elif path == "/api/research/annotations":
            request_route.fulfill(status=200, json={"annotations": []})
        elif path in replies:
            calls.append((path, request.post_data_json))
            request_route.fulfill(status=200, json=replies[path])
        else:
            errors.append("Unexpected request: " + path)
            request_route.abort()

    context.route("**/*", route)
    try:
        page.goto("https://renewal.invalid/scan", timeout=10000)
        expect(page.locator("#st-queries input")).to_have_count(1)
        yield page, calls, replies
        assert errors == []
    finally:
        context.close()


def test_add_to_inbox_preserves_literal_provenance(scan):
    page, calls, replies = scan
    page.locator(".btn-add-research").click()
    expect(page.locator("#tick-0")).to_be_visible()
    expect(page.locator(".btn-add-research")).to_be_hidden()
    assert calls == [("/api/research/inbox/add", {
        "title": TITLE, "url": ITEM["url"], "has_url": True,
        "author": ITEM["author"], "raw": TITLE, "scan_id": "abc12",
    })]


def test_inbox_refusal_keeps_retry_available(scan):
    page, calls, replies = scan
    replies["/api/research/inbox/add"] = {"ok": False, "error": "Synthetic refusal"}
    page.locator(".btn-add-research").click()
    expect(page.locator("#toast")).to_have_text("Error: Synthetic refusal")
    expect(page.locator(".btn-add-research")).to_be_enabled()
    expect(page.locator("#tick-0")).to_be_hidden()
    assert len(calls) == 1


def test_invalid_thread_slug_does_not_dispatch(scan):
    page, calls, replies = scan
    page.locator("#st-topic").fill("../invalid")
    page.locator("#st-launch").click()
    expect(page.locator("#st-status")).to_contain_text("lowercase")
    assert calls == []


def test_thread_query_preserves_literal_title_and_refusal_restores_button(scan):
    page, calls, replies = scan
    expect(page.locator("#st-queries input")).to_have_value(TITLE)
    page.locator("#st-topic").fill("synthetic-topic")
    page.locator("#st-launch").click()
    expect(page.locator("#st-status")).to_have_text("Synthetic refusal")
    expect(page.locator("#st-launch")).to_be_enabled()
    assert calls[0] == ("/api/research/spawn-thread", {
        "topic": "synthetic-topic", "motivation": "Synthetic interest", "queries": [TITLE],
        "scan_id": "abc12", "scan_title": "Synthetic scan", "duration_days": 5,
    })
    assert page.locator("#st-queries literal").count() == 0


def test_dive_refusal_restores_launch_control(scan):
    page, calls, replies = scan
    page.locator("#btn-dive").click()
    expect(page.locator("#dive-status")).to_have_text("Synthetic refusal")
    expect(page.locator("#btn-dive")).to_be_enabled()
    expect(page.locator("#btn-dive")).to_have_text("Generate Deeper Dive")
    assert calls == [("/api/research/scan-to-dive", {"hash_id": "abc12"})]


def test_catalog_expand_and_collapse_preserve_literal_titles(browser):
    from datetime import datetime
    from domains.curator.research.scan_index_render import render_catalog
    scans=[{'title':f'Article {n} <literal>', 'source':'Example', 'hash_id':f'id{n}', 'date':datetime(2026,10,29)} for n in range(6)]
    dives=[{'topic':f'Thread {n}', 'excerpt':'A finding', 'stats':'2 sessions', 'stem':f'topic-{n}', 'date':datetime(2026,10,29)} for n in range(4)]
    context=browser.new_context(service_workers='block')
    context.route('**/*', lambda route: route.abort())
    page=context.new_page();errors=[]
    page.on('pageerror',lambda error: errors.append(str(error)))
    try:
        page.set_content(render_catalog(scans,dives),wait_until='domcontentloaded')
        expect(page.locator('.row-article:visible')).to_have_count(5)
        expect(page.locator('.row-thread:visible')).to_have_count(3)
        page.locator('#section-articles .toggle-btn').click()
        expect(page.locator('.row-article:visible')).to_have_count(6)
        expect(page.locator('.row-article').last).to_contain_text('Article 5 <literal>')
        assert page.locator('literal').count()==0
        page.locator('#section-articles .toggle-btn').click()
        expect(page.locator('.row-article:visible')).to_have_count(5)
        page.locator('#section-threads .expand-footer').click()
        expect(page.locator('.row-thread:visible')).to_have_count(4)
        assert errors==[]
    finally:
        context.close()
