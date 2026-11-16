"""#633: on a phone, a live CoS call keeps the thread above the composer.

The picture is a band at the top of the thread, not a fixed grid row, and the column uses 100dvh.
Desktop rules stay on 100vh. No call is placed.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COS = (ROOT / "domains/cos/static/cos.css").read_text(encoding="utf-8")
CONFER = (ROOT / "domains/cos/static/calm-confer.css").read_text(encoding="utf-8")


def _block(css, header):
    start = css.index(header)
    depth, i = 0, css.index("{", start)
    for j in range(i, len(css)):
        depth += {"{": 1, "}": -1}.get(css[j], 0)
        if depth == 0:
            return css[i + 1:j]
    raise AssertionError(f"unclosed {header}")


def test_phone_call_drops_the_fixed_picture_row_and_uses_100dvh():
    call = _block(CONFER, "@media (max-width: 700px)")
    assert "#voice-live:not([hidden])" in call
    assert "grid-template-rows:minmax(0,1fr)" in call
    assert "clamp(" not in call and "160px minmax" not in call
    assert ".confer-picture{display:none}" in call
    band = call.split("#chat-log::before{", 1)[1].split("}", 1)[0]
    assert "height:160px" in band and "width:calc(100% + 28px)" in band
    assert "url(photos/cos_discuss_private.png?v=1)" in band
    assert "height:100dvh" in call
    column = _block(COS, "@media (max-width: 700px)")
    assert "height: 100dvh;" in column
    assert ".tab-panel-photo" in column and "height: auto;" in column
    assert "calc(100vh - var(--total-h))" not in column


def test_desktop_column_and_idle_phone_band_stay():
    desktop = COS.split("@media (max-width: 700px)", 1)[0]
    assert desktop.count("calc(100vh - var(--total-h))") >= 3
    assert "min-height: calc(100vh - var(--total-h));" in desktop
    idle = _block(CONFER, "@media(max-width:639px){")
    assert "grid-template-rows:clamp(160px,30dvh,320px) minmax(0,1fr)" in idle
    assert "object-fit:contain" in CONFER.split("@media", 1)[0]
