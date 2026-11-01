"""Offline Chromium acceptance for CoS's In flight table (Review 06, slice 3), against a real temporary CoS store.

The page is the real cos_ui.html; its two routes are the real In flight blueprint over a StoreWriter in a temporary
folder (the portal's owner and CSRF gates are stood in for). Add a row, change its status and priority, comment, Done
moves it to Earlier, Download for Excel, the phone's cards, and the plain "not recorded" state without a store.
Screenshots go to COS_FLIGHT_SHOTS when it is set."""
import csv
import io
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from flask import Flask, jsonify, render_template
from playwright.sync_api import expect, sync_playwright

from core.founders_ui import install
from domains.cos.history import History
from domains.cos.history_api import create_blueprint as history_blueprint
from domains.cos.in_flight import NOT_CONFIGURED, create_blueprint
from domains.cos.store import StoreWriter

ROOT = Path(__file__).resolve().parents[1]
SHOTS = os.environ.get('COS_FLIGHT_SHOTS')


def _app(tmp_path, writer):
    app = Flask(__name__, template_folder=str(ROOT / 'domains/cos/templates'), static_folder=str(ROOT / 'domains/cos/static'))
    install(app)
    app.register_blueprint(history_blueprint(History(tmp_path / 'history', 'dev'), resolve_owner=lambda request: 1))
    app.register_blueprint(create_blueprint(lambda: writer, is_owner=lambda: True, write_verified=lambda: True))

    @app.get('/ui/<tab>')
    def page(tab):
        return render_template('cos_ui.html', initial_tab=tab, managed_realtime_ready=False)

    @app.get('/ui/files')
    def files():
        return jsonify(available=True, files=[])
    return app.test_client()


def _open(p, client, width, height=900):
    browser = p.chromium.launch(channel='chrome', headless=True)
    context = browser.new_context(viewport={'width': width, 'height': height}, service_workers='block', accept_downloads=True)
    tab = context.new_page()
    errors = []
    tab.on('pageerror', lambda e: errors.append(str(e)))

    def route(r):
        u = urlsplit(r.request.url)
        if u.hostname != 'founders.invalid':
            return r.fulfill(status=404, body='')
        response = client.open(u.path + ('?' + u.query if u.query else ''), method=r.request.method,
                               data=r.request.post_data, content_type=r.request.headers.get('content-type'))
        r.fulfill(status=response.status_code, body=response.data, headers={'content-type': response.content_type})
    tab.route('**/*', route)
    tab.goto('https://founders.invalid/ui/flight')
    tab.wait_for_function('window.cosInFlight?.ready === true')
    return browser, tab, errors


def _add(tab, title):
    field = tab.locator('[data-fl-new]')
    field.fill(title)
    field.press('Enter')
    expect(tab.locator('[data-fl-row]', has_text=title)).to_have_count(1)


