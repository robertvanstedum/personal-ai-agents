"""Thinking Store T1 (v0.7 Unit 5): portable tools against a synthetic shelf written by the real shelf writer.

The snapshot in tests/vault_t1/snapshot is invented data. Tests copy it before touching it."""
from __future__ import annotations

import ast
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from core.vault_t1 import portable, reader                        # noqa: E402
from core.vault_t1.reader import Vault, VaultError              # noqa: E402

SNAPSHOT = REPO / "tests/vault_t1/snapshot"
CLI = str(REPO / "scripts/vault/vault.py")
MANDATE = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


@pytest.fixture
def shelf(tmp_path) -> Path:
    dest = tmp_path / "shelf"
    shutil.copytree(SNAPSHOT, dest)
    return dest


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        digest.update(p.relative_to(root).as_posix().encode())
        if p.is_file():
            digest.update(p.read_bytes())
    return digest.hexdigest()


def by_title(vault: Vault, word: str):
    return next(r for r in vault.records() if word in r.stem)


# ── reading ────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_list_shows_what_the_caller_may_read_and_only_counts_the_rest(shelf):
    doc = Vault(str(shelf)).list()
    assert len(doc["records"]) == 4 and doc["withheld_by_scope"] == 1 and doc["unreadable"] == 0
    flat = json.dumps(doc)
    assert "orchard" not in flat.lower() and MANDATE not in flat                       # no title, source or ID of what is withheld
    garden = next(r for r in doc["records"] if r["title"] == "garden-planning")
    assert garden["editions"] == [1, 2] and garden["edition"] == 2 and garden["turns"] == 6 and garden["chair"] == "Claude Code"


def test_a_mandate_record_appears_only_when_that_mandate_is_named(shelf):
    plain, mandated = Vault(str(shelf)), Vault(str(shelf), (MANDATE,))
    assert len(mandated.records()) == 5 and mandated.withheld() == 0 and plain.withheld() == 1
    assert Vault(str(shelf), ("01ARZ3NDEKTSV4RRFFQ69G5FAW",)).withheld() == 1           # a different mandate opens nothing
    secret = next(r for r in mandated.records() if "orchard" in r.stem)
    with pytest.raises(reader.NotFound) as hidden:
        plain.open(secret.id)
    with pytest.raises(reader.NotFound) as absent:
        plain.open("01ZZZZZZZZZZZZZZZZZZZZZZZZ")
    assert hidden.value.code == absent.value.code == "record_not_found"                  # withheld looks exactly like absent
    assert mandated.open(secret.id)[2]


def test_topics_and_coverage_and_decisions(shelf):
    vault = Vault(str(shelf))
    topics = vault.topics()
    assert topics["workshop_topics"] == {"garden-build": 5} and topics["tags"]["claude-code"] == 1 and topics["withheld_by_scope"] == 1
    cov = vault.coverage()
    assert cov["totals"]["records"] == 4 and cov["totals"]["turns"] == 6 + 3 + 2 + 5 and cov["totals"]["turns_by_role"]["coordination"] == 6
    dec = vault.decisions()
    kinds = [d["record_event_kind"] for d in dec["workshop_decisions"]]
    assert kinds == ["proposed", "approved-direct"] and dec["workshop_decisions"][1]["supersedes"] == dec["workshop_decisions"][0]["event_id"]
    assert dec["workshop_decisions"][1]["resolves"] == [dec["workshop_decisions"][0]["event_id"]] and dec["workshop_decisions"][1]["cites_authority"] is True
    assert all(e["kind"] != "edition-added" for e in dec["memory_events"])


def test_open_returns_the_exact_retained_bytes_of_any_edition(shelf):
    vault = Vault(str(shelf))
    garden = by_title(vault, "garden")
    _, e1, one = vault.open(garden.id, 1)
    _, e2, two = vault.open(garden.id)
    assert e1.number == 1 and e2.number == 2 and one != two
    assert one == (garden.path.parent / "editions" / e1.name).read_bytes() and hashlib.sha256(two).hexdigest() == garden.meta["edition_hash"]
    assert vault.find(garden.id[-8:]).id == garden.id and vault.find(garden.id[:10].lower()).id == garden.id
    with pytest.raises(reader.NotFound):
        vault.open(garden.id, 9)
    with pytest.raises(reader.NotFound):
        vault.find("01")                                                                   # too short to be a reference


