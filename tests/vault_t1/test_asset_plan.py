"""Selected-file custody planning against real synthetic native asset graphs."""
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import pytest
from core.vault_t1 import assets, asset_plan as planner
from core.vault_t1.reader import VaultError


@pytest.fixture
def env(tmp_path, monkeypatch):
    source = tmp_path / 'source'; source.mkdir(mode=0o700)
    store = tmp_path / 'store'; store.mkdir(mode=0o700)
    (store / '.assets.lock').touch(mode=0o600)
    # Deterministic admission on CI disks; explicit low-space test below uses
    # the real ensure_headroom guard with a lower mocked free-space observation.
    monkeypatch.setattr(assets.shutil, 'disk_usage', lambda _: SimpleNamespace(free=1 << 40))
    return source, store


def native(env, name='review.md', data=b'private synthetic bytes'):
    source, _ = env
    sid = '_working/' + name
    path = source / sid; path.parent.mkdir(mode=0o700, exist_ok=True)
    path.write_bytes(data)
    row = {'path': sid, 'bytes': len(data), 'sha256': assets.sha256(data),
           'authority': 'unknown_pending_source_review', 'kind': 'private_candidate'}
    return row, path


def run(env, rows, **kwargs):
    source, store = env
    return planner.plan({'format': 'working-migration-inventory/1', 'entries': rows},
                        [r['path'] for r in rows], source_root=source,
                        source_instance='synthetic-originals', custody_root=store, **kwargs)


def capture(env, row, path):
    return assets.capture(env[1], path, source_instance='synthetic-originals', source_id=row['path'])


def test_new_and_covered_are_distinct_and_do_not_capture(env):
    row, path = native(env)
    before = sorted(str(p.relative_to(env[1])) for p in env[1].rglob('*'))
    fresh = run(env, [row])
    assert fresh['counts'] == {'missing': 1}
    assert fresh['items'][0]['reason'] == 'custody_missing'
    assert fresh['items'][0]['proposed_capture']['expected_sha256'] == row['sha256']
    assert fresh['captured'] is False and fresh['admitted_for_review'] is True
    assert before == sorted(str(p.relative_to(env[1])) for p in env[1].rglob('*'))
    capture(env, row, path)
    covered = run(env, [row])
    assert covered['counts'] == {'current_matching_covered': 1}
    assert covered['proposed_captures'] == 0


def test_changed_original_and_unhashed_inventory_freeze_current_proof(env):
    row, path = native(env); capture(env, row, path)
    path.write_bytes(b'changed')
    result = run(env, [row])['items'][0]
    assert result['status'] == 'stale'
    assert not result['inventory_matches']
    assert result['proposed_capture']['expected_sha256'] == assets.sha256(b'changed')
    other, _ = native(env, 'unhashed.md', b'new'); other.pop('sha256')
    result = run(env, [other])
    assert result['counts'] == {'unhashed': 1}
    assert result['items'][0]['current_sha256'] == assets.sha256(b'new')
    assert 'not_authorized' in result['authority']


def test_relocated_identity_and_original_old_working_path_are_retained(env):
    row, path = native(env); old = capture(env, row, path)
    moved = env[0] / 'permanent.md'; moved.write_bytes(path.read_bytes()); path.unlink()
    result = run(env, [row], source_paths={row['path']: str(moved)})
    item = result['items'][0]
    assert item['status'] == 'stale' and item['reason'] == 'source_path_changed'
    assert item['asset_id'] == old['asset_id']
    assert item['proposed_capture']['source_id'] == row['path']
    assert result['capacity']['remaining_editions_after_plan'] == assets.MAX_CAPTURES - 2


def test_duplicate_content_preserves_distinct_provenance(env):
    a, _ = native(env, 'a.md'); b, _ = native(env, 'b.md')
    result = run(env, [a, b])
    assert result['proposed_captures'] == 2
    assert result['items'][0]['asset_id'] != result['items'][1]['asset_id']
    assert result['items'][0]['current_sha256'] == result['items'][1]['current_sha256']


def test_only_explicit_selected_source_and_selected_latest_blob_are_read(env, monkeypatch):
    selected, path = native(env); capture(env, selected, path)
    unselected, other = native(env, 'not-selected.md', b'should not be read'); capture(env, unselected, other)
    original = assets.read_stable; seen = []
    def observed(path):
        seen.append(Path(path)); return original(path)
    monkeypatch.setattr(assets, 'read_stable', observed)
    run(env, [selected])
    assert set(seen) == {path, env[1] / assets.object_path(selected['sha256'])}
    assert other not in seen


