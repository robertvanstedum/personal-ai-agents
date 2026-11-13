"""Phone Confer: the table photograph is a full-width band, not a letterboxed thumbnail (#566), and the picture choice still hides it."""
from playwright.sync_api import expect

from browser_checks_beta_phone_header import _page

LOADED = "() => { const i = document.querySelector('.confer-picture img'); return i && i.complete && i.naturalWidth > 0; }"


def _band(page):
    return page.evaluate('''() => {
      const aside = document.querySelector('.confer-picture');
      const img = aside.querySelector('img');
      const a = aside.getBoundingClientRect(), i = img.getBoundingClientRect();
      const input = document.querySelector('#send-input').getBoundingClientRect();
      return {shown: getComputedStyle(aside).display !== 'none', left: i.left, right: i.right, width: innerWidth,
        imgHeight: i.height, gapTop: i.top - a.top, gapBottom: a.bottom - i.bottom, fit: getComputedStyle(img).objectFit,
        height: innerHeight, inputTop: input.top, inputBottom: input.bottom, loaded: img.complete && img.naturalWidth > 0};
    }''')


def test_phone_confer_art_is_a_full_width_band_and_hides_on_the_picture_choice():
    pw, browser, page = _page(390, scripts=False)
    try:
        page.wait_for_function(LOADED)
        band = _band(page)
        assert band['shown'] and band['loaded'], band
        assert band['left'] <= 0.5 and band['right'] >= band['width'] - 0.5, band        # edge to edge
        assert band['fit'] == 'cover', band
        assert 0.27 * band['height'] <= band['imgHeight'] <= 0.36 * band['height'], band   # about 30dvh, not a thumbnail
        assert abs(band['gapTop']) <= 0.5 and band['gapBottom'] <= 1.5, band               # the art fills the band
        assert 0 <= band['inputTop'] < band['inputBottom'] <= band['height'], band         # the chat stays usable
        page.screenshot(path='/private/tmp/beta-confer-phone-art-390.png')
        page.evaluate("() => { document.body.dataset.picture = 'hidden' }")
        expect(page.locator('.confer-picture')).to_be_hidden()
    finally:
        browser.close()
        pw.stop()


def test_desktop_confer_keeps_the_whole_side_picture():
    pw, browser, page = _page(1440, scripts=False)
    try:
        page.wait_for_function(LOADED)
        assert page.evaluate("() => getComputedStyle(document.querySelector('.confer-picture img')).objectFit") == 'contain'
    finally:
        browser.close()
        pw.stop()
