"""The Board's pop-out window, in a real browser against a real (SQLite) floor. Run explicitly:

    venv/bin/python3 -m pytest tests/guild/shop_floor/browser_checks_board_popout.py -q -p no:cacheprovider

Two windows of ONE signed-in browser profile (a page and its pop-out): add, edit, remove in either shows in the other;
the pop-out stays open while the main window navigates and reloads; notes persist across a reload of either window;
a stale token is refreshed once without applying a write twice; a blocked pop-up is stated with a tab fallback;
the editor keeps a person's text on a conflict. Not covered here: two browsers or devices, a browser restart,
Safari. Nothing leaves the machine."""
from __future__ import annotations

import pytest
from playwright.sync_api import expect

from browser_checks_b1 import (_context, _errors, _no_page_overflow, board_media, browser, floor, fresh_queue, go,  # noqa: F401
                               server)

BOARD = "/guild-next/guild/board"


def _texts(page):
    return page.locator("[data-bd-note] .bd-text").all_inner_texts()


def _add(page, text):
    page.click("[data-bd-new]")                                                       # a note you type straight into
    page.fill("[data-bd-new-field]", text)
    page.keyboard.press("Control+Enter")
    expect(page.locator("[data-bd-note] .bd-text", has_text=text)).to_have_count(1)       # drawn, not only saved


def _popout(ctx, page):
    with ctx.expect_page() as info:
        page.click("[data-bd-popout]")
    pop = info.value
    pop.wait_for_selector("body[data-ready=true], [data-bd-grid]")
    return pop


def test_the_board_in_two_windows_stays_one_board(browser, server, board_media):
    floor_, _media = board_media
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    go(page, f"{server['url']}{BOARD}")
    _add(page, "From the page")
    pop = _popout(ctx, page)
    pop_errors = _errors(pop)
    # The pop-out is the light page, with the same notes.
    expect(pop.locator("body")).to_have_attribute("data-page", "board_popout")
    assert pop.locator("[data-mc-pill]").count() == 1 and pop.locator("[data-bd-popout]").count() == 0     # a small MC head, and no pop-out button in the pop-out
    assert _texts(pop) == ["From the page"]
    # Added in the pop-out -> shown in the page without a reload (the channel hint, then the server read).
    _add(pop, "From the pop-out")
    expect(page.locator("[data-bd-note] .bd-text")).to_have_text(["From the pop-out", "From the page"])
    # Edited in the page -> shown in the pop-out; the server holds it.
    note = page.locator("[data-bd-note]", has_text="From the page")
    note.locator(".bd-edit > summary").click(); note.locator("[data-bd-edit-open]").click()
    page.locator("[data-bd-edit-field]").fill("From the page, edited")
    page.keyboard.press("Control+Enter")
    expect(pop.locator("[data-bd-note] .bd-text")).to_have_text(["From the pop-out", "From the page, edited"])
    assert [p["text"] for p in floor_.store().board().data["active"]] == ["From the pop-out", "From the page, edited"]
    # Discarded in the pop-out -> in the page's Trash count.
    pop.locator("[data-bd-note]", has_text="From the pop-out").locator(".bd-edit > summary").click()
    pop.locator("[data-bd-bin]").first.click()
    expect(page.locator('[data-bd-view="trash"]')).to_have_text("Bin 1")
    expect(pop.locator('[data-bd-view="trash"]')).to_have_text("Bin 1")
    assert not errors and not pop_errors, (errors, pop_errors)
    ctx.close()


def test_the_pop_out_stays_independent_while_the_main_window_moves_and_reloads(browser, server, board_media):
    floor_, _media = board_media
    ctx, page = _context(browser, server, 1440, 900)
    go(page, f"{server['url']}{BOARD}")
    _add(page, "Keep me")
    pop = _popout(ctx, page)
    go(page, f"{server['url']}/guild-next/guild/build")                  # the main window goes elsewhere
    assert not pop.is_closed()
    _add(pop, "Added while you were elsewhere")
    go(page, f"{server['url']}{BOARD}")                                  # and comes back: the note is there (durable)
    assert _texts(page) == ["Added while you were elsewhere", "Keep me"]
    page.reload(); page.wait_for_selector("body[data-ready=true]")       # a reload loses nothing and the pop-out is untouched
    assert _texts(page) == ["Added while you were elsewhere", "Keep me"] and not pop.is_closed()
    pop.reload(); pop.wait_for_selector("[data-bd-grid]")                # reloading the pop-out keeps the same Board
    assert _texts(pop) == ["Added while you were elsewhere", "Keep me"]
    # A second Pop out reuses the same window (the named target) instead of opening another.
    page.click("[data-bd-popout]"); page.wait_for_timeout(300)
    assert len([p for p in ctx.pages if "popout" in p.url]) == 1
    ctx.close()