def test_a_damaged_edition_is_refused_not_served(shelf):
    garden = by_title(Vault(str(shelf)), "garden")
    path = garden.path.parent / "editions" / garden.current().name
    path.write_bytes(path.read_bytes().replace(b"cedar", b"CEDAR"))
    with pytest.raises(reader.Damaged) as caught:
        Vault(str(shelf)).open(garden.id)
    assert caught.value.code == "edition_hash_mismatch"


def test_search_is_literal_bounded_and_says_where(shelf):
    vault = Vault(str(shelf))
    hit = vault.search("cedar")["hits"]
    garden_id = by_title(vault, "garden").id
    assert {h["edition"] for h in hit if h["record"] == garden_id} == {2} and all("cedar" in h["snippet"].lower() for h in hit)
    assert [h["ordinal"] for h in vault.search("zymurgy")["hits"]] == [1]
    assert vault.search("CEDAR")["hits"] == [] and len(vault.search("CEDAR", ignore_case=True)["hits"]) >= 2
    assert vault.search("a.b")["hits"] == [] and vault.search(".*")["hits"] == []                # no regular expressions
    assert {h["edition"] for h in vault.search("cedar", all_editions=True)["hits"] if h["record"] == garden_id} == {1, 2}
    coord = vault.search("Cedar it is")["hits"]
    assert coord and coord[0]["speaker"] == "coordination" and coord[0]["who"] == "robert"      # coordination is findable here, by choice
    assert len(vault.search("e", limit=3)["hits"]) == 3 and vault.search("e", limit=3)["truncated"] is True
    assert vault.search("budget")["hits"] == []                                                   # the mandate record is out of reach
    with pytest.raises(VaultError):
        vault.search("")


def test_links_folders_and_oversized_files_are_not_read(shelf):
    garden = by_title(Vault(str(shelf)), "garden")
    folder = garden.path.parent / "editions"
    victim = shelf / "outside.jsonl"
    victim.write_text("{}\n")
    (folder / "3--aaaaaaaaaaaa.jsonl").symlink_to(victim)
    assert [e.number for e in by_title(Vault(str(shelf)), "garden").editions] == [1, 2]
    (shelf / "sessions-raw" / "linked--00000000").symlink_to(garden.path.parent)
    assert len(Vault(str(shelf)).records()) == 4
    with pytest.raises(reader.Damaged):
        reader.read_file(victim, 1)                                                              # over the bound
    link = shelf / "x.md"
    link.symlink_to(victim)
    with pytest.raises(reader.Damaged) as caught:
        reader.read_file(link, 1 << 20)
    assert caught.value.code == "not_a_regular_file"


# ── export / verify / restore ──────────────────────────────────────────────────────────────────────────────────────
def test_export_a_topic_writes_a_self_describing_folder_that_verifies(shelf, tmp_path):
    out = tmp_path / "topic-export"
    manifest = portable.export(Vault(str(shelf)), str(out), topic="garden-build")
    assert manifest["counts"] == {"records": 1, "editions": 1, "turns_indexed": 5}
    assert manifest["left_out"] == {"outside_scope": 1, "outside_topic": 3}
    assert {p.name for p in out.iterdir()} == {"manifest.json", "manifest.sha256", "SCHEMA.md", "index.jsonl", "terms.json", "records"}
    assert "vault-export/1" in (out / "SCHEMA.md").read_text() and "needs no MiniMoi" in (out / "SCHEMA.md").read_text()
    index = [json.loads(l) for l in (out / "index.jsonl").read_text().splitlines()]
    assert [r["ordinal"] for r in index] == [1, 2, 3, 4, 5] and {r["speaker"] for r in index} == {"coordination"}
    terms = json.loads((out / "terms.json").read_text())
    assert terms["cedar"] and all(":" in p for p in terms["cedar"])
    assert portable.verify(str(out))["ok"] is True
    assert oct(out.stat().st_mode & 0o777) == "0o700" and oct((out / "manifest.json").stat().st_mode & 0o777) == "0o600"


