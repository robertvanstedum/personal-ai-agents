"""MiniMoi 1.2's production setup: check changes nothing; apply never overwrites, never prints a value."""
import json
import os
import stat

import pytest

from scripts.operations import minimoi_1_2_prod_setup as setup


def _tree(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def test_check_changes_nothing_and_reports_everything_missing(tmp_path, capsys):
    assert setup.main(["check", "--root", str(tmp_path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert _tree(tmp_path) == []
    assert set(out["folders"].values()) == {"missing"} and set(out["generated"].values()) == {"missing"}
    assert out["by_hand"]


def test_apply_makes_private_folders_and_secrets(tmp_path, capsys):
    setup.main(["apply", "--root", str(tmp_path)])
    for rel in setup.FOLDERS:
        assert stat.S_IMODE((tmp_path / rel).stat().st_mode) == 0o700
    for rel in setup.GENERATED:
        path = tmp_path / rel
        assert stat.S_IMODE(path.stat().st_mode) == 0o600 and len(path.read_text()) >= 32


def test_apply_never_prints_a_value(tmp_path, capsys):
    setup.main(["apply", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert all((tmp_path / rel).read_text() not in out for rel in setup.GENERATED)


def test_apply_never_overwrites_and_each_secret_is_its_own(tmp_path):
    setup.apply(tmp_path)
    first = {rel: (tmp_path / rel).read_text() for rel in setup.GENERATED}
    assert len(set(first.values())) == len(first)
    assert setup.apply(tmp_path)["made"] == []
    assert {rel: (tmp_path / rel).read_text() for rel in setup.GENERATED} == first


def test_apply_writes_nothing_an_operator_must_bind(tmp_path):
    setup.apply(tmp_path)
    assert all(not (tmp_path / rel).exists() for rel in setup.BY_OPERATOR)


@pytest.mark.parametrize("kind", ["open_mode", "symlink"])
def test_unsafe_paths_stop_apply_before_any_change(tmp_path, kind):
    rel = setup.FOLDERS[-1]
    target = tmp_path / rel
    target.parent.mkdir(parents=True)
    if kind == "open_mode":
        target.mkdir(mode=0o755)
        target.chmod(0o755)
    else:
        os.symlink("/tmp", target)
    with pytest.raises(SystemExit, match="unsafe"):
        setup.apply(tmp_path)
    assert not (tmp_path / setup.FOLDERS[0]).exists()


def test_applying_to_production_needs_the_operators_word(monkeypatch):
    monkeypatch.delenv("MINIMOI_1_2_SETUP_APPROVED", raising=False)
    with pytest.raises(SystemExit, match="MINIMOI_1_2_SETUP_APPROVED"):
        setup.main(["apply"])


def test_layer_secret_folders_are_all_provisioned():
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    layer = (root / "docker-compose.minimoi-1-2-production.yml").read_text()
    folders = re.findall(r'MINIMOI_1_2_SECRETS:\?[^}]*\}/([a-z-]+)', layer)
    assert folders and all(f"{setup.SECRETS}/{name}" in setup.FOLDERS for name in folders)
    states = re.findall(r'MINIMOI_1_2_STATE:\?[^}]*\}/([a-z-]+)', layer)
    assert states and all(f"{setup.STATE}/{name}" in setup.FOLDERS for name in states)