def test_a_second_window_that_hears_nothing_still_catches_up_by_polling(browser, server, board_media):
    floor_, _media = board_media
    ctx, page = _context(browser, server, 1280, 800)
    ctx.add_init_script("delete window.BroadcastChannel;")                 # a browser without the channel: only the server read remains
    go(page, f"{server['url']}{BOARD}")
    _add(page, "First")
    pop = _popout(ctx, page)
    _add(pop, "Second")
    expect(page.locator("[data-bd-note] .bd-text")).to_have_text(["Second", "First"], timeout=20000)   # within one poll (15 s) or on focus
    ctx.close()


def test_a_note_being_typed_is_never_overwritten_and_a_conflict_keeps_their_words(browser, server, board_media):
    floor_, _media = board_media
    ctx, page = _context(browser, server, 1440, 900)
    go(page, f"{server['url']}{BOARD}")
    _add(page, "Shared note")
    pop = _popout(ctx, page)
    page.locator("[data-bd-note] .bd-edit > summary").click(); page.locator("[data-bd-note] [data-bd-edit-open]").click()
    page.locator("[data-bd-edit-field]").fill("My longer edit in the page")                       # typing in the page ...
    pop.locator("[data-bd-note] .bd-edit > summary").click(); pop.locator("[data-bd-note] [data-bd-edit-open]").click()
    pop.locator("[data-bd-edit-field]").fill("Their edit in the pop-out")
    pop.keyboard.press("Control+Enter")                                                            # ... and the other window saves first (no buttons in a Post-it)
    page.wait_for_timeout(500)
    assert page.locator("[data-bd-edit-field]").input_value() == "My longer edit in the page"     # not clobbered while typing
    page.keyboard.press("Control+Enter")                                                          # leaving it saves it: the version is stale
    expect(page.locator(".bd-conflict")).to_be_visible()                                          # stale version: conflict, nothing lost
    assert page.locator("[data-bd-edit-field]").input_value() == "My longer edit in the page"
    assert [p["text"] for p in floor_.store().board().data["active"]] == ["Their edit in the pop-out"]
    page.click("[data-bd-edit-save]")                                                             # the one place buttons appear: "Replace with mine", on purpose
    expect(page.locator("[data-bd-note] .bd-text")).to_have_text(["My longer edit in the page"])
    assert [p["text"] for p in floor_.store().board().data["active"]] == ["My longer edit in the page"]
    ctx.close()


def test_a_stale_token_is_refreshed_once_and_a_write_never_applies_twice(browser, server, board_media):
    floor_, _media = board_media
    ctx, page = _context(browser, server, 1440, 900)
    go(page, f"{server['url']}{BOARD}")
    seen = {"posts": 0}

    def refuse_once(route):
        if route.request.method == "POST" and route.request.url.endswith("/api/v1/postits") and seen["posts"] == 0:
            seen["posts"] += 1
            route.fulfill(status=403, content_type="application/json", body='{"error":"csrf","message":"stale"}')
        else:
            route.continue_()
    page.route("**/api/v1/postits", refuse_once)
    _add(page, "Written after a refused token")
    assert [p["text"] for p in floor_.store().board().data["active"]] == ["Written after a refused token"]   # exactly once
    ctx.close()


def test_a_blocked_popup_is_stated_with_a_tab_fallback_and_the_board_stays_usable(browser, server, board_media):
    ctx, page = _context(browser, server, 1440, 900)
    go(page, f"{server['url']}{BOARD}")
    page.evaluate("window.open = () => null")
    page.click("[data-bd-popout]")
    expect(page.locator("[data-bd-result]")).to_contain_text("blocked the pop-out")
    expect(page.locator("[data-bd-result] a[target=_blank]")).to_have_attribute("href", f"{BOARD}/popout")
    _add(page, "Still works")
    assert _texts(page) == ["Still works"]
    ctx.close()


