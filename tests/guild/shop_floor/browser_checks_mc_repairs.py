"""Owner composer regressions: attachment-only delivery and retained text context."""
from playwright.sync_api import expect
from browser_checks_b1 import (browser, server, streaming_mc, board_media, fresh_queue, floor,
    _context, go, _nav_click, _script, FINISH)  # noqa: F401


def test_file_only_turn_reaches_backend_without_invented_instruction(browser, server, streaming_mc, board_media, tmp_path):
    streaming_mc["runtime"].script = _script("File received.", *FINISH)
    attachment = tmp_path / "brief.txt"
    attachment.write_text("Synthetic source: three concise bullets.")
    ctx, page = _context(browser, server, 1440, 900)
    go(page, f"{server['url']}/guild-next/guild/build")
    _nav_click(page, "[data-conv-new]")
    page.set_viewport_size({"width":390,"height":800})
    page.set_input_files("[data-mc-file]", str(attachment))
    expect(page.locator('.att[data-state="ok"]')).to_have_count(1)
    expect(page.locator("#mc-input")).to_have_value("")
    page.click("[data-mc-send]")
    expect(page.locator('[data-note-files]').last).to_contain_text("brief.txt", timeout=10000)
    expect(page.locator('.att')).to_have_count(0)
    posts = [p for p in streaming_mc["runtime"].posts if p["url"].endswith("/chat/completions")]
    assert len(posts) == 1
    content = posts[0]["body"]["messages"][0]["content"]
    assert "Attached files." in content and "Synthetic source: three concise bullets." in content
    page.click("[data-mc-send]")
    assert len(streaming_mc["runtime"].posts) == len(posts)
    ctx.close()


def test_popup_options_keep_provider_comparison_on_full_chat(browser, server, streaming_mc, monkeypatch):
    from test_mc_voice import _on
    _on(monkeypatch)
    monkeypatch.setenv('MINIMOI_MC_EVERYWHERE', '1')
    ctx, page = _context(browser, server, 1440, 900)
    for width in (320, 390, 768, 900, 1440):
        page.set_viewport_size({'width': width, 'height': 800})
        go(page, f"{server['url']}/guild-next/guild/companion/german")
        # Simulate capabilities arrival; no microphone/provider connection is made.
        page.evaluate("""() => {
          document.querySelector('[data-mc-options]').open = true;
          document.querySelector('[data-mc-managed-settings]').hidden = false;
          document.querySelector('[data-mc-voice-btn]').hidden = false;
          const lock = document.querySelector('[data-mc-record]');
          lock.dataset.privateOff = ''; lock.disabled = true;
        }""")
        expect(page.locator('[data-mc-provider-control]')).not_to_be_visible()
        expect(page.locator('[data-mc-record]')).not_to_be_visible()
        expect(page.locator('[data-mc-voice-transcript-choice]')).to_be_visible()
        box = page.locator('[data-mc-voice-btn]').bounding_box()
        assert 44 <= box['width'] <= 48 and box['height'] >= 44
    go(page, f"{server['url']}/guild-next/guild/build")
    page.evaluate("""() => {
      document.querySelector('[data-mc-options]').open = true;
      document.querySelector('[data-mc-managed-settings]').hidden = false;
    }""")
    expect(page.locator('[data-mc-voice-provider]')).to_be_visible()
    ctx.close()
