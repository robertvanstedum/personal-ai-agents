"""German and Portuguese read /static/photos/ from stored data first, and an empty folder changes nothing."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.photo_store import PHOTOS_ENV, stored_photo, stored_photos_dir

ROOT = Path(__file__).resolve().parents[1]
DOMAINS = [("german", "german_client"), ("portuguese", "portuguese_client")]
PHOTO_URL = re.compile(r"/static/photos/([^'\"()?\s]+)")


def _page_photos(domain: str) -> set[str]:
    base = ROOT / "domains" / domain
    sources = [*(base / "templates").rglob("*.html"), *(base / "static").glob("*.css")]
    return {name for source in sources for name in PHOTO_URL.findall(source.read_text(encoding="utf-8"))}


@pytest.fixture(params=DOMAINS, ids=[domain for domain, _ in DOMAINS])
def domain_client(request):
    domain, fixture = request.param
    return domain, request.getfixturevalue(fixture)


def test_every_page_photo_is_served_as_shipped_when_the_folder_is_empty(domain_client, tmp_path, monkeypatch):
    domain, client = domain_client
    monkeypatch.setenv(PHOTOS_ENV, str(tmp_path))
    names = _page_photos(domain)
    assert len(names) >= 9
    for name in sorted(names):
        response = client.get(f"/static/photos/{name}")
        assert response.status_code == 200, name
        assert response.data == (ROOT / "domains" / domain / "static" / "photos" / name).read_bytes(), name
        response.close()


def test_every_page_photo_is_served_when_the_folder_does_not_exist(domain_client, tmp_path, monkeypatch):
    domain, client = domain_client
    monkeypatch.setenv(PHOTOS_ENV, str(tmp_path / "never-made"))
    name = sorted(_page_photos(domain))[0]
    response = client.get(f"/static/photos/{name}")
    assert response.status_code == 200
    response.close()


def test_a_stored_photo_replaces_the_shipped_one_of_that_name(domain_client, tmp_path, monkeypatch):
    domain, client = domain_client
    name = sorted(_page_photos(domain))[0]
    (tmp_path / domain).mkdir()
    (tmp_path / domain / name).write_bytes(b"stored bytes, exif and all")
    monkeypatch.setenv(PHOTOS_ENV, str(tmp_path))
    response = client.get(f"/static/photos/{name}")
    assert response.status_code == 200
    assert response.data == b"stored bytes, exif and all"
    response.close()


def test_an_optional_photo_is_served_under_its_own_name_and_others_still_fall_back(domain_client, tmp_path, monkeypatch):
    domain, client = domain_client
    (tmp_path / domain).mkdir()
    (tmp_path / domain / "only-in-store.jpg").write_bytes(b"optional")
    monkeypatch.setenv(PHOTOS_ENV, str(tmp_path))
    response = client.get("/static/photos/only-in-store.jpg")
    assert response.status_code == 200 and response.data == b"optional"
    response.close()
    response = client.get(f"/static/photos/{sorted(_page_photos(domain))[0]}")
    assert response.status_code == 200 and response.data != b"optional"
    response.close()


def test_a_photo_nowhere_is_a_404_and_the_folder_serves_only_images(domain_client, tmp_path, monkeypatch):
    domain, client = domain_client
    (tmp_path / domain).mkdir()
    (tmp_path / domain / "notes.txt").write_text("not a photo")
    monkeypatch.setenv(PHOTOS_ENV, str(tmp_path))
    assert client.get("/static/photos/missing.jpg").status_code == 404
    assert client.get("/static/photos/notes.txt").status_code == 404


def test_the_rest_of_static_is_untouched(domain_client):
    domain, client = domain_client
    stylesheet = client.get(f"/static/{domain}.css")
    assert stylesheet.status_code == 200
    stylesheet.close()


def test_stored_and_shipped_photos_get_the_same_cache_lifetime(domain_client, tmp_path, monkeypatch):
    domain, client = domain_client
    stylesheet = client.get(f"/static/{domain}.css")
    (tmp_path / domain).mkdir()
    (tmp_path / domain / "only-in-store.jpg").write_bytes(b"optional")
    monkeypatch.setenv(PHOTOS_ENV, str(tmp_path))
    stored = client.get("/static/photos/only-in-store.jpg")
    assert stored.headers["Cache-Control"] == stylesheet.headers["Cache-Control"]
    stored.close()
    stylesheet.close()


def test_german_and_portuguese_use_the_same_route(german_client, portuguese_client):
    for client in (german_client, portuguese_client):
        view = client.application.view_functions["stored_or_shipped_photo"]
        assert view.__module__ == "core.photo_store"


def test_the_folder_comes_from_the_environment_or_the_domains_data(tmp_path, monkeypatch):
    monkeypatch.delenv(PHOTOS_ENV, raising=False)
    assert stored_photos_dir("german", tmp_path / "data") == (tmp_path / "data" / "photos").resolve()
    monkeypatch.setenv(PHOTOS_ENV, str(tmp_path / "stored"))
    assert stored_photos_dir("german", tmp_path / "data") == (tmp_path / "stored" / "german").resolve()


def test_a_name_cannot_climb_out_of_the_folder(tmp_path, monkeypatch):
    monkeypatch.setenv(PHOTOS_ENV, str(tmp_path / "stored"))
    (tmp_path / "stored" / "german").mkdir(parents=True)
    (tmp_path / "stored" / "secret.jpg").write_bytes(b"outside")
    assert stored_photo("german", tmp_path, "../secret.jpg") is None
    assert stored_photo("german", tmp_path, "..\\secret.jpg") is None