@pytest.mark.parametrize("width", [320, 390, 768])
def test_the_pop_out_page_is_usable_at_phone_and_tablet_widths(browser, server, board_media, width):
    floor_, _media = board_media
    ctx, page = _context(browser, server, width, 800)
    go(page, f"{server['url']}{BOARD}/popout")
    _add(page, "On a small screen")
    assert _texts(page) == ["On a small screen"] and _no_page_overflow(page)
    ctx.close()


def test_a_new_note_is_typed_straight_into_the_board_and_an_empty_one_is_never_kept(browser, server, board_media):
    floor_, _media = board_media
    ctx, page = _context(browser, server, 1440, 900)
    go(page, f"{server['url']}{BOARD}")
    page.click("[data-bd-new]")
    expect(page.locator("[data-bd-new-field]")).to_be_focused()                            # nothing to open first: type
    page.keyboard.press("Escape")
    assert page.locator("[data-bd-new-card]").count() == 0 and floor_.store().board().data["counts"]["active"] == 0
    page.click("[data-bd-new]"); page.locator("[data-bd-new-field]").blur()                # leaving it empty keeps nothing
    expect(page.locator("[data-bd-new-card]")).to_have_count(0)
    assert floor_.store().board().data["counts"]["active"] == 0
    page.click("[data-bd-new]"); page.fill("[data-bd-new-field]", "Saved by leaving it")
    page.locator('[data-bd-view="active"]').focus()                                                  # leaving a typed note saves it
    expect(page.locator("[data-bd-note] .bd-text")).to_have_text(["Saved by leaving it"])
    assert [p["text"] for p in floor_.store().board().data["active"]] == ["Saved by leaving it"]
    ctx.close()


def _fail_next_board_reads(server, n):
    """Make the next n server reads of the Board fail the way a stalled database does (the real store is otherwise untouched)."""
    from minimoi_portal.guild_ui.adapters.contract import READ_FAILED, live_unknown
    services = server["app"].extensions["guild_ui_next"]["services"]
    real = services.floor.board
    left = {"n": n}

    def board():
        if left["n"] > 0:
            left["n"] -= 1
            return live_unknown(READ_FAILED, "floor store read failed: OperationalError", "test")
        return real()
    services.floor.board = board
    return lambda: setattr(services.floor, "board", real)


def test_a_main_board_drawn_while_the_store_stalls_recovers_by_itself_and_keeps_the_notes(browser, server, board_media):
    """Robert, 10 Oct (Dev): the main Board said "unknown: it could not be read" while the pop-out showed his notes, and it stayed that way. The first read
    failed; the page then believed it was current and never looked again. Now it says it is trying, retries, and the stored notes appear without a reload."""
    floor_, _media = board_media
    ctx, page = _context(browser, server, 1440, 900)
    go(page, f"{server['url']}{BOARD}")
    _add(page, "Stored before the stall")
    _add(page, "A second stored note")
    restore = _fail_next_board_reads(server, 2)                       # the page render and the first retry both stall
    try:
        go(page, f"{server['url']}{BOARD}")
        expect(page.locator("[data-bd-unknown]")).to_be_visible()                    # honest: unknown, not empty, and says it is trying again
        expect(page.locator("[data-bd-unknown]")).to_contain_text("trying again")
        assert page.locator("[data-bd-note]").count() == 0
        expect(page.locator("[data-bd-note] .bd-text")).to_have_text(["A second stored note", "Stored before the stall"], timeout=15000)
        expect(page.locator("[data-bd-unknown]")).to_be_hidden()
    finally:
        restore()
    assert [p["text"] for p in floor_.store().board().data["active"]] == ["A second stored note", "Stored before the stall"]   # nothing was lost
    ctx.close()