def test_in_flight_add_change_comment_done_and_download(tmp_path):
    writer = StoreWriter(tmp_path / 'store', 'dev')
    client = _app(tmp_path, writer)
    with sync_playwright() as p:
        browser, tab, errors = _open(p, client, 1440)
        expect(tab.locator('#tab-flight')).to_be_visible()
        expect(tab.locator('.tab-btn[data-tab="flight"]')).to_have_class(re.compile(r'\bactive\b'))
        expect(tab.locator('body')).to_have_class(re.compile(r'\bshow-cos-flight\b'))
        expect(tab.locator('[data-fl-state]')).to_be_hidden()
        expect(tab.locator('.fl-table thead th')).to_have_count(7)
        _add(tab, 'Send the portfolio walkthrough to Thursday’s two')
        _add(tab, 'Who else builds personal agent teams, and what they charge')
        _add(tab, 'One builder, one host: what breaks if I’m away two weeks')
        assert len(writer.docs('cos.in_flight')) == 3
        rows = tab.locator('tbody [data-fl-row]')
        # Status: a dropdown; Waiting shows under Waiting.
        first = tab.locator('[data-fl-row]', has_text='Who else builds')
        first.locator('[data-fl-status]').select_option('waiting')
        expect(first.locator('[data-fl-status]')).to_have_value('waiting')
        tab.locator('[data-fl-view="waiting"]').click()
        expect(rows).to_have_count(1)
        tab.locator('[data-fl-view="open"]').click()
        expect(rows).to_have_count(3)
        # Priority: click the chip to change it; the table sorts by priority, then last moved.
        walk = tab.locator('[data-fl-row]', has_text='Send the portfolio')
        walk.locator('[data-fl-priority]').click()
        expect(walk.locator('[data-fl-priority]')).to_have_text('P3')
        walk.locator('[data-fl-priority]').click()
        expect(walk.locator('[data-fl-priority]')).to_have_text('P1')
        expect(rows.first).to_contain_text('Send the portfolio')
        tab.locator('th [data-fl-sort="title"]').click()
        expect(rows.first).to_contain_text('One builder')
        tab.locator('th [data-fl-sort="priority"]').click()
        expect(rows.first).to_contain_text('Send the portfolio')
        # A row opens on the right with its thread; a comment shows there and as the latest in the table.
        first.click()
        panel = tab.locator('[data-fl-panel]')
        expect(panel).to_be_visible()
        expect(panel).to_contain_text('Who else builds personal agent teams')
        expect(panel.locator('button[title="later"]')).to_have_count(2)
        tab.locator('[data-fl-comment]').fill('Look at the per-seat ones first.')
        tab.locator('[data-fl-comment]').press('Enter')
        expect(panel.locator('[data-fl-thread]')).to_contain_text('You: Look at the per-seat ones first.')
        tab.locator('[data-fl-comment]').fill('And the two that charge per seat.')
        panel.locator('[data-fl-add-comment]').click()
        expect(panel.locator('.fl-trail')).to_have_count(2)
        expect(first.locator('.cm')).to_contain_text('And the two that charge per seat.')
        box = panel.bounding_box()
        assert box['x'] + box['width'] == pytest.approx(1440, abs=1) and box['width'] == pytest.approx(400, abs=2)
        if SHOTS:
            tab.screenshot(path=f'{SHOTS}/in-flight-row-open-1440.png')
        # Download for Excel: what's shown, as a .csv.
        with tab.expect_download() as info:
            tab.locator('[data-fl-download]').click()
        download = info.value
        assert download.suggested_filename.endswith('.csv')
        text = Path(download.path()).read_text(encoding='utf-8-sig')
        table = list(csv.reader(io.StringIO(text)))
        assert table[0] == ['Priority', 'Item', 'Kind', 'With', 'Status', 'Moved', 'Latest comment'] and len(table) == 4
        assert table[1][:2] == ['P1', 'Send the portfolio walkthrough to Thursday’s two']
        # Done moves it to Earlier.
        panel.locator('[data-fl-done]').click()
        expect(rows).to_have_count(2)
        week = tab.locator('[data-fl-week]').first
        expect(week.locator('summary')).to_contain_text('This week')
        expect(week.locator('summary')).to_contain_text('1 closed')
        expect(week.locator('.fl-done-row')).to_contain_text('Who else builds')
        closed = writer.get('cos.in_flight', writer.docs('cos.in_flight')[0]['item_id'])
        assert sum(1 for r in writer.docs('cos.in_flight') if r['closed_at']) == 1 and closed is not None
        tab.locator('[data-fl-close]').last.click()
        expect(panel).to_be_hidden()
        if SHOTS:
            tab.screenshot(path=f'{SHOTS}/in-flight-1440.png')
            week.locator('.fl-done-row').first.click()
            tab.screenshot(path=f'{SHOTS}/in-flight-earlier-open-1440.png')
        # A reload reads the same table back from the store.
        tab.reload()
        tab.wait_for_function('window.cosInFlight?.ready === true')
        expect(tab.locator('tbody [data-fl-row]')).to_have_count(2)
        assert tab.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors, errors
        browser.close()


def test_in_flight_on_a_phone_is_cards(tmp_path):
    writer = StoreWriter(tmp_path / 'store', 'dev')
    client = _app(tmp_path, writer)
    with sync_playwright() as p:
        browser, tab, errors = _open(p, client, 390, 844)
        _add(tab, 'Send the portfolio walkthrough to Thursday’s two')
        _add(tab, 'Reply to the outside issue before linking anything')
        cards = tab.locator('.fl-card')
        expect(cards).to_have_count(2)
        expect(tab.locator('.fl-table')).to_have_count(0)
        card = cards.filter(has_text='Reply to the outside')
        expect(card.locator('.fl-card-cm')).to_have_text('💬 Add a comment')
        expect(card.locator('.fl-card-kind')).to_have_text('Follow-up · you')
        card.locator('[data-fl-status]').select_option('doing')
        expect(card.locator('[data-fl-status]')).to_have_value('doing')
        assert tab.evaluate("getComputedStyle(document.querySelector('[data-conv-title-short]'), '::after').content") == '"In flight"'
        assert tab.evaluate('document.documentElement.scrollWidth <= innerWidth')
        if SHOTS:
            tab.screenshot(path=f'{SHOTS}/in-flight-390.png')
        card.click()
        panel = tab.locator('[data-fl-panel]')
        box = panel.bounding_box()
        assert box['x'] == 0 and box['width'] == pytest.approx(390, abs=1)
        expect(panel.locator('.fl-back')).to_be_visible()
        tab.locator('[data-fl-comment]').fill('After Robert’s call.')
        tab.locator('[data-fl-comment]').press('Enter')
        expect(panel.locator('[data-fl-thread]')).to_contain_text('After Robert’s call.')
        if SHOTS:
            tab.screenshot(path=f'{SHOTS}/in-flight-row-open-390.png')
        panel.locator('.fl-back').click()
        expect(panel).to_be_hidden()
        expect(card.locator('.fl-card-cm')).to_contain_text('After Robert’s call.')
        assert not errors, errors
        browser.close()


def test_in_flight_without_a_store_says_so(tmp_path):
    client = _app(tmp_path, None)
    with sync_playwright() as p:
        browser, tab, errors = _open(p, client, 1440)
        expect(tab.locator('[data-fl-state]')).to_have_text(NOT_CONFIGURED)
        expect(tab.locator('[data-fl-row]')).to_have_count(0)
        expect(tab.locator('[data-fl-new]')).to_have_count(0)
        expect(tab.locator('[data-fl-download]')).to_be_hidden()
        if SHOTS:
            tab.screenshot(path=f'{SHOTS}/in-flight-not-recorded-1440.png')
        assert not errors, errors
        browser.close()