def test_covered_requires_actual_latest_blob_not_just_metadata(env):
    row, path = native(env); capture(env, row, path)
    blob = env[1] / assets.object_path(row['sha256']); blob.write_bytes(b'!' * blob.stat().st_size)
    with pytest.raises(VaultError, match='asset_plan_(selected_blob_damaged|custody_object_size)'):
        run(env, [row])


def test_missing_unsupported_and_oversize_are_not_silently_dropped(env, monkeypatch):
    a, p = native(env); p.unlink()
    b, _ = native(env, 'unsupported.bin')
    c, p = native(env, 'large.md')
    with p.open('wb') as f:f.truncate(assets.MAX_BYTES + 1)
    monkeypatch.setattr(assets, 'read_stable', lambda _: pytest.fail('invalid selected source read'))
    result = run(env, [a,b,c])
    assert result['counts'] == {'missing':1, 'unsupported':1, 'oversize':1}
    assert not result['admitted_for_review'] and result['selected_files'] == 3


def test_symlink_leaf_parent_and_nonregular_fail_closed(env):
    row, path = native(env); target = env[0] / 'target.md'; target.write_bytes(b'target')
    path.unlink(); path.symlink_to(target)
    assert run(env, [row])['items'][0]['reason'] == 'source_unsafe_or_changed'
    path.unlink(); path.parent.rename(env[0] / 'real-working'); path.parent.symlink_to(env[0] / 'real-working')
    assert run(env, [row])['items'][0]['reason'] == 'source_unsafe_or_changed'
    path.parent.unlink(); path.parent.mkdir(mode=0o700); os.mkfifo(path)
    assert run(env, [row])['items'][0]['reason'] == 'source_unsafe_or_changed'


def test_stable_reader_refusal_is_not_a_pass(env, monkeypatch):
    row, _ = native(env)
    def changed(_):raise VaultError('asset_changed_while_read')
    monkeypatch.setattr(assets, 'read_stable', changed)
    result = run(env, [row])
    assert not result['admitted_for_review']
    assert result['items'][0]['proposed_capture'] is None


def test_selected_byte_and_file_bounds(env, monkeypatch):
    a, _ = native(env, 'a.md', b'aaa'); b, _ = native(env, 'b.md', b'bbb')
    monkeypatch.setattr(planner, 'MAX_BATCH_BYTES', 5)
    result = run(env, [a,b])
    assert result['counts'] == {'missing':1, 'oversize':1}
    assert result['current_selected_bytes'] == 3 and not result['admitted_for_review']
    rows = [{'path':f'_working/{i}.md','bytes':0} for i in range(21)]
    with pytest.raises(VaultError, match='asset_plan_selection_limit'):run(env, rows)


def test_capacity_metadata_and_count_limits_are_not_raised(env, monkeypatch):
    row, path = native(env); capture(env, row, path)
    extra, _ = native(env, 'extra.md')
    monkeypatch.setattr(assets, 'MAX_CAPTURES', 1)
    with pytest.raises(VaultError, match='asset_plan_capture_limit'):run(env, [extra])
    monkeypatch.setattr(assets, 'MAX_CAPTURES', 4096)
    monkeypatch.setattr(assets, 'MAX_METADATA_BYTES', 1)
    with pytest.raises(VaultError, match='asset_plan_metadata_limit'):run(env, [extra])


def test_disk_reserve_real_guard_and_no_receipt_before_admission(env, monkeypatch):
    row, _ = native(env)
    monkeypatch.setattr(assets.shutil, 'disk_usage', lambda _: SimpleNamespace(free=assets.RESERVE_BYTES))
    with pytest.raises(VaultError, match='asset_disk_low'):run(env, [row])
    assert list(env[1].iterdir()) == [env[1] / '.assets.lock']


def test_unknown_or_malformed_index_refused(env):
    row, _ = native(env); (env[1]/'.assets.lock').unlink()
    with pytest.raises(VaultError):run(env,[row])
    (env[1]/'.assets.lock').touch(mode=0o600)
    bad=env[1]/'assets'/'unexpected';bad.mkdir(parents=True)
    with pytest.raises(VaultError, match='asset_plan_custody_layout'):run(env,[row])


def test_duplicate_inventory_unknown_id_and_bad_selection_refused(env):
    row,_=native(env)
    with pytest.raises(VaultError,match='asset_plan_inventory_duplicate'):run(env,[row,row])
    with pytest.raises(VaultError,match='asset_plan_selection_limit'):
        planner.plan({'format':'working-migration-inventory/1','entries':[row]},[{}],source_root=env[0],source_instance='test',custody_root=env[1])
    with pytest.raises(VaultError,match='asset_plan_override_invalid'):run(env,[row],source_paths={'_working/not-selected.md':'/tmp/never-read'})