def test_a_refresh_that_fails_keeps_the_notes_on_screen_and_recovers(browser, server, board_media):
    floor_, _media = board_media
    ctx, page = _context(browser, server, 1440, 900)
    go(page, f"{server['url']}{BOARD}")
    _add(page, "Visible note")
    pop = _popout(ctx, page)
    state = {"left": 1}

    def stall(route):                                                  # this window's next Board read fails once, the way a stalled database answers
        if route.request.method == "GET" and state["left"] > 0:
            state["left"] -= 1
            route.fulfill(status=503, content_type="application/json", body='{"status": "unknown", "reason": "read_failed", "message": "unreadable"}')
        else:
            route.continue_()
    page.route("**/api/v1/board", stall)
    _add(pop, "Added in the pop-out")                                  # the main window is told; its refresh stalls once
    expect(page.locator("[data-bd-sync]")).to_contain_text("showing what was last loaded", timeout=10000)
    expect(page.locator("[data-bd-note] .bd-text", has_text="Visible note")).to_have_count(1)       # the notes it had are not wiped
    assert page.locator("[data-bd-unknown]").is_hidden()
    expect(page.locator("[data-bd-note] .bd-text")).to_have_text(["Added in the pop-out", "Visible note"], timeout=20000)   # and the next beat catches up
    ctx.close()


from browser_checks_b1 import _wt_mc  # noqa: E402  (a stand-in Master Craftsman runtime that records what it is sent)

POPOUT = "/guild-next/guild/board/popout"


def test_the_board_is_quiet_small_plus_discreet_icons_no_buttons_inside_a_note(browser, server, board_media):
    ctx, page = _context(browser, server, 1440, 900)
    go(page, f"{server['url']}{BOARD}")
    _add(page, "A quiet note")
    for sel in ("[data-bd-new]", "[data-bd-popout]"):
        bb = page.locator(sel).bounding_box()
        assert bb["width"] <= 36 and bb["height"] <= 36, (sel, bb)                                # a small ＋ and a discreet pop-out icon, not big buttons
    expect(page.locator("[data-bd-new]")).to_have_attribute("aria-label", "New note")
    expect(page.locator("[data-bd-popout]")).to_have_attribute("aria-label", "Pop out the Board in its own window")
    visible_buttons = page.evaluate("() => [...document.querySelectorAll('[data-bd-note] .bd-btn, [data-bd-note] button')].filter((b) => b.offsetParent && !b.closest('.bd-edit-body') && !b.matches('[data-bd-resize]') && !b.hasAttribute('hidden')).map((b) => b.textContent.trim())")
    expect(page.locator("[data-bd-resize]")).to_have_count(1)
    assert page.locator("[data-bd-resize]").bounding_box()["width"] >= 24
    assert visible_buttons == [], visible_buttons                                                   # nothing inside the Post-it but the text and one ⋯
    note = page.locator("[data-bd-note]").first
    note.locator(".bd-edit > summary").click()
    for sel in ("[data-bd-edit-open]", "[data-bd-bin]"):                                             # 11 Oct: only Edit and Discard
        expect(note.locator(sel)).to_be_visible()
    expect(note.locator("[data-bd-done], [data-bd-move], [data-bd-save-label], [data-bd-save-link]")).to_have_count(0)
    page.keyboard.press("Escape")
    expect(note.locator("[data-bd-bin]")).to_be_hidden()
    note.locator(".bd-text").click()                                                               # clicking the text edits it in place
    expect(page.locator("[data-bd-edit-field]")).to_be_visible()
    ctx.close()


def test_an_empty_board_shows_a_labelled_example_that_is_never_a_note_and_never_hides_an_unreadable_board(browser, server, board_media):
    floor_, _media = board_media
    ctx, page = _context(browser, server, 1440, 900)
    go(page, f"{server['url']}{BOARD}")
    example = page.locator("[data-bd-example]")
    expect(example).to_be_visible()
    expect(example).to_contain_text("Example · not a saved note")
    assert page.locator("[data-bd-note]").count() == 0 and floor_.store().board().data["active"] == []        # it is not stored and not a note
    _add(page, "A real note")
    expect(example).to_have_count(0)
    # an unreadable Board is "unknown", never the cheerful example
    restore = _fail_next_board_reads(server, 4)
    try:
        go(page, f"{server['url']}{BOARD}")
        expect(page.locator("[data-bd-unknown]")).to_be_visible()
        assert page.locator("[data-bd-example]").count() == 0
    finally:
        restore()
    ctx.close()


