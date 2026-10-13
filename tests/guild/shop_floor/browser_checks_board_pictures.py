"""Direct Board picture gestures, real local media storage, no external services."""
import pytest
from playwright.sync_api import expect
from browser_checks_b1 import (browser, server, fresh_queue, floor, board_media, _context, go,
                               _png_b64, _no_page_overflow)  # noqa: F401


def _picture(page, kind='paste', selector='body', mime='image/png', size=None):
    page.evaluate("""([b64, kind, selector, mime, size]) => {
      const bytes = size ? new Uint8Array(size) : Uint8Array.from(atob(b64), c => c.charCodeAt(0));
      const transfer = new DataTransfer();
      transfer.items.add(new File([bytes], 'clipboard.png', {type: mime}));
      const event = kind === 'paste' ? new ClipboardEvent('paste', {clipboardData: transfer, bubbles: true, cancelable: true})
        : new DragEvent('drop', {dataTransfer: transfer, bubbles: true, cancelable: true});
      document.querySelector(selector).dispatchEvent(event);
    }""", [_png_b64(), kind, selector, mime, size])


@pytest.mark.parametrize('width', [320, 390, 768, 900, 1440])
def test_paste_adds_a_picture_without_a_library_form_and_survives_reload(browser, server, board_media, width):
    ctx, page = _context(browser, server, width, 900)
    go(page, server['url'] + '/guild-next/guild/board')
    _picture(page)
    expect(page.locator('[data-bd-note].bd-photo')).to_have_count(1)
    expect(page.locator('[data-bd-photo-form]')).to_be_hidden()
    expect(page.locator('[data-bd-view="active"]')).to_have_text('Active 1')
    expect(page.locator('[data-bd-view="active"]')).to_be_visible()
    _no_page_overflow(page)
    page.screenshot(path=f'/private/tmp/board-paste-{width}.png')
    page.reload()
    expect(page.locator('[data-bd-note].bd-photo')).to_have_count(1)
    ctx.close()


def test_drop_uncertain_response_retries_same_picture_without_duplicate(browser, server, board_media):
    ctx, page = _context(browser, server, 1440, 900)
    go(page, server['url'] + '/guild-next/guild/board')
    keys = []
    def lose_first_reply(route):
        keys.append(route.request.post_data_json['idempotency_key'])
        response = route.fetch()  # the first placement succeeds server-side, but its reply is lost
        if len(keys) == 1:
            route.abort()
        else:
            route.fulfill(response=response)
    page.route('**/postits/photo', lose_first_reply)
    _picture(page, 'drop', '[data-bd-cork]')
    expect(page.locator('[data-bd-paste-retry]')).to_be_visible()
    page.click('[data-bd-paste-retry]')
    expect(page.locator('[data-bd-note].bd-photo')).to_have_count(1)
    assert len(keys) == 2 and keys[0] == keys[1]
    assert len(board_media[0].store().board().data['active']) == 1
    ctx.close()


def test_private_unsupported_oversize_and_chat_pastes_do_not_upload(browser, server, board_media):
    ctx, page = _context(browser, server, 1440, 900)
    go(page, server['url'] + '/guild-next/guild/board?chat=floating')
    uploads = []
    page.on('request', lambda request: uploads.append(request.url) if request.method == 'POST' else None)
    _picture(page, mime='image/svg+xml')
    expect(page.locator('[data-bd-result]')).to_contain_text('JPEG')
    _picture(page, size=9 * 1024 * 1024)
    expect(page.locator('[data-bd-result]')).to_contain_text('smaller')
    _picture(page, selector='[data-mc-input]')
    page.click('[data-mc-record]')
    _picture(page)
    expect(page.locator('[data-bd-result]')).to_contain_text('record')
    assert uploads == []
    expect(page.locator('[data-bd-note]')).to_have_count(0)
    ctx.close()


def test_server_validation_refusal_does_not_offer_same_picture_retry(browser, server, board_media):
    ctx, page = _context(browser, server, 1440, 900)
    go(page, server['url'] + '/guild-next/guild/board')
    page.route('**/api/v1/media', lambda route: route.fulfill(status=415,
        json={'error': 'unsupported_type', 'message': 'This picture format is not supported.'}))
    _picture(page)
    expect(page.locator('[data-bd-result]')).to_contain_text('not supported')
    expect(page.locator('[data-bd-paste-retry]')).to_be_hidden()
    page.click('[data-bd-paste-dismiss]')
    page.unroute('**/api/v1/media')
    _picture(page)
    expect(page.locator('[data-bd-note].bd-photo')).to_have_count(1)
    ctx.close()