def test_private_receipt_exclusive_permissions_and_separate_disk_guard(env, tmp_path, monkeypatch):
    row,_=native(env);result=run(env,[row]);parent=tmp_path/'private';parent.mkdir(mode=0o700)
    out=parent/'plan.json';planner.write_receipt(out,result)
    assert out.stat().st_mode & 0o777 == 0o600
    assert json.loads(out.read_text())['captured'] is False
    with pytest.raises(FileExistsError):planner.write_receipt(out,result)
    parent.chmod(0o755)
    with pytest.raises(VaultError,match='asset_plan_receipt_parent_insecure'):planner.write_receipt(parent/'other.json',result)
    parent.chmod(0o700)
    monkeypatch.setattr(assets.shutil,'disk_usage',lambda _:SimpleNamespace(free=assets.RESERVE_BYTES))
    with pytest.raises(VaultError,match='asset_disk_low'):planner.write_receipt(parent/'low.json',result)
    assert not (parent/'low.json').exists()


def test_cli_existing_inventory_selection_counts_only_and_optional_receipt(env, tmp_path, capsys):
    # Load actual candidate CLI; no real inventory/custody/source access.
    script=Path(__file__).parents[2]/'scripts/vault/assets_plan.py'
    spec=importlib.util.spec_from_file_location('private_assets_plan_cli',script);cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    row,_=native(env,'private-personal-title.md')
    inv=tmp_path/'inventory.json';inv.write_bytes(assets.canonical({'format':'working-migration-inventory/1','entries':[row]}))
    selection=tmp_path/'selection.json';selection.write_text(json.dumps({'format':'vault-private-asset-selection/1','ids':[row['path']]}))
    argv=['--inventory',str(inv),'--inventory-sha256',assets.sha256(inv.read_bytes()),'--selection',str(selection),'--source-root',str(env[0]),'--source-instance','synthetic-originals','--root',str(env[1])]
    assert cli.main(argv)==0
    stdout=capsys.readouterr().out
    assert row['path'] not in stdout and 'private-personal' not in stdout and str(tmp_path) not in stdout
    assert json.loads(stdout)['proposed_captures']==1
    assert set(env[1].iterdir())=={env[1]/'.assets.lock'}
    argv[3]='0'*64
    assert cli.main(argv)==2
    assert json.loads(capsys.readouterr().out)['code']=='asset_plan_inventory_hash_changed'
    argv[3]=assets.sha256(inv.read_bytes())
    assert cli.main(argv+['--out',str(env[1]/'plan.json')])==2
    assert json.loads(capsys.readouterr().out)['code']=='asset_plan_receipt_inside_custody'
    assert not (env[1]/'plan.json').exists()


def test_existing_capture_writer_lock_refuses_snapshot(env):
    import fcntl
    row,_=native(env)
    with (env[1]/'.assets.lock').open('rb') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(VaultError,match='asset_plan_custody_busy'):run(env,[row])


def test_missing_referenced_object_is_unknown_not_covered(env):
    row,path=native(env);capture(env,row,path)
    (env[1]/assets.object_path(row['sha256'])).unlink()
    with pytest.raises(VaultError,match='asset_plan_custody_missing_object'):run(env,[row])


def test_actual_stable_reader_detects_change_during_read(env, monkeypatch):
    row,path=native(env,data=b'x'*8192)
    original=assets.os.read;changed=False
    def racing_read(fd,n):
        nonlocal changed
        data=original(fd,n)
        if data and not changed:
            changed=True
            with path.open('ab') as stream:stream.write(b'grew')
        return data
    monkeypatch.setattr(assets.os,'read',racing_read)
    result=run(env,[row])
    assert result['items'][0]['reason']=='source_unsafe_or_changed'
    assert result['proposed_captures']==0 and not result['admitted_for_review']


def test_receipt_mode_is_exact_even_under_restrictive_umask(env,tmp_path):
    row,_=native(env);result=run(env,[row]);parent=tmp_path/'private';parent.mkdir(mode=0o700)
    old=os.umask(0o777)
    try:planner.write_receipt(parent/'receipt.json',result)
    finally:os.umask(old)
    assert (parent/'receipt.json').stat().st_mode & 0o777 == 0o600


def test_unselected_blob_metadata_cannot_hide_directory_or_symlink(env):
    row,path=native(env);capture(env,row,path)
    fake=env[1]/assets.object_path('f'*64);fake.mkdir()
    with pytest.raises(VaultError):run(env,[row])
    fake.rmdir();fake.symlink_to(path)
    with pytest.raises(VaultError):run(env,[row])