def test_export_all_leaves_out_what_is_out_of_scope_and_a_missing_topic_is_an_error(shelf, tmp_path):
    m = portable.export(Vault(str(shelf)), str(tmp_path / "all"))
    assert m["counts"]["records"] == 4 and m["left_out"]["outside_scope"] == 1 and all("mandate" not in r["scope"] for r in m["records"])
    with pytest.raises(reader.NotFound):
        portable.export(Vault(str(shelf)), str(tmp_path / "none"), topic="no-such-topic")
    m2 = portable.export(Vault(str(shelf), (MANDATE,)), str(tmp_path / "with-mandate"))
    assert m2["counts"]["records"] == 5 and m2["selection"]["scope"].endswith(f"mandate:{MANDATE}")


def test_export_refuses_a_target_that_exists_and_has_content(shelf, tmp_path):
    target = tmp_path / "busy"
    target.mkdir()
    (target / "keep.txt").write_text("mine")
    with pytest.raises(VaultError) as caught:
        portable.export(Vault(str(shelf)), str(target))
    assert caught.value.code == "target_not_empty" and (target / "keep.txt").read_text() == "mine"


def refresh_manifest(export: Path, data: dict) -> None:
    raw = (json.dumps(data, sort_keys=True) + "\n").encode()
    (export / "manifest.json").write_bytes(raw)
    (export / "manifest.sha256").write_text(hashlib.sha256(raw).hexdigest() + "  manifest.json\n")


def tampered(export: Path, how: str) -> None:
    edition = next((export / "records").rglob("*.jsonl"))
    manifest = export / "manifest.json"
    if how == "edition":
        edition.write_bytes(edition.read_bytes().replace(b"cedar", b"CEDAR"))
    elif how == "edition_missing":
        edition.unlink()
    elif how == "extra_file":
        (export / "records" / "smuggled.txt").write_text("not in the manifest")
    elif how == "manifest":
        manifest.write_text(manifest.read_text().replace('"records": 1', '"records": 2'))
    elif how == "manifest_and_side":
        refresh_manifest(export, {**json.loads(manifest.read_text()), "format": "vault-export/9"})
    elif how == "unsafe_path":
        data = json.loads(manifest.read_text())
        data["files"][0]["path"] = "../escape.txt"
        refresh_manifest(export, data)
    elif how == "symlinked_record":
        rec = next((export / "records").glob("*/*.md"))
        rec.unlink()
        rec.symlink_to(edition)
    elif how == "turn_text":                                       # the file's own hash and name are fixed up; the turn's hash is not
        text = edition.read_bytes().replace(b"Cedar it is; pine is rejected.", b"Pine it is; cedar is rejected.")
        new = hashlib.sha256(text).hexdigest()[:12]
        target = edition.with_name(re.sub(r"--[0-9a-f]{12}", "--" + new, edition.name))
        edition.unlink()
        target.write_bytes(text)
        data = json.loads(manifest.read_text())
        for f in data["files"]:
            if f["path"].endswith(edition.name):
                f["path"] = f["path"].replace(edition.name, target.name)
                f["sha256"], f["bytes"] = hashlib.sha256(text).hexdigest(), len(text)
        for r in data["records"]:
            for e in r["editions"]:
                if e["path"].endswith(edition.name):
                    e["path"] = e["path"].replace(edition.name, target.name)
        refresh_manifest(export, data)


@pytest.mark.parametrize("how", ["edition", "edition_missing", "extra_file", "manifest", "manifest_and_side", "unsafe_path", "symlinked_record", "turn_text"])
def test_V04_hash_and_scope_a_tampered_export_fails_verify_and_restore_writes_nothing(shelf, tmp_path, how):
    export = tmp_path / "export"
    portable.export(Vault(str(shelf)), str(export), topic="garden-build")
    assert portable.verify(str(export))["ok"] is True
    tampered(export, how)
    report = portable.verify(str(export))
    assert report["ok"] is False and report["problems"], how
    target = tmp_path / "restored"
    with pytest.raises(reader.Damaged):
        portable.restore(str(export), str(target))
    assert not target.exists(), how                                                                # nothing written for a failing export