def test_the_notepad_launched_on_its_own_takes_a_paste_and_the_note_is_there_when_the_main_board_opens(browser, server, board_media):
    floor_, _media = board_media
    ctx, page = _context(browser, server, 440, 720)                                                  # a small stand-alone window, no main Guild window at all
    go(page, f"{server['url']}{POPOUT}")
    page.wait_for_selector("[data-bd-grid]")
    assert page.locator("[data-bd-popout]").count() == 0 and page.locator(".gu-section-band").count() == 0
    page.click("[data-bd-new]")
    field = page.locator("[data-bd-new-field]")
    pasted = "Call back about the quote\nhttps://example.com/quote?id=42\nask for the 12% option"     # multi-line text and a link, as one paste
    field.focus()
    page.keyboard.insert_text(pasted)                                                               # a real paste of text (no clipboard permission needed)
    expect(field).to_have_value(pasted)
    page.keyboard.press("Control+Enter")
    expect(page.locator("[data-bd-note] .bd-text", has_text="Call back about the quote")).to_have_count(1)
    expect(page.locator("[data-bd-sync]")).to_contain_text("Up to date")                             # clear save feedback
    saved = floor_.store().board().data["active"][0]["text"]
    assert saved == pasted                                                                          # multi-line kept as typed, the link not fetched or changed
    ctx2 = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.close()                                                                                     # the notepad window goes away; the notes stay
    ctx3, main = _context(browser, server, 1440, 900)
    go(main, f"{server['url']}{BOARD}")
    expect(main.locator("[data-bd-note] .bd-text")).to_have_text([pasted])                           # visible when the main Board is opened later
    ctx2.close(); ctx3.close()


def test_a_paste_longer_than_a_note_says_it_was_cut(browser, server, board_media):
    ctx, page = _context(browser, server, 1440, 900)
    go(page, f"{server['url']}{BOARD}")
    page.click("[data-bd-new]")
    page.evaluate("""() => { const f = document.querySelector('[data-bd-new-field]'); f.focus();
      const dt = new DataTransfer(); dt.setData('text', 'x'.repeat(400)); f.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true})); }""")
    expect(page.locator("[data-bd-result]")).to_contain_text("longer than a note holds")
    ctx.close()


def test_the_mc_head_in_the_notepad_chats_on_its_own_and_keeps_notes_and_drafts(browser, server, board_media, floor):
    services, saved, sent = _wt_mc(server)
    try:
        ctx, page = _context(browser, server, 440, 720)
        go(page, f"{server['url']}{POPOUT}")
        page.wait_for_selector("[data-bd-grid]")
        _add(page, "A note beside the chat")
        head = page.locator("[data-mc-pill]")
        bb = head.bounding_box()
        assert bb["width"] <= 48 and bb["height"] <= 48, bb                                         # a small head, not a bar
        head.click()
        assert page.locator("body").get_attribute("data-mc-mode") == "floating"
        sheet = page.locator("[data-mc-panel]").bounding_box()
        assert sheet["x"] >= 0 and sheet["x"] + sheet["width"] <= 441 and sheet["y"] + sheet["height"] <= 721, sheet        # usable in a small window
        page.fill("[data-mc-input]", "half a thought")
        page.locator("[data-mc-min]").click()                                                       # fold it away: notes and draft are untouched
        expect(page.locator("[data-bd-note] .bd-text")).to_have_text(["A note beside the chat"])
        head.click()
        expect(page.locator("[data-mc-input]")).to_have_value("half a thought")
        assert sent == []                                                                          # nothing was sent, and no note went to MC by itself
        page.fill("[data-mc-input]", "What is on my Board?")
        page.click("[data-mc-send]")
        expect(page.locator("[data-mc-thread]")).to_contain_text("Noted.", timeout=15000)           # a real turn through the real backend wiring, from the notepad alone
        assert len(sent) == 1 and "What is on my Board?" in sent[0] and "A note beside the chat" not in sent[0]
        ctx.close()
    finally:
        services.mc, services.mc_health, services.mc_turns = saved


