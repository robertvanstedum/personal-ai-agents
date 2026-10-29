"""Synthetic Anki import contracts: no real Anki calls or owner files."""
import importlib.util
import json
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

from domains.german import anki_import

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = [ROOT / "domains/german/import_cards.py",
           ROOT / "domains/german/data/utilities/anki_import/import_cards.py"]


@pytest.fixture(params=SCRIPTS, ids=["current", "legacy"])
def importer(request, monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("anki_test_" + request.param.parent.name, request.param)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ANKI_DIR", tmp_path / "anki")
    monkeypatch.setattr(module, "TRACKER", tmp_path / "imported_files.txt")
    module.ANKI_DIR.mkdir()
    # A missing stub is an immediate test failure, never a local Anki request.
    monkeypatch.setattr(module, "ankiconnect", lambda *a, **kw: pytest.fail("unstubbed Anki request"))
    return module


def csv_file(importer, name="new.csv", rows="Hallo,Hello — explanation,one two\n"):
    path = importer.ANKI_DIR / name
    path.write_text("Front,Back,Tags\n" + rows, encoding="utf-8")
    return path


def test_csv_output_and_entrypoint_overrides(importer, monkeypatch, capsys):
    path = csv_file(importer, rows='"Hallo, Welt",Hello — extra,one two\nbroken,row\n')
    monkeypatch.setattr(importer, "DECK_NAME", "Synthetic")
    monkeypatch.setattr(importer, "MODEL_NAME", "TestBasic")
    assert importer.parse_csv(path) == [{"deckName": "Synthetic", "modelName": "TestBasic",
        "fields": {"Front": "Hallo, Welt", "Back": "Hello"}, "tags": ["one", "two"]}]
    assert "row 3: expected 3 columns, got 2" in capsys.readouterr().out
    monkeypatch.setattr(importer, "clean_back", lambda text: "overridden")
    assert importer.parse_csv(path)[0]["fields"]["Back"] == "overridden"


def test_header_only_and_empty_file_keep_existing_behavior(importer):
    path = csv_file(importer, rows="")
    assert importer.parse_csv(path) == []
    path.write_text("")
    with pytest.raises(StopIteration):
        importer.parse_csv(path)


def test_tracker_and_no_pending_have_no_network_effect(importer, capsys):
    assert importer.load_tracker() == set()
    importer.main()
    assert capsys.readouterr().out == "Nothing new to import.\n"
    assert not importer.TRACKER.exists()
    importer.mark_imported("old.csv")
    assert importer.load_tracker() == {"old.csv"}


def test_import_loop_tracks_only_nonempty_results_and_preserves_hooks(importer, monkeypatch, capsys):
    csv_file(importer, "b.csv")
    csv_file(importer, "a.csv")
    calls = []
    monkeypatch.setattr(importer, "ankiconnect", lambda action, **kw:
                        calls.append((action, kw)) or {"result": [101, None]})
    importer.main()
    assert len(calls) == 2 and all(c[0] == "addNotes" for c in calls)
    assert importer.TRACKER.read_text() == "a.csv\nb.csv\n"
    assert "Imported 2 file(s), 2 card(s) added." in capsys.readouterr().out
    importer.main()
    assert len(calls) == 2
    assert capsys.readouterr().out == "Nothing new to import.\n"


@pytest.mark.parametrize("result", [[], None])
def test_no_result_does_not_mark_imported(importer, monkeypatch, result, capsys):
    csv_file(importer)
    monkeypatch.setattr(importer, "ankiconnect", lambda *a, **kw: {"result": result})
    importer.main()
    assert not importer.TRACKER.exists()
    assert "Not marked as imported." in capsys.readouterr().out


def test_duplicates_are_still_marked_as_imported(importer, monkeypatch):
    csv_file(importer)
    monkeypatch.setattr(importer, "ankiconnect", lambda *a, **kw: {"result": [None]})
    importer.main()
    assert importer.load_tracker() == {"new.csv"}


def test_parse_failure_does_not_mark_imported(importer):
    csv_file(importer).write_text("")
    with pytest.raises(StopIteration):
        importer.main()
    assert not importer.TRACKER.exists()


def test_transport_payload_timeout_and_error(monkeypatch, capsys):
    calls = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'{"result": [17], "error": null}'
    def open_stub(request, timeout):
        calls.append((request.full_url, json.loads(request.data), timeout))
        return Response()
    monkeypatch.setattr(anki_import.urllib.request, "urlopen", open_stub)
    assert anki_import.ankiconnect("http://synthetic.invalid", "addNotes", notes=[]) == {"result": [17], "error": None}
    assert calls == [("http://synthetic.invalid", {"action": "addNotes", "version": 6, "params": {"notes": []}}, 10)]
    def refused(*args, **kwargs): raise urllib.error.URLError("synthetic failure")
    monkeypatch.setattr(anki_import.urllib.request, "urlopen", refused)
    with pytest.raises(SystemExit) as error:
        anki_import.ankiconnect("http://synthetic.invalid", "addNotes", notes=[])
    assert error.value.code == 1
    assert "AnkiConnect unreachable" in capsys.readouterr().out


def test_legacy_import_is_stdlib_only_from_another_directory(tmp_path):
    code = """
import runpy, sys
module = runpy.run_path(sys.argv[1], run_name='import_only')
assert module['clean_back']('Hello — note') == 'Hello'
assert 'german_domain' not in sys.modules
assert 'flask' not in sys.modules
"""
    result = subprocess.run([sys.executable, "-I", "-S", "-c", code, str(SCRIPTS[1])],
                            cwd=tmp_path, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