def test_verify_a_shelf_finds_every_kind_of_damage(shelf):
    assert portable.verify(str(shelf))["ok"] is True
    garden = by_title(Vault(str(shelf)), "garden")
    editions = garden.path.parent / "editions"
    first = next(editions.glob("1--*"))
    original = first.read_bytes()
    first.write_bytes(original.replace(b"tomatoes", b"potatoes"))
    assert "edition_hash_mismatch" in {p["code"] for p in portable.verify(str(shelf))["problems"]}
    first.write_bytes(original)
    second = next(editions.glob("2--*"))
    second.rename(editions / ("3--" + second.name.split("--")[1]))
    assert {"edition_numbers_not_contiguous", "current_edition_missing"} <= {p["code"] for p in portable.verify(str(shelf))["problems"]}


def test_restore_rebuilds_a_clean_shelf_with_the_same_ids_editions_and_decision_trail(shelf, tmp_path):
    export, target = tmp_path / "export", tmp_path / "clean"
    portable.export(Vault(str(shelf)), str(export))
    result = portable.restore(str(export), str(target))
    assert result["restored_records"] == 4 and result["target_verified"] is True
    before, after = Vault(str(shelf)), Vault(str(target))
    assert [(r.id, r.meta["edition"], r.meta["edition_hash"]) for r in before.records()] == [(r.id, r.meta["edition"], r.meta["edition_hash"]) for r in after.records()]
    for a, b in zip(before.records(), after.records()):
        assert [(e.number, e.name) for e in a.editions] == [(e.number, e.name) for e in b.editions]
        assert a.raw == b.raw
    assert before.decisions() == after.decisions()
    with pytest.raises(VaultError):
        portable.restore(str(export), str(target))                                                # no overwriting a restored shelf


# ── V01: the exit test ─────────────────────────────────────────────────────────────────────────────────────────────
GUARD = r"""
import runpy, socket, sys
def refuse(*a, **k):
    raise RuntimeError("network is not available in the exit test")
socket.socket.connect = refuse
socket.create_connection = refuse
for banned in ("anthropic", "openai", "litellm", "requests", "httpx", "aiohttp", "urllib3"):
    sys.modules[banned] = None            # an import of any of these now fails
sys.argv = ["vault.py"] + sys.argv[1:]
runpy.run_path(%r, run_name="__main__")
"""


def exit_cli(*args):
    env = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"}                              # no HOME, no keys, no provider settings
    return subprocess.run([sys.executable, "-c", GUARD % CLI, *args], capture_output=True, env=env, cwd="/", timeout=120)


def test_V01_exit_test_clean_disconnected_environment_no_minimoi_no_model(shelf, tmp_path):
    export, clean = tmp_path / "export", tmp_path / "clean"
    assert exit_cli("--root", str(shelf), "export", "--to", str(export)).returncode == 0
    verify = exit_cli("verify", str(export))
    assert verify.returncode == 0 and json.loads(verify.stdout)["ok"] is True
    restored = exit_cli("restore", str(export), "--to", str(clean))
    assert restored.returncode == 0 and json.loads(restored.stdout)["target_verified"] is True
    listed = json.loads(exit_cli("--root", str(clean), "list").stdout)
    assert [r["title"] for r in listed["records"]] == ["naming-the-compost-heap", "garden-planning", "irrigation-review", "workshop-2026-10-08"]
    decisions = json.loads(exit_cli("--root", str(clean), "decisions").stdout)
    assert [d["record_event_kind"] for d in decisions["workshop_decisions"]] == ["proposed", "approved-direct"]
    garden = next(r for r in listed["records"] if r["title"] == "garden-planning")
    opened = exit_cli("--root", str(clean), "open", garden["id"], "--edition", "1")
    expected = next((clean / "sessions-raw").glob("garden-planning--*/editions/1--*")).read_bytes()
    assert opened.returncode == 0 and opened.stdout == expected                                     # exact retained bytes, provenance in the header
    assert json.loads(opened.stdout.splitlines()[0])["source_sha256"]
    hits = json.loads(exit_cli("--root", str(clean), "search", "zymurgy").stdout)["hits"]
    assert hits and "zymurgy" in hits[0]["snippet"] and hits[0]["source"] == "claude-ai:synthetic-export-1"
    topic = exit_cli("--root", str(clean), "export", "--to", str(tmp_path / "topic"), "--topic", "garden-build")
    assert topic.returncode == 0 and (tmp_path / "topic" / "SCHEMA.md").read_text().startswith("# Vault export")
    assert b"Traceback" not in verify.stderr + restored.stderr + opened.stderr


