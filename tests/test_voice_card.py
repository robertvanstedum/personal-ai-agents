"""A person's voice card loads from that person's release folder, only when its hash is the manifest's, never invents a
voice id, and is pinned by data: <root>/pin.json names the release (Spec 165 §4.5). Optional language and accent."""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from core.realtime_voice.voice_card import load_card, load_pinned

REPO = Path(__file__).resolve().parents[1]
MC_ROOT = REPO / "services/mc_operations/personas/mc-chat"


def release_with(root, release, payload, *, pin=False):
    folder = root / "releases" / release
    folder.mkdir(parents=True)
    raw = json.dumps(payload).encode()
    (folder / "voice.json").write_bytes(raw)
    (folder / "manifest.json").write_text(json.dumps({"files": {"voice.json": hashlib.sha256(raw).hexdigest()}}))
    if pin:
        (root / "pin.json").write_text(json.dumps({"release": release}))
    return folder


def test_an_unset_voice_uses_the_provider_default_and_says_so(tmp_path):
    release_with(tmp_path, "0.9.0", {
        "schema": 1,
        "card": "Master Craftsman. English by default. Light Austrian accent.",
        "voices": {},
    })
    loaded = load_card(tmp_path, "0.9.0")
    assert loaded["voices"] == {"openai": "marin", "xai": "eve"}
    assert loaded["notices"] == ["openai voice is unset, using marin", "xai voice is unset, using eve"]
    assert "Austrian" in loaded["card"]
    assert loaded["language"] == "en-US" and loaded["accent"] is None and loaded["release"] == "0.9.0"   # absent: en-US


def test_a_named_voice_is_kept_and_an_unknown_one_falls_back(tmp_path):
    release_with(tmp_path, "0.9.1", {
        "schema": 1,
        "card": "Master Craftsman.",
        "voices": {"openai": "cedar", "xai": "not-a-voice"},
    })
    loaded = load_card(tmp_path, "0.9.1")
    assert loaded["voices"]["openai"] == "cedar"
    assert loaded["voices"]["xai"] == "eve"
    assert loaded["notices"] == ["xai voice is unset, using eve"]


def test_the_card_is_refused_unless_its_hash_is_the_manifests(tmp_path):
    folder = release_with(tmp_path, "0.9.2", {"schema": 1, "card": "Master Craftsman.", "voices": {}})
    (folder / "voice.json").write_text(json.dumps({"schema": 1, "card": "Someone else.", "voices": {}}))
    with pytest.raises(ValueError, match="manifest"):
        load_card(tmp_path, "0.9.2")
    folder = release_with(tmp_path, "0.9.3", {"schema": 1, "card": "Master Craftsman.", "voices": {}})
    (folder / "manifest.json").write_text(json.dumps({"files": {}}))      # not listed: refused too
    with pytest.raises(ValueError, match="manifest"):
        load_card(tmp_path, "0.9.3")


def test_the_pin_names_the_release_and_a_missing_or_bad_pin_is_refused(tmp_path):
    release_with(tmp_path, "0.9.4", {"schema": 1, "card": "Chief of Staff.", "voices": {"openai": "cedar"}}, pin=True)
    assert load_pinned(tmp_path)["card"] == "Chief of Staff." and load_pinned(tmp_path)["release"] == "0.9.4"
    for pin in ({"release": "0.9.9"}, {}, {"release": "0.9.4", "card": "x"}, ["0.9.4"], {"release": 4}):
        (tmp_path / "pin.json").write_text(json.dumps(pin))
        with pytest.raises((OSError, ValueError)):
            load_pinned(tmp_path)
    (tmp_path / "pin.json").write_text("not json")
    with pytest.raises(ValueError):
        load_pinned(tmp_path)
    (tmp_path / "pin.json").unlink()
    with pytest.raises(OSError):
        load_pinned(tmp_path)


def test_a_release_is_a_name_never_a_path(tmp_path):
    release_with(tmp_path, "0.9.5", {"schema": 1, "card": "x.", "voices": {}})
    for release in ("../releases/0.9.5", "0.9.5/", "/etc", "..", "0.9.5\n", "", None):
        with pytest.raises(ValueError, match="release name"):
            load_card(tmp_path, release)
    (tmp_path / "pin.json").write_text(json.dumps({"release": "../../mc-chat/releases/0.1.18"}))
    with pytest.raises(ValueError, match="release name"):
        load_pinned(tmp_path)


def test_language_is_an_allowed_locale_and_accent_a_short_label(tmp_path):
    loaded = load_card(tmp_path, release_with(tmp_path, "0.9.6", {
        "schema": 1, "card": "Chefe de gabinete.", "voices": {}, "language": "pt-BR", "accent": "light Austrian"}).name)
    assert loaded["language"] == "pt-BR" and loaded["accent"] == "light Austrian"
    for n, extra in enumerate(({"language": "fr-FR"}, {"language": None}, {"language": "EN-us"}, {"accent": ""},
                               {"accent": 7}, {"accent": "x" * 81})):
        release = f"0.8.{n}"
        release_with(tmp_path, release, {"schema": 1, "card": "x.", "voices": {}, **extra})
        with pytest.raises(ValueError, match="language|accent"):
            load_card(tmp_path, release)


