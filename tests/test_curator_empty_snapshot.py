import importlib.util, pathlib, hashlib, os, subprocess, pytest
P = pathlib.Path
HERE = P(__file__).parent
SOURCE = HERE / 'test_curator_persistence_deploy.py'
s = importlib.util.spec_from_file_location('existing_persistence', SOURCE)
f = importlib.util.module_from_spec(s)
s.loader.exec_module(f)
f.OPS = HERE if (HERE / 'curator_seed_state.sh').exists() else HERE.parent / 'scripts/operations'
f.MAP = f.OPS / 'curator_state.map'

def fixture(tmp):
    host = f._host(tmp)
    snap = tmp / 'snap'
    (snap / 'tree').mkdir(parents=True)
    (snap / 'FILES.sha256').write_bytes(b'')
    (snap / 'SNAPSHOT').write_text('container_id=test\n')
    paths = []
    for kind, old, rel, target in f._map_rows():
        if old == '-':
            continue
        dest = host / rel
        if kind in ('dir', 'appdir'):
            dest.mkdir(parents=True, exist_ok=True)
        elif kind == 'file':
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text('existing durable\n')
        paths.append('mounted ' + old)
    (snap / 'PATHS').write_text('\n'.join(paths) + '\n')
    return (host, snap)

def test_actual_zero_files_all_host_mounted_idempotent(tmp_path):
    host, snap = fixture(tmp_path)
    before = {p.relative_to(host): p.read_bytes() for p in host.rglob('*') if p.is_file()}
    for _ in range(2):
        r = f._run('curator_seed_state.sh', str(snap), host=host)
        assert r.returncode == 0, r.stderr
        assert '(0 new, nothing overwritten)' in r.stdout
    assert before == {p.relative_to(host): p.read_bytes() for p in host.rglob('*') if p.is_file()}

def test_empty_manifest_unlisted_file_refuses_no_host_changes(tmp_path):
    host, snap = fixture(tmp_path)
    (snap / 'tree' / 'hidden').write_text('not listed')
    r = f._run('curator_seed_state.sh', str(snap), host=host)
    assert r.returncode != 0
    assert 'unlisted snapshot entries' in r.stderr

def test_empty_manifest_symlink_refuses(tmp_path):
    host, snap = fixture(tmp_path)
    (snap / 'tree' / 'link').symlink_to(host)
    r = f._run('curator_seed_state.sh', str(snap), host=host)
    assert r.returncode != 0
    assert 'unlisted snapshot entries' in r.stderr

def test_nonempty_malformed_manifest_still_refuses(tmp_path):
    if 'GNU' not in subprocess.run(['sha256sum', '--version'], capture_output=True, text=True).stdout:
        pytest.skip('GNU production sha256sum required; Darwin1.0 accepts zero formatted checksum lines')
    host, snap = fixture(tmp_path)
    (snap / 'FILES.sha256').write_text('not a checksum\n')
    r = f._run('curator_seed_state.sh', str(snap), host=host)
    assert r.returncode != 0
    assert 'does not match its own manifest' in r.stderr

def test_nonempty_corrupt_payload_still_refuses(tmp_path):
    host, snap = fixture(tmp_path)
    (snap / 'tree' / 'x').write_text('changed')
    (snap / 'FILES.sha256').write_text(hashlib.sha256(b'original').hexdigest() + '  ./x\n')
    r = f._run('curator_seed_state.sh', str(snap), host=host)
    assert r.returncode != 0
    assert 'does not match its own manifest' in r.stderr

def test_empty_manifest_nested_file_refuses(tmp_path):
    host, snap = fixture(tmp_path)
    (snap / 'tree' / 'nested').mkdir()
    (snap / 'tree' / 'nested' / 'x').write_text('hidden')
    assert f._run('curator_seed_state.sh', str(snap), host=host).returncode != 0

def test_empty_manifest_special_entry_refuses(tmp_path):
    host, snap = fixture(tmp_path)
    os.mkfifo(snap / 'tree' / 'fifo')
    assert f._run('curator_seed_state.sh', str(snap), host=host).returncode != 0

def test_empty_manifest_wrong_container_refuses(tmp_path):
    host, snap = fixture(tmp_path)
    r = f._run('curator_seed_state.sh', str(snap), host=host, docker=f._fake_docker(tmp_path, FAKE_CONTAINERS='minimoi-curator'))
    assert r.returncode != 0
    assert 'not the current' in r.stderr

def test_empty_manifest_symlink_tree_refuses(tmp_path):
    host, snap = fixture(tmp_path)
    (snap / 'tree').rmdir()
    (snap / 'tree').symlink_to(host)
    assert f._run('curator_seed_state.sh', str(snap), host=host).returncode != 0

def test_empty_manifest_manifest_symlink_refuses(tmp_path):
    host, snap = fixture(tmp_path)
    (snap / 'FILES.sha256').unlink()
    empty = tmp_path / 'empty'
    empty.write_bytes(b'')
    (snap / 'FILES.sha256').symlink_to(empty)
    assert f._run('curator_seed_state.sh', str(snap), host=host).returncode != 0

def test_empty_manifest_find_failure_refuses_before_host_write(tmp_path):
    host, snap = fixture(tmp_path)
    docker = f._fake_docker(tmp_path)
    fakefind = tmp_path / 'fakebin' / 'find'
    fakefind.write_text('#!/bin/bash\nexit 32\n')
    fakefind.chmod(493)
    r = f._run('curator_seed_state.sh', str(snap), host=host, docker=docker)
    assert r.returncode != 0
    assert 'could not inspect empty snapshot tree' in r.stderr
