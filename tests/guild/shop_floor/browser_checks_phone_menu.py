"""Staging, 26 Nov: on a phone the ⋯ menu opened upward out of the dock and the dock's scroll box cut it off (Dictate, Voice
and Model hidden). Every row of the open menu must be the element at its own centre."""
from browser_checks_b1 import browser, server, streaming_mc, floor, fresh_queue, board_media, go  # noqa: F401

def test_the_open_menu_is_not_cut_off_on_a_phone(browser, server, streaming_mc, monkeypatch):
    from test_mc_voice import _on
    _on(monkeypatch)
    ctx = browser.new_context(viewport={"width": 390, "height": 664}, is_mobile=True, has_touch=True, device_scale_factor=3,
        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")
    page = ctx.new_page(); page.goto(f"{server['url']}/__b1_test_sign_in")
    go(page, f"{server['url']}/guild-next/guild/build")
    page.wait_for_timeout(1500)
    page.click("[data-mc-options] > summary")
    page.wait_for_timeout(800)
    hidden = page.evaluate("""() => [...document.querySelectorAll('.mc-options-body > .partner-menu > :not([hidden]), .mc-options-body > .partner-menu label')]
      .filter((e) => e.getClientRects().length).filter((e) => { const r = e.getBoundingClientRect();
        const hit = document.elementFromPoint(r.left + Math.min(20, r.width / 2), r.top + r.height / 2); return !(hit && e.contains(hit)); })
      .map((e) => e.className || e.tagName)""")
    assert hidden == [], hidden
    assert page.locator("[data-partner-voice]").is_enabled()            # no call live: the Voice can be changed
    ctx.close()
