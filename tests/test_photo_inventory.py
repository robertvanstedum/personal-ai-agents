"""Git carries only the images a site page uses. A new optional photo belongs in stored data, and this test says so.

scripts/release/photo_inventory.json lists every tracked image. To add an image a page really shows, add a row with
class "stays", reason "site_page", and the file that names it in used_by. Optional and unused rows only ever shrink.
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "scripts" / "release" / "photo_inventory.json"
IMAGE = re.compile(r"\.(png|jpe?g|webp|gif|svg|avif|heic|bmp|tiff?|ico)$", re.I)
# The list as of 6 Nov 2026. These numbers go down as files leave git, and never up.
MAX_OPTIONAL = 14
MAX_UNUSED = 126
PHOTO_URL = re.compile(r"/static/photos/([^'\"()?\s]+)")

spec = importlib.util.spec_from_file_location("move_optional_photos",
                                              ROOT / "scripts" / "release" / "move_optional_photos.py")
mover = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mover)


def _images() -> dict:
    return json.loads(INVENTORY.read_text(encoding="utf-8"))["images"]


def _tracked_images() -> set[str]:
    listed = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True).stdout.decode()
    return {path for path in listed.split("\0") if IMAGE.search(path)}


def unlisted(tracked: set[str], images: dict) -> set[str]:
    return tracked - set(images)


def test_every_image_in_git_is_on_the_list():
    missing = unlisted(_tracked_images(), _images())
    assert not missing, (
        "New image in git. If it is optional, keep it in the stored-data folder (core/photo_store.py), not in git. "
        f"If a page shows it, add a site_page row to {INVENTORY.relative_to(ROOT)}: {sorted(missing)}"
    )


def test_the_guard_catches_a_new_photo():
    images = {"domains/german/static/photos/a.jpg": {}}
    assert unlisted({"domains/german/static/photos/a.jpg", "domains/german/static/photos/new.jpg"}, images) == {
        "domains/german/static/photos/new.jpg"}


def test_every_listed_image_is_in_git():
    gone = set(_images()) - _tracked_images()
    assert not gone, f"Remove these rows; the files left git: {sorted(gone)}"


def test_optional_and_unused_only_shrink():
    classes = [row["class"] for row in _images().values()]
    assert classes.count("moves") <= MAX_OPTIONAL
    assert classes.count("unused") <= MAX_UNUSED
    assert set(classes) <= {"stays", "moves", "unused"}


@pytest.mark.parametrize("reason", ["site_page", "readme", "test_pinned"])
def test_an_image_that_stays_is_still_named_by_what_keeps_it(reason):
    stale = []
    for path, row in _images().items():
        if row["class"] != "stays" or row["reason"] != reason:
            continue
        if not row["used_by"] or not any(
            Path(path).name in (ROOT / used).read_text(encoding="utf-8", errors="ignore")
            for used in row["used_by"] if (ROOT / used).is_file()
        ):
            stale.append(path)
    assert not stale, f"Nothing names these any more; reclassify them: {stale}"


def test_a_site_page_row_points_at_a_page_not_a_test_or_a_document():
    for path, row in _images().items():
        if row["reason"] == "site_page":
            for used in row["used_by"]:
                assert "tests/" not in used and not used.endswith(".md"), (path, used)


@pytest.mark.parametrize("domain", ["german", "portuguese", "cos"])
def test_every_photo_a_page_names_ships_and_stays(domain):
    base = ROOT / "domains" / domain
    sources = [*(base / "templates").rglob("*.html"), *(base / "static").glob("*.css")]
    named = {name for source in sources for name in PHOTO_URL.findall(source.read_text(encoding="utf-8"))}
    assert named
    images = _images()
    for name in named:
        row = images[f"domains/{domain}/static/photos/{name}"]
        assert row["class"] == "stays", name


def test_the_move_plan_is_the_optional_list_and_never_a_portuguese_or_page_photo():
    rows = mover.plan(_images())
    assert len(rows) == sum(row["class"] == "moves" for row in _images().values())
    assert not [path for path, _, _ in rows if "portuguese" in path]
    assert not mover.still_on_a_page(rows, mover.names_on_pages())
    targets = dict((path, target) for path, target, _ in rows)
    assert targets["domains/german/static/photos/Bookstore_1.jpg"] == "german/photos/Bookstore_1.jpg"
    assert targets["domains/cos/static/photos/cos_discuss.jpg"] == "guild/photos/cos_discuss.jpg"


def test_the_plan_with_unused_leaves_documents_in_place():
    rows = mover.plan(_images(), include_unused=True)
    assert not [path for path, _, _ in rows if path.startswith("docs/")]
    assert not [path for path, target, _ in rows if path.startswith("minimoi_portal/static/tour/")
                and not target.startswith("guild/photos/archive/")]


def test_a_photo_a_page_names_blocks_the_move():
    assert mover.still_on_a_page([("domains/german/static/photos/a.jpg", "german/photos/a.jpg", 1)], "url(photos/a.jpg)") == [
        "domains/german/static/photos/a.jpg"]


def test_the_default_is_a_dry_run(capsys, monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("a dry run must not reach this")

    monkeypatch.setattr(mover, "apply", refuse)
    monkeypatch.setattr(mover, "copy_verified", refuse)
    assert mover.main([]) == 0
    assert "Dry run: nothing was copied or removed." in capsys.readouterr().out


def test_apply_needs_a_destination_outside_the_repository(monkeypatch, tmp_path):
    monkeypatch.setattr(mover, "apply", lambda *_a: pytest.fail("must refuse before applying"))
    with pytest.raises(SystemExit):
        mover.main(["--apply"])
    with pytest.raises(SystemExit):
        mover.main(["--apply", "--dest", str(ROOT / "data" / "photos")])


def test_a_copy_is_byte_for_byte_and_never_overwrites_different_bytes(tmp_path):
    source = tmp_path / "IMG_1.jpg"
    source.write_bytes(b"\xff\xd8\xff\xe1 Exif GPS and all \xff\xd9")
    target = tmp_path / "dest" / "german" / "photos" / "IMG_1.jpg"
    mover.copy_verified(source, target)
    assert target.read_bytes() == source.read_bytes()
    target.write_bytes(b"someone else's")
    with pytest.raises(SystemExit):
        mover.copy_verified(source, target)
    assert target.read_bytes() == b"someone else's"