def test_all_historical_editions_export_bound_matches_actual_export(env,tmp_path,monkeypatch):
    # Regression: two6byte editions exceed the10byte export, despite latest6B.
    row,path=native(env,data=b'aaaaaa');capture(env,row,path)
    path.write_bytes(b'bbbbbb');last=capture(env,row,path)
    row.update(bytes=6,sha256=assets.sha256(b'bbbbbb'))
    monkeypatch.setattr(assets,'MAX_BYTES',10)
    monkeypatch.setattr(planner,'MAX_BATCH_BYTES',10)
    with pytest.raises(VaultError,match='asset_export_batch_too_large'):
        assets.export(env[1],tmp_path/'actual-export',ids=[last['asset_id']])
    with pytest.raises(VaultError,match='asset_plan_export_batch_too_large'):
        run(env,[row])


def test_projected_history_and_proposal_match_actual_export_without_old_blob_reads(env,tmp_path,monkeypatch):
    row,path=native(env,data=b'first-bytes');first=capture(env,row,path)
    path.write_bytes(b'old-edition');latest=capture(env,row,path)
    path.write_bytes(b'new-edition')
    old_reader=assets.read_stable;reads=[]
    def tracked(p):reads.append(Path(p));return old_reader(p)
    monkeypatch.setattr(assets,'read_stable',tracked)
    planned=run(env,[row]);projection=planned['projected_selected_export']
    assert projection['asset_editions']==3 and projection['unique_blob_bytes']==33
    assert projection['historical_integrity_verified'] is False
    # Only selected latest(old) proof and selected current source are read; no
    # new historical traversal gets introduced by projected recovery accounting.
    assert set(reads)=={path,env[1]/assets.object_path(latest['sha256'])}
    assert env[1]/assets.object_path(first['sha256']) not in reads
    second=capture(env,row,path)
    manifest=assets.export(env[1],tmp_path/'export',ids=[second['asset_id']])
    assert manifest['counts']['asset_editions']==projection['asset_editions']
    assert manifest['counts']['asset_bytes']==projection['unique_blob_bytes']
    assert len(manifest['files'])==projection['files']
    assert len(assets.canonical(manifest))==projection['manifest_bytes']
    assert assets.allocation_estimate(manifest['files'],manifest_bytes=len(assets.canonical(manifest)))==projection['allocation_bytes']


def test_recovery_disk_guard_uses_large_history_not_small_latest(env,monkeypatch):
    row,path=native(env,data=b'h'*16384);capture(env,row,path)
    path.write_bytes(b'x');capture(env,row,path)
    row.update(bytes=1,sha256=assets.sha256(b'x'))
    plan=run(env,[row]);recovery=plan['projected_selected_export']
    assert recovery['unique_blob_bytes']==16385 and recovery['asset_editions']==2
    expected=assets.allocation_estimate([{'path':'.assets.lock','bytes':0}])+3*recovery['allocation_bytes']+assets.MAX_METADATA_BYTES
    assert plan['capacity']['required_extra_bytes']==expected
    old_wrong=assets.allocation_estimate([{'path':'.assets.lock','bytes':0}])+3*assets.allocation_estimate([{'path':'0','bytes':1}])+assets.MAX_METADATA_BYTES
    assert expected>old_wrong
    monkeypatch.setattr(assets.shutil,'disk_usage',lambda _:SimpleNamespace(free=assets.RESERVE_BYTES+old_wrong+1))
    with pytest.raises(VaultError,match='asset_disk_low'):run(env,[row])


@pytest.mark.parametrize('limit',['files','manifest'])
def test_projected_export_file_and_manifest_bounds_match_real_export(env,tmp_path,monkeypatch,limit):
    row,path=native(env);receipt=capture(env,row,path)
    if limit=='files':
        monkeypatch.setattr(assets,'MAX_EXPORT_FILES',2)
        real_code='asset_export_batch_too_large';plan_code='asset_plan_export_batch_too_large'
    else:
        metadata=sum(len(raw) for _,_,raw in assets.inventory(env[1]))
        # One metadata record fits; export's larger JSON manifest does not.
        monkeypatch.setattr(assets,'MAX_METADATA_BYTES',metadata+1)
        real_code='asset_metadata_limit';plan_code='asset_plan_export_manifest_limit'
    with pytest.raises(VaultError,match=real_code):
        assets.export(env[1],tmp_path/'real-export',ids=[receipt['asset_id']])
    with pytest.raises(VaultError,match=plan_code):run(env,[row])