@pytest.mark.parametrize("width,height", [(390, 844), (320, 700)])
def test_on_a_phone_the_board_is_the_page_with_touch_targets_and_mc_one_tap_away(browser, server, board_media, floor, width, height):
    services, saved, sent = _wt_mc(server)
    try:
        ctx = browser.new_context(viewport={"width": width, "height": height}, is_mobile=True, has_touch=True)
        page = ctx.new_page(); page.goto(f"{server['url']}/__b1_test_sign_in")
        errors = _errors(page)
        go(page, f"{server['url']}{POPOUT}")
        page.wait_for_selector("[data-bd-grid]")
        assert _no_page_overflow(page)
        for sel in ("[data-bd-new]", "[data-mc-pill]"):
            bb = page.locator(sel).bounding_box(); assert bb["width"] >= 36 and bb["height"] >= 36, (sel, bb)       # touch-sized, still small
        page.tap("[data-bd-new]")
        field = page.locator("[data-bd-new-field]")
        field.focus(); page.keyboard.insert_text("Pasted on my phone\nhttps://example.com/x")
        page.keyboard.press("Control+Enter")
        expect(page.locator("[data-bd-note] .bd-text")).to_have_count(1)
        page.tap("[data-mc-pill]")
        sheet = page.locator("[data-mc-panel]").bounding_box()
        assert sheet["x"] >= -1 and sheet["x"] + sheet["width"] <= width + 1 and sheet["y"] + sheet["height"] <= height + 1, sheet     # a bottom sheet inside the screen
        expect(page.locator("[data-mc-input]")).to_be_visible()
        assert page.locator("[data-mc-input]").bounding_box()["y"] + 20 <= height                                       # the composer is reachable, not under the keyboard line
        page.fill("[data-mc-input]", "phone draft")
        page.locator("[data-mc-min]").click()
        expect(page.locator("[data-bd-note] .bd-text")).to_have_count(1)
        page.tap("[data-mc-pill]")
        expect(page.locator("[data-mc-input]")).to_have_value("phone draft")                                            # the draft survives going back to the notes
        assert _no_page_overflow(page)
        assert not [e for e in errors if "401" not in e], errors
        ctx.close()
    finally:
        services.mc, services.mc_health, services.mc_turns = saved


def test_plus_always_makes_a_new_note_even_while_another_is_being_edited_and_notes_save_when_you_leave_them(browser, server, board_media):
    """Robert, 10 Oct (Dev): with a note left open for editing, ＋ did nothing, before and after the pop-out was closed. Now ＋ finishes the open note (saving it) and
    starts a new one; a note has no Save, Cancel or instructions: leaving it saves it, Esc cancels, an empty one is never kept."""
    floor_, _media = board_media
    ctx, page = _context(browser, server, 1440, 900)
    go(page, f"{server['url']}{BOARD}")
    _add(page, "First note")
    page.locator("[data-bd-note] .bd-text").first.click()                                           # click the text: edit in place
    field = page.locator("[data-bd-edit-field]")
    expect(field).to_be_visible()
    assert page.locator("[data-bd-note] .bd-btn:visible, [data-bd-note] .bd-edit-hint").count() == 0   # no buttons, no instructions inside the Post-it
    field.fill("First note, edited")
    page.click("[data-bd-new]")                                                                      # ＋ while editing
    new = page.locator("[data-bd-new-field]")
    expect(new).to_be_visible()
    new.fill("Second note\nwith a second line")
    page.locator("h1.page-title").click()                                                            # leaving it saves it
    expect(page.locator("[data-bd-note] .bd-text")).to_have_count(2)
    texts = [p["text"] for p in floor_.store().board().data["active"]]
    assert sorted(texts) == ["First note, edited", "Second note\nwith a second line"], texts
    # Esc cancels an edit; an empty new note is never kept
    page.locator("[data-bd-note] .bd-text", has_text="First note, edited").click()
    page.locator("[data-bd-edit-field]").fill("changed my mind")
    page.keyboard.press("Escape")
    expect(page.locator("[data-bd-note] .bd-text", has_text="First note, edited")).to_have_count(1)
    page.click("[data-bd-new]"); page.locator("h1.page-title").click()
    expect(page.locator("[data-bd-new-field]")).to_have_count(0)
    assert len(floor_.store().board().data["active"]) == 2
    # sizes follow what is written and nothing grows to the size of the screen
    sizes = page.evaluate("() => [...document.querySelectorAll('[data-bd-note]')].map((n) => [n.dataset.size, Math.round(n.getBoundingClientRect().width)])")
    assert all(w <= 345 for _s, w in sizes), sizes
    assert page.locator(".bd-cork").evaluate("e => e.getBoundingClientRect().height") >= 500          # the board itself is big
    ctx.close()


