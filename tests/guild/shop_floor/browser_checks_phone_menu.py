"""Staging, 26 Nov: on a phone the ⋯ menu opened upward out of the dock and the dock's scroll box cut it off (Dictate, Voice
and Model hidden). Every row of the open menu must be the element at its own centre."""
import os

from browser_checks_b1 import browser, server, streaming_mc, floor, fresh_queue, board_media, go  # noqa: F401

def test_the_open_menu_is_not_cut_off_on_a_phone(browser, server, streaming_mc, monkeypatch):
    from test_mc_voice import _on
    _on(monkeypatch)
    monkeypatch.setenv("MINIMOI_DICTATION", "1")
    ctx = browser.new_context(viewport={"width": 390, "height": 664}, is_mobile=True, has_touch=True, device_scale_factor=3,
        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")
    page = ctx.new_page(); page.goto(f"{server['url']}/__b1_test_sign_in")
    go(page, f"{server['url']}/guild-next/guild/build")
    page.wait_for_timeout(1500)
    page.evaluate("() => { const d = document.querySelector('[data-dictate]'); if (d) d.hidden = false; }")   # as after /dictation/capabilities
    page.click("[data-mc-options] > summary")
    page.wait_for_timeout(800)
    hidden = page.evaluate("""() => [...document.querySelectorAll('.mc-options-body > .partner-menu > :not([hidden]), .mc-options-body > .partner-menu label')]
      .filter((e) => e.getClientRects().length).filter((e) => { const r = e.getBoundingClientRect();
        const hit = document.elementFromPoint(r.left + Math.min(20, r.width / 2), r.top + r.height / 2); return !(hit && e.contains(hit)); })
      .map((e) => e.className || e.tagName)""")
    assert hidden == [], hidden
    assert page.locator("[data-partner-voice]").is_enabled()
    # Dictate is the quiet round microphone, as on CoS (owner, 26 Nov), a phone-sized target with its word as its name.
    mic = page.evaluate("""() => { const b = document.querySelector('.mc-options-body [data-dictate]'); const r = b.getBoundingClientRect();
      const cs = getComputedStyle(b), icon = getComputedStyle(b, '::before');
      return { w: r.width, h: r.height, font: cs.fontSize, radius: cs.borderRadius, mask: icon.maskImage || icon.webkitMaskImage, name: b.textContent }; }""")
    assert mic["w"] == 44 and mic["h"] == 44 and mic["font"] == "0px" and mic["radius"] == "50%", mic
    assert "svg" in mic["mask"] and mic["name"] == "Dictate", mic            # no call live: the Voice can be changed
    if os.environ.get("PHONE_MENU_SHOT"):
        page.screenshot(path=os.environ["PHONE_MENU_SHOT"])
    ctx.close()
