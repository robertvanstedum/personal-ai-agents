"""MC's chat list (Guild → Craft) after Robert's production test, 12 Nov 2026, in a real browser with synthetic local
records: older chats carry a date, one "Show older chats" brings the next ones in place, "Back to top" returns, empty
"New chat" rows are not listed, and starting a new chat and typing still works. Phone and desktop, light and dark.

    venv/bin/python3 -m pytest tests/guild/shop_floor/browser_checks_chat_list.py -q -p no:cacheprovider
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone

import pytest
from playwright.sync_api import expect
from browser_checks_b1 import browser, server, fresh_queue, floor, go, _wt, _no_page_overflow  # noqa:F401

CLOCK = re.compile(r"^\d{1,2}:\d{2}\s(AM|PM)$")


def _restamp(server, cid, when):
    """Move a chat's last activity (the list's order and its label) to ``when``."""
    folder = os.path.join(server["app"].extensions["guild_ui_next"]["services"].store.folder, "conversations")
    path = os.path.join(folder, f"{cid}.json")
    with open(path) as f:
        conv = json.load(f)
    conv["updated_at"] = when.isoformat()
    with open(path, "w") as f:
        json.dump(conv, f)


def _open_list(page):
    history = page.locator("[data-floor-history]")
    if not history.is_visible():
        page.locator("[data-history-toggle]:visible, .mm-chat-rail [data-sidebar-toggle]:visible").first.click()
    expect(history).to_be_visible()
    return history


@pytest.mark.parametrize("width,height,scheme", [(390, 844, "light"), (390, 844, "dark"), (1440, 900, "light"), (1440, 900, "dark")])
def test_craft_list_dates_show_older_and_no_empty_chats(browser, server, floor, width, height, scheme):
    ctx = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    page = ctx.new_page()
    page.goto(f"{server['url']}/__b1_test_sign_in")
    go(page, f"{server['url']}/guild-next/guild/build")
    now = datetime.now(timezone.utc)
    made = []
    for index in range(14):
        cid = _wt(page, "/conversations", {})["conversation"]["id"]
        assert _wt(page, "/notes", {"text": f"List check {scheme} {width} {index}", "conversation_id": cid})["result"] == "kept"
        made.append(cid)
    for offset, cid in enumerate(made[:12]):                         # twelve older chats under the two newest
        _restamp(server, cid, now - timedelta(days=2, minutes=offset))
    _restamp(server, made[0], now - timedelta(days=1, hours=1))      # yesterday or the day before, by Chicago's clock
    _restamp(server, made[1], datetime(2025, 6, 3, 15, 0, tzinfo=timezone.utc))
    empties = [_wt(page, "/conversations", {})["conversation"]["id"] for _ in range(3)]
    newest = made[-1]
    go(page, f"{server['url']}/guild-next/guild/build?c={newest}")
    history = _open_list(page)
    rows = history.locator("[data-conv-list] [data-conv]")
    expect(rows).to_have_count(10)
    for cid in empties:
        expect(history.locator(f'[data-conv="{cid}"]')).to_have_count(0)        # no empty "New chat" rows
    assert CLOCK.match(history.locator(f'[data-conv="{newest}"] [data-chat-when]').inner_text().strip())
    expect(history.get_by_role("link", name=re.compile("^Newer$|^Older$"))).to_have_count(0)
    older, top = history.locator("[data-conv-older]"), history.locator("[data-conv-top]")
    expect(older).to_have_text("Show older chats")
    expect(top).to_be_hidden()
    expect(history.locator("[data-conv-view=archived]")).to_have_text("Archived chats")
    older.click()
    expect(top).to_be_visible()
    expect(rows.nth(10)).to_be_attached()                            # the next ones joined below, in place
    while older.is_visible() and not history.locator(f'[data-conv="{made[1]}"]').count():   # earlier runs' chats sit above
        older.click()
        page.wait_for_timeout(150)
    expect(history.locator(f'[data-conv="{made[1]}"]')).to_be_attached()
    expect(history.locator(f'[data-conv="{made[1]}"] [data-chat-when]')).to_have_text("Tue 3 Jun 2025")
    label = history.locator(f'[data-conv="{made[0]}"] [data-chat-when]').inner_text().strip()
    assert label == "Yesterday" or re.match(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d{1,2} [A-Z][a-z]{2}$", label), label
    assert re.match(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d{1,2} [A-Z][a-z]{2} \d{4}, \d{1,2}:\d{2} (AM|PM)$",
                    history.locator(f'[data-conv="{made[1]}"] [data-chat-when]').get_attribute("title"))
    expect(top).to_be_visible()
    added = history.locator(f'[data-conv="{made[1]}"]')
    added.locator("[data-conv-menu]").click()                        # a row brought in later has its actions too
    expect(added.locator("[data-conv-act=rename]")).to_be_visible()
    added.locator("[data-conv-menu]").click()                        # closes it (Escape would also close a phone's drawer)
    expect(added.locator("[data-conv-act=rename]")).to_be_hidden()
    while older.is_visible():
        older.click()
        page.wait_for_timeout(150)
    ids = rows.evaluate_all("rs => rs.map(r => r.dataset.conv)")
    assert len(ids) == len(set(ids)) and set(made) <= set(ids) and not set(empties) & set(ids)
    top.click()
    expect(history.locator("[data-conv-link]").first).to_be_focused()
    assert _no_page_overflow(page)
    page.screenshot(path=f"/private/tmp/mc-chat-list-{width}-{scheme}.png")
    ctx.close()


def test_new_chat_then_typing_keeps_it_in_the_list(browser, server, floor):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    page.goto(f"{server['url']}/__b1_test_sign_in")
    go(page, f"{server['url']}/guild-next/guild/build")
    history = _open_list(page)
    history.locator("[data-conv-new]").click()
    page.wait_for_url(re.compile(r"[?&]c=c-[0-9a-f]{12}"))
    page.wait_for_selector("body[data-ready=true]")
    cid = re.search(r"c=(c-[0-9a-f]{12})", page.url)[1]
    expect(page.locator(f'[data-conv-list] [data-conv="{cid}"]')).to_have_attribute("aria-current", "true")   # open: listed
    page.fill("#mc-input", "First words in a brand new chat")
    page.click("[data-mc-send]")
    expect(page.locator('[data-mc-thread] [data-kind="note"]').last).to_contain_text("First words in a brand new chat")
    go(page, f"{server['url']}/guild-next/guild/build?c=shop-floor-thread")           # away from it: still listed, titled
    expect(page.locator(f'[data-conv-list] [data-conv="{cid}"] [data-conv-title]')).to_contain_text("First words in a brand new chat")
    ctx.close()