def test_a_slow_save_never_clears_or_overwrites_a_newer_note_and_a_failed_save_keeps_the_words(browser, server, board_media):
    """Codex review P1 (10 Oct): the blur and the click that caused it must share one save; a save that finishes late must never clear a newer draft; a failed save keeps
    the right words. Responses are held and released by hand so the order is exactly the bad one."""
    floor_, _media = board_media
    ctx, page = _context(browser, server, 1440, 900)
    go(page, f"{server['url']}{BOARD}")
    _add(page, "Note A"); _add(page, "Note B")                                                         # B is first on the Board, A second
    held, seen = [], []

    def hold(route):
        if route.request.method == "POST":
            seen.append(route.request.post_data or "")
            held.append(route)
        else:
            route.continue_()
    page.route("**/api/v1/postits/*/text", hold)
    # edit A, change it, then click B: the blur save and the click share ONE write
    page.locator("[data-bd-note] .bd-text", has_text="Note A").click()
    page.locator("[data-bd-edit-field]").fill("Note A, changed")
    page.locator("[data-bd-note] .bd-text", has_text="Note B").click()
    page.wait_for_timeout(600)
    assert len(seen) == 1, seen                                                                          # one write for A, not two
    held.pop(0).continue_()                                                                              # the slow answer arrives now
    page.wait_for_function("() => document.querySelector('[data-bd-edit-field]') && document.querySelector('[data-bd-edit-field]').value === 'Note B'")
    field = page.locator("[data-bd-edit-field]")
    field.fill("Note B, being typed")                                                                    # B is the newer draft
    page.wait_for_timeout(300)
    assert field.input_value() == "Note B, being typed"                                                  # nothing from A's finished save touched it
    assert [p["text"] for p in floor_.store().board().data["active"]] == ["Note B", "Note A, changed"]
    # a FAILED save keeps A's words and does not open B over them
    page.keyboard.press("Escape")
    page.unroute("**/api/v1/postits/*/text")
    page.route("**/api/v1/postits/*/text", lambda route: route.fulfill(status=503, content_type="application/json", body='{"error": "unavailable", "message": "The server could not save that."}') if route.request.method == "POST" else route.continue_())
    page.locator("[data-bd-note] .bd-text", has_text="Note A, changed").click()
    page.locator("[data-bd-edit-field]").fill("Note A, third version")
    page.locator("[data-bd-note] .bd-text", has_text="Note B").click()
    page.wait_for_timeout(800)
    assert page.locator("[data-bd-edit-field]").input_value() == "Note A, third version"                # the failed save kept their words, in the same editor
    assert [p["text"] for p in floor_.store().board().data["active"]] == ["Note B", "Note A, changed"]  # and the stored note is untouched
    ctx.close()


def test_the_keyboard_save_shares_the_one_save_and_words_typed_while_it_is_pending_are_kept(browser, server, board_media):
    """Codex residual (10 Oct): Ctrl/⌘+Enter went straight to the save and could run beside the blur save; and words typed in the same editor while a save is pending
    must not be discarded when it finishes."""
    floor_, _media = board_media
    ctx, page = _context(browser, server, 1440, 900)
    go(page, f"{server['url']}{BOARD}")
    _add(page, "Keyboard note")
    held, seen = [], []

    def hold(route):
        if route.request.method == "POST":
            seen.append(route.request.post_data or "")
            held.append(route)
        else:
            route.continue_()
    page.route("**/api/v1/postits/*/text", hold)
    page.locator("[data-bd-note] .bd-text").first.click()
    field = page.locator("[data-bd-edit-field]")
    field.fill("Keyboard note, saved by keyboard")
    page.keyboard.press("Control+Enter"); page.keyboard.press("Control+Enter")                     # twice, and then the blur: one write
    page.locator("h1.page-title").click()
    page.wait_for_timeout(600)
    assert len(seen) == 1, seen
    field.fill("Keyboard note, saved by keyboard, and more typed while it was away")               # typing continues in the same editor
    held.pop(0).continue_()
    page.wait_for_timeout(600)
    assert page.locator("[data-bd-edit-field]").count() == 1                                          # the editor stayed open
    assert page.locator("[data-bd-edit-field]").input_value() == "Keyboard note, saved by keyboard, and more typed while it was away"
    assert [p["text"] for p in floor_.store().board().data["active"]] == ["Keyboard note, saved by keyboard"]
    ctx.close()
