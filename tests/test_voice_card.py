"""MC's voice card loads from a release, only when its hash is the manifest's, and never invents a voice id."""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from core.realtime_voice.voice_card import load_card

REPO = Path(__file__).resolve().parents[1]


def release_with(folder, payload):
    folder.mkdir()
    raw = json.dumps(payload).encode()
    (folder / "voice.json").write_bytes(raw)
    (folder / "manifest.json").write_text(json.dumps({"files": {"voice.json": hashlib.sha256(raw).hexdigest()}}))


def test_an_unset_voice_uses_the_provider_default_and_says_so(tmp_path, monkeypatch):
    import core.realtime_voice.voice_card as cards
    release_with(tmp_path / "0.9.0", {
        "schema": 1,
        "card": "Master Craftsman. English by default. Light Austrian accent.",
        "voices": {},
    })
    monkeypatch.setattr(cards, "RELEASES", tmp_path)
    loaded = load_card("0.9.0")
    assert loaded["voices"] == {"openai": "marin", "xai": "eve"}
    assert loaded["notices"] == ["openai voice is unset, using marin", "xai voice is unset, using eve"]
    assert "Austrian" in loaded["card"]


def test_a_named_voice_is_kept_and_an_unknown_one_falls_back(tmp_path, monkeypatch):
    import core.realtime_voice.voice_card as cards
    release_with(tmp_path / "0.9.1", {
        "schema": 1,
        "card": "Master Craftsman.",
        "voices": {"openai": "cedar", "xai": "not-a-voice"},
    })
    monkeypatch.setattr(cards, "RELEASES", tmp_path)
    loaded = load_card("0.9.1")
    assert loaded["voices"]["openai"] == "cedar"
    assert loaded["voices"]["xai"] == "eve"
    assert loaded["notices"] == ["xai voice is unset, using eve"]


def test_the_card_is_refused_unless_its_hash_is_the_manifests(tmp_path, monkeypatch):
    import core.realtime_voice.voice_card as cards
    monkeypatch.setattr(cards, "RELEASES", tmp_path)
    release_with(tmp_path / "0.9.2", {"schema": 1, "card": "Master Craftsman.", "voices": {}})
    (tmp_path / "0.9.2" / "voice.json").write_text(json.dumps({"schema": 1, "card": "Someone else.", "voices": {}}))
    with pytest.raises(ValueError, match="manifest"):
        load_card("0.9.2")
    release_with(tmp_path / "0.9.3", {"schema": 1, "card": "Master Craftsman.", "voices": {}})
    (tmp_path / "0.9.3" / "manifest.json").write_text(json.dumps({"files": {}}))      # not listed: refused too
    with pytest.raises(ValueError, match="manifest"):
        load_card("0.9.3")


def test_the_candidate_release_is_listed_and_verified():
    manifest = json.loads((REPO / "services/mc_operations/personas/mc-chat/releases/0.1.18/manifest.json").read_text())
    assert "voice.json" in manifest["files"]
    assert load_card("0.1.18")["card"].startswith("Master Craftsman, Robert's colleague in the work.")


def test_a_header_release_carries_the_voice_card_and_its_hash(tmp_path):
    script = tmp_path / "scripts/mc_persona/make_header_release.py"
    script.parent.mkdir(parents=True)
    shutil.copyfile(REPO / "scripts/mc_persona/make_header_release.py", script)
    releases = tmp_path / "services/mc_operations/personas/mc-chat/releases"
    shutil.copytree(REPO / "services/mc_operations/personas/mc-chat/releases/0.1.18", releases / "0.1.18")
    subprocess.run([sys.executable, "-I", str(script), "0.1.18", "0.1.99", "test"], check=True, capture_output=True)
    made = json.loads((releases / "0.1.99/manifest.json").read_text())
    assert made["files"]["voice.json"] == hashlib.sha256((releases / "0.1.18/voice.json").read_bytes()).hexdigest()
    assert (releases / "0.1.99/voice.json").read_bytes() == (releases / "0.1.18/voice.json").read_bytes()
