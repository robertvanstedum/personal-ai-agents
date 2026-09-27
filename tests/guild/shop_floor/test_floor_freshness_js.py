"""The floor's stale/unknown rules in the browser (review F1), tested at the
JavaScript level with Node against static/js/freshness.js, the module
floor.js uses. Skipped when Node is not installed; browser_checks_b1.py
covers the same behaviour on the real page."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[3] / "minimoi_portal" / "guild_ui" / "static" / "js" / "freshness.js"
NODE = shutil.which("node")

SCRIPT = r"""
import * as f from './freshness.mjs';
const out = {};
const T = 1_000_000_000;
const clock = (ms) => `t${(ms - T) / 1000}`;
out.modes = {
  live: f.freshnessOf({ missed: 0, lastGoodMs: T, nowMs: T + 30_000 }),
  one_miss: f.freshnessOf({ missed: 1, lastGoodMs: T, nowMs: T + 60_000 }),
  two_misses: f.freshnessOf({ missed: 2, lastGoodMs: T, nowMs: T + 120_000 }),
  one_miss_3min: f.freshnessOf({ missed: 1, lastGoodMs: T, nowMs: T + 180_000 }),
  no_poll_100s: f.freshnessOf({ missed: 0, lastGoodMs: T, nowMs: T + 100_000 }),
  no_poll_3min: f.freshnessOf({ missed: 0, lastGoodMs: T, nowMs: T + 180_000 }),
  signed_out: f.freshnessOf({ missed: 0, lastGoodMs: T, nowMs: T + 1_000, signedOut: true }),
};
const green = { state: 'green', shape: 'circle', word: 'OK', reason: '2 active · 0 blocked', source_mark: 'live', source: 'live' };
const since = f.sinceText(T, T + 125_000, clock);
out.since = since;
out.views = Object.fromEntries(['live', 'stale', 'unknown', 'signed_out'].map((m) => [m, f.lightView(green, m, since)]));
const needs = { status: 'ok', total: 0, items: [] };
out.needs = Object.fromEntries(['live', 'stale', 'unknown', 'signed_out'].map((m) => [m, f.needsView(needs, m, since, 'Nothing needs you · checked 12:00')]));
out.banners = Object.fromEntries(['live', 'stale', 'unknown', 'signed_out'].map((m) => [m, f.bannerText(m, since, 'the server answered 503')]));
out.briefing = Object.fromEntries(['live', 'stale', 'unknown', 'signed_out'].map((m) => [m, f.briefingText(m, since, 'As of 12:00 · nothing needs you · Queue OK')]));
out.zones = Object.fromEntries(['live', 'stale', 'unknown', 'signed_out'].map((m) => [m, f.zoneMarkText(m, since)]));
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    if NODE is None:
        pytest.skip("node is not installed")
    folder = tmp_path_factory.mktemp("freshness")
    shutil.copy(JS, folder / "freshness.mjs")
    (folder / "run.mjs").write_text(SCRIPT)
    done = subprocess.run([NODE, str(folder / "run.mjs")], capture_output=True, text=True, timeout=30,
                          cwd=folder)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_one_failed_poll_is_stale_two_or_three_minutes_is_unknown(result):
    assert result["modes"] == {
        "live": "live", "one_miss": "stale", "two_misses": "unknown", "one_miss_3min": "unknown",
        "no_poll_100s": "stale", "no_poll_3min": "unknown", "signed_out": "signed_out",
    }


def test_no_mode_but_live_keeps_the_live_mark_or_the_colour(result):
    views = result["views"]
    assert views["live"]["src"] == "live" and views["live"]["state"] == "green"
    for mode in ("stale", "unknown", "signed_out"):
        v = views[mode]
        assert "live" not in v["src"] and v["state"] != "green" and v["shape"] != "circle", mode
        assert result["since"] in v["src"], mode             # time since the last good read
    assert views["stale"]["word"] == "Stale · was OK" and views["stale"]["shape"] == "stale"
    assert views["unknown"]["word"] == "Unknown" and views["unknown"]["state"] == "unknown"
    assert views["signed_out"]["word"] == "Signed out" and views["signed_out"]["state"] == "unknown"
    assert result["since"] == "t0 (2 min ago)"


def test_needs_you_never_says_nothing_once_the_floor_is_not_live(result):
    needs = result["needs"]
    assert needs["live"]["line"].startswith("Nothing needs you")
    assert "stale, last good read t0 (2 min ago)" in needs["stale"]["line"]
    assert needs["unknown"]["line"] == "Needs you · unknown — no good read since t0 (2 min ago)"
    assert needs["signed_out"]["line"] == "Needs you · unknown — signed out"
    assert needs["unknown"]["count"] == "?" and needs["signed_out"]["countWord"] == "unknown"


def test_banner_and_briefing_say_why(result):
    assert result["banners"]["live"] == ""
    assert "the server answered 503" in result["banners"]["stale"]
    assert result["banners"]["unknown"].startswith("Unknown · no good read since t0")
    assert result["banners"]["signed_out"].startswith("Signed out")
    assert result["briefing"]["live"].startswith("As of")
    assert result["briefing"]["stale"].startswith("Stale, last good read t0")
    assert result["briefing"]["unknown"].startswith("Floor unknown")


def test_floor_store_zones_use_the_same_words(result):
    """(c): post-its, Continue and the notes line carry the lights' marks."""
    z = result["zones"]
    assert z["live"] == ""
    assert z["stale"].startswith("Stale · last good read") and z["unknown"].startswith("Unknown · no good read since")
    assert z["signed_out"].startswith("Signed out")
