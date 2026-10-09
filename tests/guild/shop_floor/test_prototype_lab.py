"""Guild 1.1 dev, Prototype Lab (5 Oct): the gallery and one prototype page, drawn from
guild_ui/lab_catalog.py. Real screenshots only, documents on GitHub, a run book that matches
the prototype's own, and no claim that a demo is running. Nothing here starts or checks a demo."""
from __future__ import annotations

from pathlib import Path

import pytest

from floor_helpers import load_portal, staging  # noqa: F401  (pytest fixtures)

from minimoi_portal.guild_ui import lab_catalog as prototypes

REPO = Path(__file__).resolve().parents[3]
STATIC = REPO / "minimoi_portal" / "guild_ui" / "static"
GALLERY = "/guild-next/guild/experiment"
DETAIL = f"{GALLERY}/iot-connect"


def test_the_catalog_is_consistent_with_the_files_it_points_to():
    slugs = [p["slug"] for p in prototypes.CATALOG]
    assert len(slugs) == len(set(slugs))
    for p in prototypes.CATALOG:
        assert (REPO / p["repo_path"]).is_dir(), p["repo_path"]
        for title, path in p["docs"]:
            assert (REPO / path).is_file(), path                      # a GitHub link must not 404 on main
        for s in p["slides"] + p["app_screens"]:
            assert (STATIC / s["file"]).is_file(), s["file"]
        assert p["cover"] is None or (STATIC / p["cover"]).is_file()
        assert p["kind"] in prototypes.KIND_LABEL


def test_the_iot_run_book_matches_the_prototypes_own_run_book():
    book = (REPO / prototypes.IOT_CONNECT["repo_path"] / "HANDS_ON_RUNBOOK.md").read_text()
    run = prototypes.IOT_CONNECT["run"]
    for step in run["steps"]:
        for command in step["commands"]:
            assert command in book, command                        # every command is the run book's own
    for _label, url in run["urls"]:
        assert url.replace("http://", "") in book.replace("<http://", "").replace(">", ""), url
    makefile = (REPO / prototypes.IOT_CONNECT["repo_path"] / "Makefile").read_text()
    for target in ("status", "up", "smoke", "verify", "reset", "down"):
        assert f"\n{target}:" in makefile, target


def test_the_slides_carry_no_location_or_camera_metadata():
    from PIL import Image
    for p in prototypes.CATALOG:
        for s in p["slides"]:
            with Image.open(STATIC / s["file"]) as img:
                assert not img.getexif(), s["file"]
                assert not any(k.lower() in ("exif", "gps", "xmp", "icc_profile") for k in img.info), s["file"]


def test_the_gallery_lists_the_catalog_with_the_two_actions_and_no_running_claim(staging):
    page = staging.owner().get(GALLERY).get_data(as_text=True)
    assert "IoT Connect" in page and "Enterprise Connectivity Management" in page
    assert prototypes.IOT_CONNECT["description"] in page                      # the owner's own words, unabridged
    assert f'href="{DETAIL}"' in page and f'href="{DETAIL}#run"' in page      # Look inside, Run it
    assert 'data-proto-view="cards"' in page and 'data-proto-view="list"' in page
    assert 'data-proto-search' not in page and 'data-proto-filter' not in page # only when there are enough prototypes to need them
    assert 'href="https://minimoi.ai/app/iotconnect/" target="_blank" rel="noopener"' in page
    lowered = page.lower()
    for claim in ("currently running", "running now", "online", "live now", "healthy", "up and running", "available now"):
        assert claim not in lowered, claim                                      # a link is not a health check
    assert "does not check that it is running" in lowered                       # and the page says so


def test_the_prototype_page_has_look_inside_run_it_docs_and_where_it_lives(staging):
    page = staging.owner().get(DETAIL).get_data(as_text=True)
    for anchor in ('id="look"', 'id="shows"', 'id="run"', 'id="docs"', 'id="where"'):
        assert anchor in page, anchor
    assert page.count('class="proto-shot"') == 5
    assert "No screenshots of the running application have been captured yet" in page      # never a placeholder as a picture
    assert "make up" in page and "make status" in page and "make down" in page
    assert page.count("data-copy=") >= 10
    assert "This page does not check whether it is running" in page
    assert "8 GB" in page and "has not been measured" in page                  # no invented memory figure
    assert "https://github.com/robertvanstedum/personal-ai-agents/blob/main/prototype-lab/projects/project-iot-connect/README.md" in page
    assert "script-src 'self'" in staging.owner().get(DETAIL).headers["Content-Security-Policy"]


def test_a_prototype_page_that_is_not_in_the_catalog_is_a_plain_404(staging):
    r = staging.owner().get(f"{GALLERY}/nope")
    assert r.status_code == 404 and "Prototype not found" in r.get_data(as_text=True)
    assert staging.owner().get(f"{GALLERY}/..%2Fsecrets").status_code == 404


@pytest.mark.parametrize("path", [GALLERY, DETAIL, "/guild-next/guild/ui-assets/prototypes/iot-connect/slide-1.png"])
def test_only_the_owner_sees_it(staging, path):
    assert staging.owner().get(path).status_code == 200
    for client in (staging.guest(), staging.client()):
        assert client.get(path).status_code in (302, 403)


def test_the_page_uses_no_inline_script_or_style(staging):
    import re
    mains = [staging.owner().get(u).get_data(as_text=True) for u in (DETAIL, GALLERY)]
    body = "".join(re.search(r"<main.*?</main>", m, flags=re.S).group(0) for m in mains)       # the portal bar has its own inline style
    assert "<style" not in body and " style=" not in body and "onclick=" not in body
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", body)
