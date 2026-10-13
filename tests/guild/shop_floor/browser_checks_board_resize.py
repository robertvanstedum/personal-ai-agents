"""Board resize acceptance against local SQLite; no external calls or live owner data."""
import re
import uuid
import pytest
from playwright.sync_api import expect
from browser_checks_b1 import browser, server, fresh_queue, floor, board_media, _context, go, _no_page_overflow  # noqa:F401
from browser_checks_board_popout import _add, _popout, BOARD


def test_pointer_resize_reload_popout_and_phone_preserve_geometry(browser, server, board_media):
    floor,_=board_media
    ctx,page=_context(browser,server,1440,900)
    go(page,server['url']+BOARD)
    _add(page,'Resize me')
    handle=page.locator('[data-bd-resize]')
    expect(handle).to_be_visible()
    box=handle.bounding_box()
    page.mouse.move(box['x']+12,box['y']+12);page.mouse.down()
    page.mouse.move(box['x']+150,box['y']+95,steps=12);page.mouse.up()
    expect(page.locator('[data-bd-note]')).to_have_attribute('data-resized','true')
    saved=floor.store().board().data['active'][0]
    assert saved['size_w']>180 and saved['size_h']>170
    page.reload();expect(page.locator('[data-bd-note]')).to_have_attribute('data-resized','true')
    pop=_popout(ctx,page)
    expect(pop.locator('[data-bd-note]')).to_have_attribute('data-resized','true')
    handle=page.locator('[data-bd-resize]');handle.focus();page.keyboard.press('ArrowRight')
    expect(page.locator('[data-bd-note]')).to_have_css('width',f"{saved['size_w']+20}px")
    pop.reload();expect(pop.locator('[data-bd-note]')).to_have_attribute('style',re.compile(f"--card-w: {saved['size_w']+20}px"))
    page.set_viewport_size({'width':768,'height':800});_no_page_overflow(page)
    page.set_viewport_size({'width':390,'height':780});_no_page_overflow(page)
    assert floor.store().board().data['active'][0]['size_w']==saved['size_w']+20
    page.screenshot(path='/private/tmp/board-resize-phone.png')
    page.set_viewport_size({'width':1440,'height':900});expect(page.locator('[data-bd-resize]')).to_be_visible()
    page.screenshot(path='/private/tmp/board-resize-desktop.png')
    ctx.close()


def test_resize_escape_cancels_and_growth_stops_at_neighbour(browser,server,board_media):
    floor,_=board_media
    ctx,page=_context(browser,server,1440,900);go(page,server['url']+BOARD)
    _add(page,'First');_add(page,'Second')
    handle=page.locator('[data-bd-resize]').first;b=handle.bounding_box()
    page.mouse.move(b['x']+12,b['y']+12);page.mouse.down();page.mouse.move(b['x']+90,b['y']+80)
    page.keyboard.press('Escape');page.mouse.up()
    assert all(p['size_w'] is None for p in floor.store().board().data['active'])
    handle.focus();page.keyboard.press('ArrowRight')
    expect(page.locator('[data-bd-note]').first).to_have_attribute('data-resized','true')
    before=floor.store().board().data['active'][0]['size_w']
    page.locator('[data-bd-resize]').first.focus();page.keyboard.press('ArrowRight')
    assert floor.store().board().data['active'][0]['size_w']==before
    ctx.close()


