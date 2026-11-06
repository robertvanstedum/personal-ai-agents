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


def _apply_refuses_ownership(root):
    """This host cannot chown to the container ids. Apply must fail closed after writing."""
    with pytest.raises(SystemExit, match="uid 1000 gid 1000"):
        setup.apply(root)


def test_apply_makes_private_folders_and_secrets(tmp_path):
    _apply_refuses_ownership(tmp_path)
    for rel in setup.FOLDERS:
        assert stat.S_IMODE((tmp_path / rel).stat().st_mode) == 0o700
    for rel in setup.GENERATED:
        path = tmp_path / rel
        assert stat.S_IMODE(path.stat().st_mode) == 0o600 and len(path.read_text()) >= 32
    report = setup.report(tmp_path)
    token = f"{setup.SECRETS}/cos-actions-token/token"
    assert report["generated"][token] == "unsafe"
    assert report["owners"]["node"] == {"uid": 1000, "gid": 1000}


def test_apply_never_prints_a_value(tmp_path, capsys):
    with pytest.raises(SystemExit):
        setup.main(["apply", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert all((tmp_path / rel).read_text() not in out for rel in setup.GENERATED)


def test_apply_never_overwrites_and_pairs_match(tmp_path):
    _apply_refuses_ownership(tmp_path)
    first = {rel: (tmp_path / rel).read_text() for rel in setup.GENERATED}
    paired = {rel for pair in setup.ASSERTION_PAIRS for rel in pair}
    for left, right in setup.ASSERTION_PAIRS:
        assert first[left] == first[right]
        assert first[left].count(":") == 1 and len(first[left].split(":", 1)[1].strip()) >= 32
    singles = [text for rel, text in first.items() if rel not in paired]
    assert len(singles) == len(set(singles))
    with pytest.raises(SystemExit, match="unsafe"):
        setup.apply(tmp_path)
    assert {rel: (tmp_path / rel).read_text() for rel in setup.GENERATED} == first


def test_apply_writes_nothing_an_operator_must_bind(tmp_path):
    _apply_refuses_ownership(tmp_path)
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


@pytest.mark.parametrize("root", ["/opt/minimoi", "/opt/minimoi/", "/opt/minimoi/.", "/opt/minimoi/../minimoi"])
def test_applying_to_production_needs_the_operators_word(monkeypatch, root):
    monkeypatch.delenv("MINIMOI_1_2_SETUP_APPROVED", raising=False)
    with pytest.raises(SystemExit, match="MINIMOI_1_2_SETUP_APPROVED"):
        setup.main(["apply", "--root", root])


def test_a_root_symlink_is_refused_before_resolve_and_writes_nothing(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(SystemExit, match="symlink"):
        setup.apply(link)
    assert list(target.iterdir()) == []


def test_a_symlink_above_the_root_is_refused_before_any_write(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(SystemExit, match="symlink"):
        setup.apply(link / "minimoi")
    assert list(real.iterdir()) == []


def test_a_symlink_ancestor_is_refused_before_any_write(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    (root / "secrets").symlink_to(outside)
    with pytest.raises(SystemExit, match="unsafe"):
        setup.apply(root)
    assert list(outside.iterdir()) == []
    assert not (root / "cos").exists()


def test_a_half_pair_or_a_bad_key_is_refused(tmp_path):
    left, right = setup.ASSERTION_PAIRS[0]
    (tmp_path / left).parent.mkdir(parents=True)
    (tmp_path / left).write_text("kid:" + ("s" * 40) + "\n")
    os.chmod(tmp_path / left, 0o600)
    with pytest.raises(SystemExit, match="unsafe"):
        setup.apply(tmp_path)
    assert not (tmp_path / right).exists()
    assert (tmp_path / left).read_text().startswith("kid:")
    assert not (tmp_path / setup.FOLDERS[0]).exists()


def test_a_private_binding_the_node_consumer_cannot_read_is_unsafe(tmp_path):
    rel = f"{setup.SECRETS}/cos-bound/bindings.json"
    path = tmp_path / rel
    path.parent.mkdir(parents=True, mode=0o711)
    (tmp_path / "secrets").chmod(0o711)
    (tmp_path / setup.SECRETS).chmod(0o711)
    path.write_text("{}\n")
    os.chmod(path, 0o600)
    assert setup.status(tmp_path, rel, directory=False) == "unsafe"
    assert setup.report(tmp_path)["by_operator"][rel] == "unsafe"
    with pytest.raises(SystemExit, match="unsafe"):
        setup.apply(tmp_path)
    assert path.read_text() == "{}\n"
    assert not (tmp_path / setup.FOLDERS[0]).exists()


def test_assertion_pairs_load_and_sign(tmp_path):
    from core.portal_assertion import load_key, sign, verify
    _apply_refuses_ownership(tmp_path)
    body = b"{}"
    now = 1_700_000_000
    for left, right in setup.ASSERTION_PAIRS:
        key = load_key(tmp_path / left)
        other = load_key(tmp_path / right)
        assert key and other and key == other
        token = sign(key, audience="cos-owner", subject="owner:robert", method="POST",
                     target="/chat", body=body, env="prod", now=now, nonce="n" * 16)
        assert verify(token, other, audience="cos-owner", env="prod", method="POST",
                      target="/chat", body=body, now=now, spend=lambda nonce, exp: True) == "owner:robert"


def test_root_owned_private_paths_are_not_readable_by_another_uid():
    class Info:
        def __init__(self, mode, uid):
            self.st_mode = mode
            self.st_uid = uid
            self.st_gid = 0
    assert not setup.readable_by(Info(stat.S_IFDIR | 0o700, 0), 1000, 1000)
    assert setup.readable_by(Info(stat.S_IFDIR | 0o700, 1000), 1000, 1000)
    assert not setup.readable_by(Info(stat.S_IFREG | 0o600, 0), 1000, 1000)
    assert setup.readable_by(Info(stat.S_IFREG | 0o600, 1000), 1000, 1000)
    assert setup.readable_by(Info(stat.S_IFDIR | 0o711, 0), 1000, 1000)


def test_layer_secret_folders_are_all_provisioned():
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    layer = (root / "docker-compose.minimoi-1-2-production.yml").read_text()
    folders = re.findall(r'MINIMOI_1_2_SECRETS:\?[^}]*\}/([a-z-]+)', layer)
    assert folders and all(f"{setup.SECRETS}/{name}" in setup.FOLDERS for name in folders)
    states = re.findall(r'MINIMOI_1_2_STATE:\?[^}]*\}/([a-z-]+)', layer)
    assert states and all(f"{setup.STATE}/{name}" in setup.FOLDERS for name in states)
