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
