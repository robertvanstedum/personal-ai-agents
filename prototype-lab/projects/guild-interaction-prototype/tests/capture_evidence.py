"""Walkthrough screenshots (not a test). Writes PNGs for the eight acceptance
steps at 1280 px, phone frames at 390/360 px, and one live-mode bench.

    .venv/bin/python prototype-lab/projects/guild-interaction-prototype/tests/capture_evidence.py [OUT_DIR]
    .venv/bin/python prototype-lab/projects/guild-interaction-prototype/tests/capture_evidence.py OUT_DIR rev3
"""
from __future__ import annotations

import logging
import sys
import threading
import socket
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
logging.getLogger("werkzeug").setLevel(logging.ERROR)

from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

from app import create_app  # noqa: E402

# Default: <main checkout>/_working/guild-prototype-evidence, i.e. outside this
# worktree (<main>/_working/worktrees/guild-prototype). Pass OUT_DIR otherwise.
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT.parents[4] / "guild-prototype-evidence"


def serve(**kw):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = make_server("127.0.0.1", port, create_app(port=port, **kw), threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{port}"


def main():
    if not OUT.parent.is_dir():
        sys.exit(f"output parent {OUT.parent} does not exist; pass OUT_DIR explicitly")
    OUT.mkdir(exist_ok=True)
    srv, base = serve(sources="sample")
    live_srv, live = serve(sources="live", records_db="")
    shots = []
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context(viewport={"width": 1280, "height": 800})
        pg = ctx.new_page()

        def go(url):
            pg.goto(url); pg.wait_for_selector("body[data-ready=true]")

        def shot(name, full=False, page=None):
            path = OUT / name
            (page or pg).screenshot(path=str(path), full_page=full)
            shots.append(path)

        def say(text):
            pg.fill("[data-mc-input]", text); pg.press("[data-mc-input]", "Enter")

        go(base + "/guild"); shot("step1-arrive-1280.png")
        go(base + "/guild/build/bench"); shot("step1b-bench-focus-1280.png", full=True)
        pg.click('[data-panel="since"] [data-act="up"]'); pg.click('[data-panel="postits"] [data-act="fold"]')
        go(base + "/guild/operate"); go(base + "/guild/build/bench")
        shot("step2-bench-arranged-after-return-1280.png", full=True)
        go(base + "/guild/operate"); shot("step3-operate-evidence-1280.png", full=True)
        pg.click("[data-mc-pill]"); say("Why did usage stall here?"); pg.wait_for_timeout(200)
        shot("step4-ask-mc-operate-1280.png")
        pg.click('[data-section="build"]'); pg.wait_for_selector("body[data-ready=true]")
        shot("step4b-build-same-thread-1280.png")
        pg.click('[data-panel="subject"] a[data-queue-link]'); pg.wait_for_selector("body[data-ready=true]")
        shot("step5-queue-item-1280.png")
        pg.click("[data-back]"); pg.wait_for_selector("body[data-ready=true]")
        shot("step5b-back-to-bench-1280.png")
        pg.click("[data-mc-sample-attach='0']"); pg.wait_for_timeout(200)
        shot("step6-attach-for-discussion-1280.png")
        say("file this"); pg.wait_for_timeout(200); shot("step6b-file-this-proposal-1280.png")
        pg.locator(".msg-proposal").last.locator("[data-proposal-act=confirm]").click(); pg.wait_for_timeout(200)
        shot("step6c-file-receipt-thread-1280.png")
        go(base + "/guild/build/items/158"); shot("step6d-receipt-on-item-1280.png")
        go(base + "/guild/build/bench")
        say("Bring Claude and Codex in."); pg.wait_for_timeout(2000)
        say("Agreed. Grant Guest now, mobile fix as an Idea, coach Admin after."); pg.wait_for_timeout(200)
        shot("step7-invite-and-decision-proposal-1280.png")
        pg.locator(".msg-proposal").last.locator("[data-proposal-act=confirm]").click(); pg.wait_for_timeout(200)
        pg.click("[data-mc-min]")
        pg.click("[data-return-monday]"); pg.wait_for_timeout(200)
        shot("step7b-monday-return-1280.png", full=True)
        pg.click("[data-mc-pill]"); say("What did we decide about file this?"); pg.wait_for_timeout(200)
        shot("step7c-what-did-we-decide-1280.png")
        ctx.close()

        for w, h in ((390, 844), (360, 780)):
            c = b.new_context(viewport={"width": w, "height": h}, is_mobile=True, has_touch=True)
            ph = c.new_page()
            ph.goto(base + "/guild/build/bench"); ph.wait_for_selector("body[data-ready=true]")
            shot(f"step8-phone-arrive-{w}.png", page=ph)
            ph.click(".mc-hold"); ph.wait_for_timeout(200)
            shot(f"step8b-phone-hold-to-talk-{w}.png", page=ph)
            if w == 390:
                ph.click("[data-mc-type]"); ph.click("[data-mc-sample-attach='0']")
                ph.click(".mc-hold"); ph.click(".mc-hold"); ph.wait_for_timeout(200)
                shot("step8c-phone-file-this-proposal-390.png", page=ph, full=True)
                ph.goto(base + "/guild/operate"); ph.wait_for_selector("body[data-ready=true]")
                ph.click("[data-phone-open]")
                shot("step8d-phone-operate-open-390.png", page=ph, full=True)
            c.close()

        lc = b.new_context(viewport={"width": 1280, "height": 800})
        lp = lc.new_page()
        lp.goto(live + "/guild/build/bench"); lp.wait_for_selector("body[data-ready=true]")
        shot("live-bench-1280.png", full=True, page=lp)
        lp.goto(live + "/guild/build/queue"); lp.wait_for_selector("body[data-ready=true]")
        shot("live-queue-1280.png", page=lp)
        lc.close()
        b.close()
    srv.shutdown(); live_srv.shutdown()
    for s in shots:
        print(s)


def rev3(out: Path):
    """Revision 3 (Shop floor) evidence into OUT/rev3."""
    out.mkdir(exist_ok=True)
    srv, base = serve(sources="sample")
    shots = []
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context(viewport={"width": 1280, "height": 800})
        pg = ctx.new_page()

        def go(url, page=None):
            (page or pg).goto(url); (page or pg).wait_for_selector("body[data-ready=true]")

        def shot(name, page=None, full=False):
            (page or pg).screenshot(path=str(out / name), full_page=full); shots.append(out / name)

        def say(text):
            pg.fill("[data-mc-input]", text); pg.press("[data-mc-input]", "Enter"); pg.wait_for_timeout(150)

        go(base + "/guild/build"); shot("floor-sat-empty-1280.png")
        say("Why did usage stall here?")
        pg.click('[data-light="systems"] [data-ask]'); pg.wait_for_timeout(150)
        shot("floor-sat-1280.png")
        pg.click('[data-light="rollouts"] [data-light-link]'); pg.wait_for_selector("body[data-ready=true]")
        shot("light-detail-rollouts-operate-1280.png", full=True)
        go(base + "/guild/operate?tile=systems"); shot("light-detail-systems-operate-1280.png")
        go(base + "/guild/build")
        say("Bring Claude and Codex in."); pg.wait_for_timeout(1800)
        say("Agreed. Grant Guest now, mobile fix as an Idea, coach Admin after.")
        pg.locator(".msg-proposal").last.locator("[data-proposal-act=confirm]").click(); pg.wait_for_timeout(150)
        go(base + "/guild/build?clock=mon"); pg.wait_for_timeout(150)
        shot("floor-mon-1280.png")
        go(base + "/guild/build/bench"); shot("bench-after-tokens-1280.png", full=True)
        go(base + "/guild"); shot("landing-after-tokens-1280.png")
        ctx.close()
        for w, h in ((390, 844), (360, 780)):
            c = b.new_context(viewport={"width": w, "height": h}, is_mobile=True, has_touch=True)
            ph = c.new_page()
            go(base + "/guild/build", ph); shot(f"phone-floor-{w}.png", ph)
            ph.click(".mc-hold"); ph.wait_for_timeout(150); shot(f"phone-floor-voice-{w}.png", ph)
            if w == 390:
                ph.click("[data-lights-toggle]"); ph.wait_for_timeout(100); shot("phone-floor-lights-open-390.png", ph)
                ph.click("[data-lights-toggle]"); ph.click("[data-sheet-toggle]"); ph.wait_for_timeout(100)
                shot("phone-floor-sheet-open-390.png", ph)
            c.close()
        b.close()
    srv.shutdown()
    for s_ in shots:
        print(s_)


def usage(out: Path):
    """Revision 3.1 (Usage & limits) evidence into OUT/rev3 with a usage- prefix; changed surfaces only."""
    out.mkdir(exist_ok=True)
    srv, base = serve(sources="sample")
    shots = []
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context(viewport={"width": 1280, "height": 800})
        pg = ctx.new_page()

        def go(url, page=None):
            (page or pg).goto(url); (page or pg).wait_for_selector("body[data-ready=true]")

        def shot(name, page=None, full=False):
            (page or pg).screenshot(path=str(out / name), full_page=full); shots.append(out / name)

        go(base + "/guild/build"); shot("usage-floor-sat-1280.png")
        pg.click('[data-light="usage"]:not([hidden]) [data-ask]'); pg.wait_for_timeout(150)
        shot("usage-ask-proposal-1280.png")
        pg.locator(".msg-proposal").last.locator("[data-proposal-act=confirm]").click(); pg.wait_for_timeout(150)
        shot("usage-confirm-receipt-1280.png")
        go(base + "/guild/operate?tile=usage"); shot("usage-detail-sat-1280.png", full=True)
        go(base + "/guild/build")

        def say(text):
            pg.fill("[data-mc-input]", text); pg.press("[data-mc-input]", "Enter"); pg.wait_for_timeout(150)

        say("Claude Code says: you are approaching your usage limit. Resets Sat 11:00. (sample text)")
        say("file this"); shot("usage-capture-warning-proposal-1280.png")
        pg.locator(".msg-proposal").last.locator("[data-proposal-act=confirm]").click()
        pg.wait_for_timeout(1000); pg.wait_for_selector("body[data-ready=true]")
        say("Receipt from xAI (sample). Amount paid $30.00 on Sat 9:30. Paid with Mastercard - 1234.")
        say("file this"); shot("usage-capture-receipt-proposal-1280.png")
        pg.locator(".msg-proposal").last.locator("[data-proposal-act=confirm]").click()
        pg.wait_for_timeout(1000); pg.wait_for_selector("body[data-ready=true]")
        go(base + "/guild/operate?tile=usage"); shot("usage-detail-after-capture-1280.png", full=True)
        go(base + "/guild/build?clock=mon"); shot("usage-floor-mon-1280.png")
        go(base + "/guild/operate?tile=usage"); shot("usage-detail-mon-1280.png", full=True)
        ctx.close()
        c = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        ph = c.new_page()
        go(base + "/guild/build", ph); shot("usage-phone-floor-390.png", ph)
        ph.click("[data-lights-toggle]"); ph.wait_for_timeout(100); shot("usage-phone-lights-open-390.png", ph)
        go(base + "/guild/operate?tile=usage", ph); ph.click("[data-phone-open]"); ph.wait_for_timeout(100)
        shot("usage-phone-detail-390.png", ph, full=True)
        c.close()
        b.close()
    srv.shutdown()
    for s_ in shots:
        print(s_)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[2] == "usage":
        usage(OUT / "rev3")
    elif len(sys.argv) > 2 and sys.argv[2] == "rev3":
        rev3(OUT / "rev3")
    else:
        main()