def test_the_cli_reports_errors_as_one_object_and_exit_codes(shelf, tmp_path):
    missing = exit_cli("--root", str(shelf), "open", "01ZZZZZZZZZZZZZZZZZZZZZZZZ")
    assert missing.returncode == 8 and json.loads(missing.stdout)["reason"] == "record_not_found"
    assert exit_cli("list").returncode == 2
    bad = tmp_path / "bad"
    portable.export(Vault(str(shelf)), str(bad))
    next((bad / "records").rglob("*.jsonl")).write_bytes(b"x")
    assert exit_cli("verify", str(bad)).returncode == 5


def test_V01_the_tools_import_no_model_network_or_application_code():
    banned_exact = {"core.memory_shelf", "core.workshop_journal", "minimoi_portal"}
    banned_root = {"socket", "ssl", "http", "urllib", "requests", "httpx", "aiohttp", "anthropic", "openai", "litellm", "subprocess", "sqlite3"}
    for path in [*(REPO / "core/vault_t1").glob("*.py"), REPO / "scripts/vault/vault.py"]:
        for node in ast.walk(ast.parse(path.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            for name in names:
                assert name not in banned_exact and name.split(".")[0] not in banned_root, (path.name, name)


# ── V03: another reader reproduces the same IDs, editions and trail; nothing is rewritten or widened ──────────────
def independent_reader(root: Path) -> dict:
    """Written here from the format alone, sharing no code with the tools, to stand in for a different client."""
    out = {}
    for md in sorted(root.glob("*/*/*.md")):
        if md.parent.parent.name not in ("sessions-raw", "sessions", "notes", "turns", "snapshots", "briefs"):
            continue
        text = md.read_text()
        meta = yaml.safe_load(text[4:text.index("\n---\n", 4)])
        if meta["scope"] != "robert":
            continue
        editions = {}
        for f in sorted((md.parent / "editions").glob("*.jsonl")):
            n, rest = f.name.split("--")
            editions[int(n)] = hashlib.sha256(f.read_bytes()).hexdigest()
            assert rest.startswith(editions[int(n)][:12])
        trail = []
        for line in next((md.parent / "editions").glob(f"{max(editions)}--*")).read_text().splitlines()[1:]:
            ev = (json.loads(line).get("attrs") or {}).get("event")
            if ev and ev["kind"] == "decision":
                trail.append((ev["event_id"], ev["payload"]["record_event_kind"], ev.get("supersedes")))
        out[meta["id"]] = (meta["source"], editions, trail)
    return out


def test_V03_a_different_reader_sees_the_same_ids_editions_and_trail_and_nothing_is_rewritten(shelf, tmp_path):
    before_tree = tree_hash(shelf)
    vault = Vault(str(shelf))
    mine = {r.id: (r.meta["source"], {e.number: hashlib.sha256(e.path.read_bytes()).hexdigest() for e in r.editions},
                   [(d["event_id"], d["record_event_kind"], d["supersedes"]) for d in vault.decisions()["workshop_decisions"] if d["record"] == r.id])
            for r in vault.records()}
    vault.list(), vault.topics(), vault.coverage(), vault.search("cedar"), vault.open(by_title(vault, "garden").id)
    assert tree_hash(shelf) == before_tree                                                        # reading never writes
    assert independent_reader(shelf) == mine
    export, clean = tmp_path / "e", tmp_path / "c"
    portable.export(vault, str(export))
    portable.restore(str(export), str(clean))
    assert independent_reader(clean) == mine                                                      # the restored shelf is the same record
    assert tree_hash(shelf) == before_tree                                                        # exporting and restoring did not touch the source
    assert Vault(str(clean)).withheld() == 0 and len(Vault(str(clean)).records()) == 4           # no access was widened, no hidden record carried over
    assert not any("orchard" in p.name for p in clean.rglob("*"))                                 # and no second archive of the withheld one
