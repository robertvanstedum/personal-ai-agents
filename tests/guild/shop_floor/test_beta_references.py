"""Approved beta source boundaries and staging entry contract."""
from pathlib import Path
import pytest
from floor_helpers import load_portal, staging
from minimoi_portal.guild_ui import references


def test_exact_nested_flat_and_denied_paths(tmp_path):
    for ref in ("docs/specs/flat.md", "docs/specs/pkg/nested.md", "planning-studio/initiatives/one/documents/spec.md"):
        p = tmp_path / ref
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# Exact source")
        assert references.resolve(ref, tmp_path) == p
    assert references.resolve("flat.md", tmp_path) == tmp_path / "docs/specs/flat.md"
    for ref in ("nested.md", "docs/../secret.md", "/tmp/secret.md", "docs/../../secret.md", "docs/specs/flat.md/", "docs\\secret.md", "README.md", "docs/missing.md"):
        assert references.resolve(ref, tmp_path) is None
    outside = tmp_path / "private.md"
    outside.write_text("private")
    (tmp_path / "docs/escape.md").symlink_to(outside)
    assert references.resolve("docs/escape.md", tmp_path) is None


def test_guarded_pages_and_exact_spec(staging, tmp_path, monkeypatch):
    monkeypatch.setattr(references, "ROOT", tmp_path)
    spec = tmp_path / "docs/specs/spec_floor_api.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# Actual spec\n<script>alert(1)</script>")
    url = "/guild-next/guild/build/items/12/spec"
    answer = staging.owner().get(url)
    assert answer.status_code == 200
    assert b"Actual spec" in answer.data and b"<script>alert(1)</script>" not in answer.data
    assert staging.owner().get("/guild-next/guild/build/items/999/spec").status_code == 404
    for client in (staging.guest(), staging.client()):
        for path in (url, "/guild-next/guild/docs", "/guild-next/guild/improve", "/guild-next/guild/experiment", "/guild-next/guild/operate/checks"):
            assert client.get(path).status_code in (302, 403)


def test_primary_rollback_and_previous(staging, monkeypatch):
    assert staging.owner().get("/guild").location == "/guild-next/"
    assert staging.owner().get("/guild-previous").status_code == 200
    monkeypatch.setenv("MINIMOI_GUILD_PRIMARY", "0")
    assert staging.owner().get("/guild").status_code == 200
    assert staging.owner().post("/guild").status_code == 405


def test_production_entry_unchanged(load_portal):
    portal = load_portal(base_url="https://minimoi.ai")
    assert portal.owner().get("/guild").status_code == 200
    assert portal.owner().get("/guild-previous").status_code == 404


@pytest.mark.parametrize("path", ["docs", "improve", "experiment", "operate/checks", "operate/maintain", "operate/tending"])
def test_new_sections_are_source_backed_not_mocked(staging, path):
    response = staging.owner().get("/guild-next/guild/" + path)
    if path == "docs":
        assert response.status_code == 302
        assert response.headers["Location"] == "https://github.com/robertvanstedum/personal-ai-agents/tree/main/docs"
        return
    assert response.status_code == 200
    if path == "operate/tending":
        assert b"In design." in response.data
    elif path == "improve":
        assert b"Search saved references" in response.data and b"Add a reference" in response.data
    elif path == "experiment":
        assert b"https://minimoi.ai/app/iotconnect/" in response.data
    elif path == "operate/maintain":
        assert b"Search runbooks" in response.data
    else:
        assert b"Source:" in response.data
    assert b"Previous Guild" not in response.data


def test_full_reader_download_and_relative_links(staging, tmp_path, monkeypatch):
    monkeypatch.setattr(references, "ROOT", tmp_path)
    folder = tmp_path / "docs/specs"
    folder.mkdir(parents=True)
    (folder / "other.md").write_text("# Other")
    source = "# Long document\n[Other](other.md)\n[Escape](../../secret.md)\n" + "Long content " * 2000 + " END_MARKER"
    (folder / "long.md").write_text(source)
    url = "/guild-next/guild/document?ref=docs/specs/long.md"
    # Obtain route from the registered endpoint, independent of mount spelling.
    with staging.app.test_request_context():
        from flask import url_for
        url = url_for("guild_ui_next.document", ref="docs/specs/long.md")
    response = staging.owner().get(url)
    assert response.status_code == 200
    assert b"END_MARKER" in response.data
    assert b"ref=docs/specs/other.md" in response.data
    assert b'href="../../secret.md"' not in response.data
    download = staging.owner().get(url + "&download=md")
    assert download.mimetype == "text/markdown" and download.get_data(as_text=True) == source
    for client in (staging.guest(), staging.client()):
        assert client.get(url + "&download=md").status_code in (302, 403)