def test_mcs_card_is_pinned_and_verified():
    manifest = json.loads((MC_ROOT / "releases/0.1.22/manifest.json").read_text())
    assert "voice.json" in manifest["files"]
    assert json.loads((MC_ROOT / "pin.json").read_text()) == {"release": "0.1.22"}
    pinned = load_pinned(MC_ROOT)
    assert pinned["release"] == "0.1.22" and pinned["language"] == "en-US" and pinned["transcription"] == "bilingual"
    assert pinned["card"].startswith("Master Craftsman, Robert's longtime collaborator and friend in the work;")
    assert pinned["voices"] == {"openai": "marin", "xai": "leo"} and pinned["named"] == ["openai", "xai"]   # by ear, 29 Oct
    assert pinned == load_card(MC_ROOT, "0.1.22")


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


# Review R1 P3-11 / R2 N3: a card that matches its hash but has the wrong shapes is refused as ValueError (which every caller
# catches), never AttributeError or TypeError, which would stop a host from starting while direct voice is on.
@pytest.mark.parametrize("payload", [["schema", 1], "a card", {"schema": 1, "card": "x", "language": ["en-US"]},
                                     {"schema": 1, "card": "x", "language": {"en": 1}}, {"schema": 1, "card": "x", "voices": ["marin"]},
                                     # review FR S3: a voice that is not a name
                                     {"schema": 1, "card": "x", "voices": {"openai": ["cedar"]}},
                                     {"schema": 1, "card": "x", "voices": {"xai": {"a": 1}}},
                                     {"schema": 1, "card": "x", "voices": {"openai": 7}}])
def test_a_malformed_card_is_refused_as_value_error(tmp_path, payload):
    release_with(tmp_path, "1.0.0", payload)
    with pytest.raises(ValueError):
        load_card(tmp_path, "1.0.0")


@pytest.mark.parametrize("manifest", [["voice.json"], {"files": ["voice.json"]}, {"files": "voice.json"}])
def test_a_malformed_manifest_is_refused_as_value_error(tmp_path, manifest):
    folder = release_with(tmp_path, "1.0.0", {"schema": 1, "card": "x"})
    (folder / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        load_card(tmp_path, "1.0.0")


def test_a_malformed_card_does_not_stop_the_host_from_starting(tmp_path):
    """install_direct_voice reads the card at install: a malformed one means voice unavailable, not a failed portal start."""
    from flask import Flask
    from core.realtime_voice.direct_host import DirectVoiceAdapter, install_direct_voice
    release_with(tmp_path, "1.0.0", {"schema": 1, "card": "x", "language": ["en-US"]}, pin=True)

    class Host(DirectVoiceAdapter):
        person = {"key": "p", "name": "P", "short": "P", "domain": "p", "notes": ("save a note:",)}
        name = "p_voice"

        def card(self):
            return load_pinned(tmp_path)
    metadata, _sessions = install_direct_voice(Flask("malformed"), Host(), url_prefix="/p", environ={})
    assert metadata["card_release"] is None


# Spec 165 v0.3 §4.5, §7 (A2): the card's transcription ("bilingual", D19), the voices it names itself, and the persona its
# release manifest says the card restates.
def test_transcription_is_absent_or_bilingual(tmp_path):
    assert load_card(tmp_path, release_with(tmp_path, "0.7.0", {"schema": 1, "card": "x.", "voices": {}}).name)[
        "transcription"] is None
    loaded = load_card(tmp_path, release_with(tmp_path, "0.7.1", {"schema": 1, "card": "x.", "transcription": "bilingual"}).name)
    assert loaded["transcription"] == "bilingual"
    for n, value in enumerate(("english", "", ["bilingual"], True, "Bilingual")):
        release_with(tmp_path, f"0.6.{n}", {"schema": 1, "card": "x.", "transcription": value})
        with pytest.raises(ValueError, match="transcription"):
            load_card(tmp_path, f"0.6.{n}")


def test_named_lists_only_the_voices_the_card_names_itself(tmp_path):
    loaded = load_card(tmp_path, release_with(tmp_path, "0.5.0", {"schema": 1, "card": "x.", "voices": {"openai": "cedar"}}).name)
    assert loaded["named"] == ["openai"] and loaded["voices"]["xai"] == "eve"          # the default is still reported
    assert load_card(tmp_path, release_with(tmp_path, "0.5.1", {"schema": 1, "card": "x.", "voices": {
        "openai": "cedar", "xai": "not-a-voice"}}).name)["named"] == ["openai"]
    assert load_card(MC_ROOT, "0.1.18")["named"] == []                                   # MC's card names none (D3)


def test_a_manifest_names_the_persona_the_card_restates_or_nothing(tmp_path):
    folder = release_with(tmp_path, "0.4.0", {"schema": 1, "card": "x."})
    manifest = json.loads((folder / "manifest.json").read_text())
    persona = {"source": "MASTER_CRAFTSMAN_PERSONA.md", "version": "1.1", "sha256": "a" * 64}
    (folder / "manifest.json").write_text(json.dumps({**manifest, "persona": persona}))
    assert load_card(tmp_path, "0.4.0")["persona"] == persona
    (folder / "manifest.json").write_text(json.dumps({**manifest, "persona": "mc-chat"}))   # MC's package name: none
    assert load_card(tmp_path, "0.4.0")["persona"] is None
    assert load_card(MC_ROOT, "0.1.18")["persona"] is None
    for bad in ({**persona, "sha256": "short"}, {"source": "x", "version": "1"}, {**persona, "extra": "x"}, ["x"],
                {**persona, "version": ""}):
        (folder / "manifest.json").write_text(json.dumps({**manifest, "persona": bad}))
        with pytest.raises(ValueError, match="persona"):
            load_card(tmp_path, "0.4.0")
