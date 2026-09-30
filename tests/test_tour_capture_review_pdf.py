import json
from pathlib import Path
import re

from PIL import Image, ImageDraw
import pytest

from scripts.tools.tour_capture import cli
from scripts.tools.tour_capture import review_pdf
from scripts.tools.tour_capture.manifest import (
    CapturedScene,
    write_manifest,
    write_report,
    write_review_page,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "tour_capture" / "sample.ppm"
SCENES = (
    ("landing", "Prototype landing", "The first screen a reviewer sees.", (24, 15)),
    ("detail", "Detail panel", "Opens the detail panel for one item.", (48, 30)),
)


def _make_run(root: Path, scenario_id="prototype-review", profile="desktop", sizes=None):
    """Write a complete synthetic capture run the way CaptureRunner does."""
    run_dir = root / scenario_id / "2026-09-26T120000Z"
    for name in ("raw", "optimized"):
        (run_dir / name).mkdir(parents=True)
    scenes = []
    for order, (slug, title, description, size) in enumerate(SCENES, start=1):
        size = (sizes or {}).get(slug, size)
        stem = f"{order:02d}-prototype-{slug}-{profile}"
        with Image.open(FIXTURE) as source:
            image = source.convert("RGB").resize(size)
        image.save(run_dir / "raw" / f"{stem}.png", "PNG")
        image.save(run_dir / "optimized" / f"{stem}.webp", "WEBP")
        scenes.append(
            CapturedScene(
                order=order,
                scene=slug,
                title=title,
                description=description,
                alt=title,
                raw=f"raw/{stem}.png",
                optimized=f"optimized/{stem}.webp",
                width=size[0],
                height=size[1],
                bytes=1,
                captured_at=f"2026-09-26T12:0{order}:00+00:00",
            )
        )
    scenario = {"id": scenario_id, "domain": "prototype", "device_profile": profile}
    write_manifest(run_dir, scenario, scenes, {})
    write_report(
        run_dir,
        {
            "status": "complete",
            "scenario": scenario_id,
            "base_url": "http://127.0.0.1:18895",
            "started_at": "2026-09-26T12:00:00+00:00",
            "completed_at": "2026-09-26T12:05:00+00:00",
        },
    )
    return run_dir, write_review_page(run_dir, scenario, scenes)


def _page_count(pdf: Path) -> int:
    return len(re.findall(rb"/Type\s*/Page\b(?!s)", pdf.read_bytes()))


def test_pdf_has_cover_plus_one_page_per_scene(tmp_path):
    run_dir, _ = _make_run(tmp_path)
    output = review_pdf.build_review_pdf([run_dir], tmp_path / "out.pdf", title="Review")
    assert output.read_bytes().startswith(b"%PDF")
    assert _page_count(output) == len(SCENES) + 1


def test_pdf_combines_multiple_runs_in_order(tmp_path):
    first, _ = _make_run(tmp_path, "first-run")
    second, _ = _make_run(tmp_path, "second-run", profile="mobile", sizes={"landing": (10, 30)})
    output = review_pdf.build_review_pdf([first, second], tmp_path / "both.pdf")
    assert _page_count(output) == len(SCENES) * 2 + 1


def test_captions_come_from_manifest_data(tmp_path, monkeypatch):
    run_dir, _ = _make_run(tmp_path)
    drawn: list[str] = []
    original = ImageDraw.ImageDraw.text

    def spy(self, xy, text, *args, **kwargs):
        drawn.append(text)
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", spy)
    review_pdf.build_review_pdf([run_dir], tmp_path / "out.pdf", title="Guild review")
    joined = "\n".join(drawn)

    assert "Guild review" in drawn
    assert "01. Prototype landing" in drawn
    assert "02. Detail panel" in drawn
    assert "The first screen a reviewer sees." in joined
    assert "desktop · 1440×900 @2x" in joined
    assert "captured 2026-09-26 12:01 UTC" in joined
    assert "http://127.0.0.1:18895" in joined
    assert "simulated" in joined


def test_scene_pages_keep_aspect_ratio_and_orientation(tmp_path):
    run_dir, _ = _make_run(tmp_path, sizes={"landing": (1170, 2532)})
    scenes = review_pdf.load_run(run_dir).scenes
    tall = review_pdf._scene_page(scenes[0], 2, 3)
    wide = review_pdf._scene_page(scenes[1], 3, 3)
    assert tall.size == review_pdf.PORTRAIT
    assert wide.size == review_pdf.LANDSCAPE


def test_incomplete_run_is_rejected(tmp_path):
    run_dir, _ = _make_run(tmp_path)
    write_report(run_dir, {"status": "failed"})
    with pytest.raises(review_pdf.ReviewPdfError, match="did not complete"):
        review_pdf.build_review_pdf([run_dir], tmp_path / "out.pdf")


def test_review_pdf_cli_writes_output(tmp_path, capsys):
    run_dir, _ = _make_run(tmp_path)
    output = tmp_path / "bundle" / "review.pdf"
    assert review_pdf.main([str(run_dir), "-o", str(output), "--title", "T"]) == 0
    assert output.is_file()
    assert "review pdf:" in capsys.readouterr().out


def _write_scenario_file(tmp_path: Path) -> Path:
    path = tmp_path / "example_scenario.json"
    path.write_text(
        json.dumps(
            {
                "id": "prototype-review",
                "domain": "prototype",
                "device_profile": "desktop",
                "auth_profile": "none",
                "start_path": "/guild",
                "steps": [
                    {"goto": "/guild"},
                    {"wait_for": "body"},
                    {"screenshot": "landing", "title": "t", "description": "d", "alt": "a"},
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_scenario_file_dry_run(tmp_path, capsys):
    path = _write_scenario_file(tmp_path)
    assert cli.main(["--scenario-file", str(path), "--dry-run"]) == 0
    assert "valid: prototype-review · 1 screenshots" in capsys.readouterr().out


def test_scenario_file_dry_run_refuses_auth_none_on_dev(tmp_path, capsys):
    path = _write_scenario_file(tmp_path)
    code = cli.main(
        ["--scenario-file", str(path), "--dry-run", "--base-url", "https://dev.minimoi.ai"]
    )
    assert code == 1
    assert "only for localhost or 127.0.0.1" in capsys.readouterr().out


@pytest.mark.parametrize("extra", [["guild-desktop"], []])
def test_scenario_name_and_file_are_mutually_exclusive(tmp_path, extra):
    args = extra + (["--scenario-file", str(_write_scenario_file(tmp_path))] if extra else [])
    with pytest.raises(SystemExit) as exc:
        cli.main(args + ["--dry-run"])
    assert exc.value.code == 2


def test_builtin_scenario_name_still_dry_runs(capsys):
    assert cli.main(["guild-desktop", "--dry-run"]) == 0
    assert "valid: guild-desktop" in capsys.readouterr().out


def test_pdf_flag_writes_review_pdf_into_run_dir(tmp_path, monkeypatch, capsys):
    path = _write_scenario_file(tmp_path)
    created = {}

    class FakeRunner:
        def __init__(self, scenario, base_url, output_root, **kwargs):
            created["args"] = (scenario["id"], base_url, output_root, kwargs)

        def run(self):
            _, review_path = _make_run(tmp_path / "out")
            return review_path

    monkeypatch.setattr(cli, "CaptureRunner", FakeRunner)
    code = cli.main(
        [
            "--scenario-file",
            str(path),
            "--base-url",
            "http://127.0.0.1:18895",
            "--output-root",
            str(tmp_path / "out"),
            "--headless",
            "--pdf",
        ]
    )
    assert code == 0
    pdf = tmp_path / "out" / "prototype-review" / "2026-09-26T120000Z" / "review.pdf"
    assert pdf.is_file()
    assert _page_count(pdf) == len(SCENES) + 1
    assert created["args"][3]["headless"] is True
    assert f"review pdf: {pdf.resolve()}" in capsys.readouterr().out


def test_browser_channel_option_is_validated_and_passed():
    """--browser-channel uses an installed Chrome/Edge; other values are refused."""
    import pytest as _pytest
    from scripts.tools.tour_capture import cli as _cli
    from scripts.tools.tour_capture.runner import CaptureRunError, CaptureRunner

    args = _cli.build_parser().parse_args(
        ["guild-desktop", "--base-url", "http://127.0.0.1:1", "--browser-channel", "chrome"]
    )
    assert args.browser_channel == "chrome"
    with _pytest.raises(SystemExit):
        _cli.build_parser().parse_args(["guild-desktop", "--browser-channel", "firefox"])
    scenario = {"auth_profile": "owner_session", "device_profile": "desktop", "_summary": {}}
    with _pytest.raises(CaptureRunError):
        CaptureRunner(scenario, "http://127.0.0.1:1", _pytest.importorskip("pathlib").Path("/tmp"),
                      browser_channel="firefox")


def test_long_text_is_wrapped_within_width():
    """Cover titles and captions wrap by measured width and never overflow."""
    from PIL import Image, ImageDraw
    from scripts.tools.tour_capture import review_pdf as _rp

    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    font = _rp._font(96, bold=True)
    text = "Guild Shop floor prototype · revision 3 (after) and current dev (before) " * 2
    lines = _rp._fit_lines(draw, text, font, 1500, 3)
    assert 1 < len(lines) <= 3
    assert all(draw.textlength(line, font=font) <= 1500 for line in lines)
    assert lines[-1].endswith("…")


def test_notes_add_an_about_page_after_the_cover(tmp_path):
    run_dir, _ = _make_run(tmp_path)
    output = review_pdf.build_review_pdf([run_dir], tmp_path / "notes.pdf", title="Review",
                                         notes=["Section 1: the current pages.", "Section 2: the new ones."])
    assert _page_count(output) == len(SCENES) + 2


def test_notes_that_do_not_fit_are_refused(tmp_path):
    run_dir, _ = _make_run(tmp_path)
    with pytest.raises(review_pdf.ReviewPdfError, match="do not fit"):
        review_pdf.build_review_pdf([run_dir], tmp_path / "long.pdf", notes=["word " * 3000])


def test_lower_quality_makes_a_smaller_file_and_bad_quality_is_refused(tmp_path):
    from PIL import Image as _Image
    run_dir, _ = _make_run(tmp_path, sizes={"landing": (1200, 800), "detail": (1200, 800)})
    noisy = __import__("os").urandom(1200 * 800 * 3)
    for png in (run_dir / "raw").glob("*.png"):
        _Image.frombytes("RGB", (1200, 800), noisy).save(png)
    high = review_pdf.build_review_pdf([run_dir], tmp_path / "high.pdf", quality=90)
    low = review_pdf.build_review_pdf([run_dir], tmp_path / "low.pdf", quality=60)
    assert low.stat().st_size < high.stat().st_size
    with pytest.raises(review_pdf.ReviewPdfError, match="quality"):
        review_pdf.build_review_pdf([run_dir], tmp_path / "bad.pdf", quality=5)


def test_review_pdf_cli_takes_notes_and_quality(tmp_path, capsys):
    run_dir, _ = _make_run(tmp_path)
    out = tmp_path / "cli.pdf"
    assert review_pdf.main([str(run_dir), "-o", str(out), "--note", "About.", "--quality", "70"]) == 0
    assert _page_count(out) == len(SCENES) + 2