@pytest.mark.parametrize('shape',[(900,100),(100,900)])
def test_photos_stay_complete_at_minimum_and_maximum_card_sizes(browser,server,board_media,tmp_path,shape):
    from board_media_helpers import image_bytes
    ctx,page=_context(browser,server,1440,900);go(page,server['url']+BOARD)
    picture=tmp_path/'aspect.jpg';picture.write_bytes(image_bytes(size=shape))
    page.click('[data-bd-open]');page.set_input_files('[data-bd-photo-file]',str(picture))
    expect(page.locator('[data-bd-photo-add]')).to_be_enabled();page.click('[data-bd-photo-add]')
    expect(page.locator('.bd-photo')).to_be_visible()
    for w,h in [(140,130),(480,450)]:
        # Exercise the real server transaction, then inspect the responsive rendering.
        card=board_media[0].store().board().data['active'][0]
        from browser_checks_b1 import _wt
        answer=_wt(page,'/postits/layout',{'moves':[{'id':card['id'],'x':10,'y':10,'w':w,'h':h}], 'expect_order_rev':board_media[0].store().board().data['order_rev'], 'idempotency_key':uuid.uuid4().hex})
        assert answer['result']=='moved'
        page.reload();img=page.locator('.bd-photo img');expect(img).to_be_visible();expect(img).to_have_css('object-fit','contain')
        expect(page.locator('[data-bd-resize]')).to_be_visible()
        expect(img).not_to_have_css('width','0px')
        expect(img).not_to_have_css('height','0px')
        page.screenshot(path=f'/private/tmp/board-resize-photo-{shape[0]}-{w}.png')
        dimensions=img.evaluate('(i)=>[i.naturalWidth,i.naturalHeight,i.clientWidth,i.clientHeight]')
        assert dimensions[0]/dimensions[1]==pytest.approx(shape[0]/shape[1],rel=.02) and dimensions[2]>0 and dimensions[3]>0
    page.screenshot(path=f'/private/tmp/board-resize-photo-{shape[0]}.png')
    ctx.close()



def test_lost_capture_cancels_resize_and_allows_next_change(browser,server,board_media):
    ctx,page=_context(browser,server,1440,900);go(page,server['url']+BOARD);_add(page,'Capture')
    b=page.locator('[data-bd-resize]').bounding_box()
    page.mouse.move(b['x']+12,b['y']+12);page.mouse.down();page.mouse.move(b['x']+100,b['y']+80)
    page.locator('[data-bd-note]').dispatch_event('lostpointercapture',{'pointerId':1})
    page.mouse.up()
    assert board_media[0].store().board().data['active'][0]['size_w'] is None
    page.locator('[data-bd-resize]').focus();page.keyboard.press('Shift+ArrowRight')
    expect(page.locator('[data-bd-note]')).to_have_css('width','280px')
    ctx.close()


def test_grabbing_the_picture_moves_the_card_without_native_image_drag(browser, server, board_media, tmp_path):
    from board_media_helpers import image_bytes
    ctx, page = _context(browser, server, 1440, 900)
    go(page, server['url'] + BOARD)
    picture = tmp_path / 'move-picture.jpg'
    picture.write_bytes(image_bytes(size=(900, 500)))
    page.click('[data-bd-open]')
    page.set_input_files('[data-bd-photo-file]', str(picture))
    expect(page.locator('[data-bd-photo-add]')).to_be_enabled()
    page.click('[data-bd-photo-add]')
    img = page.locator('.bd-photo img')
    expect(img).to_be_visible()
    expect(img).to_have_attribute('draggable', 'false')
    before = dict(board_media[0].store().board().data['active'][0])
    page.evaluate("window.nativePhotoDrags = 0; document.addEventListener('dragstart', e => { if (e.target.matches('.bd-photo img')) window.nativePhotoDrags++; });")
    box = img.bounding_box()
    x, y = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 200, y + 120, steps=16)
    with page.expect_response(lambda r: r.url.endswith('/postits/layout') and r.request.method == 'POST') as saved:
        page.mouse.up()
    assert saved.value.ok
    expect(page.locator('.bd-photo')).not_to_have_attribute('data-dragging', 'true')
    after = board_media[0].store().board().data['active'][0]
    assert (after['pos_x'], after['pos_y']) != (before['pos_x'], before['pos_y'])
    assert page.evaluate('window.nativePhotoDrags') == 0
    page.reload()
    expect(page.locator('.bd-photo')).to_have_attribute('style', re.compile(f"--x: {after['pos_x']}"))
    handle = page.locator('[data-bd-resize]')
    expect(handle).to_be_visible()                      # it appears once the board has been drawn after the reload
    box = None
    for _ in range(40):                                 # the board may redraw once more right after a reload; measure the settled control
        box = handle.bounding_box()
        if box:
            break
        page.wait_for_timeout(50)
    assert box and box['width'] >= 24
    handle.focus()
    page.keyboard.press('ArrowRight')
    expect(page.locator('.bd-photo')).to_have_css('width', '200px')
    for width in (320, 390, 768, 900, 1440):
        page.set_viewport_size({'width': width, 'height': 900})
        _no_page_overflow(page)
        expect(img).to_be_visible()
        page.screenshot(path=f'/private/tmp/board-polish-{width}.png', full_page=True)
    ctx.close()
