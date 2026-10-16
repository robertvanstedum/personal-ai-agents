from pathlib import Path
import json
import os
import pytest

from scripts.tools.tour_capture import auth, paths
from scripts.tools.tour_capture.cli import build_parser


def test_private_defaults_shared_across_checkouts(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.delenv('MINIMOI_CAPTURE_OUTPUT_ROOT', raising=False)
    monkeypatch.delenv('MINIMOI_CAPTURE_AUTH_ROOT', raising=False)
    expected = tmp_path / 'minimoi-runtime/credentials/tour-capture/owner_session.json'
    assert auth.storage_state_path(tmp_path/'checkout-a', 'owner_session') == expected
    assert auth.storage_state_path(tmp_path/'checkout-b', 'owner_session') == expected
    assert paths.default_output_root() == tmp_path/'minimoi-runtime/operators/tour-capture/runs'
    assert not (tmp_path/'checkout-a').exists()
    for part in ['minimoi-runtime','minimoi-runtime/credentials','minimoi-runtime/credentials/tour-capture']:
        assert (tmp_path/part).stat().st_mode & 0o777 == 0o700
    assert not expected.exists()


def test_output_cli_precedence_and_default_is_readonly(monkeypatch, tmp_path):
    monkeypatch.setenv('MINIMOI_CAPTURE_OUTPUT_ROOT', str(tmp_path/'env-output'))
    assert build_parser().parse_args([]).output_root == tmp_path/'env-output'
    assert build_parser().parse_args(['--output-root',str(tmp_path/'explicit')]).output_root == tmp_path/'explicit'
    assert not (tmp_path/'env-output').exists()


@pytest.mark.parametrize('profile', ['../escape', '/absolute', 'unknown'])
def test_unknown_profile_never_creates_directory(monkeypatch,tmp_path,profile):
    monkeypatch.setenv('MINIMOI_CAPTURE_AUTH_ROOT',str(tmp_path/'state'))
    with pytest.raises(auth.CaptureAuthError): auth.storage_state_path(tmp_path/'repo',profile)
    assert not (tmp_path/'state').exists()


@pytest.mark.parametrize('suffix', ['_working/auth','_NewDomains/auth','repo/auth'])
def test_auth_refuses_working_or_checkout(monkeypatch,tmp_path,suffix):
    monkeypatch.setenv('MINIMOI_CAPTURE_AUTH_ROOT',str(tmp_path/suffix))
    with pytest.raises(auth.CaptureAuthError): auth.storage_state_path(tmp_path/'repo','owner_session')
    assert not (tmp_path/suffix).exists()


def test_auth_explicit_outside_root_is_distinct_from_output(monkeypatch,tmp_path):
    monkeypatch.setenv('MINIMOI_CAPTURE_AUTH_ROOT',str(tmp_path/'credentials'))
    monkeypatch.setenv('MINIMOI_CAPTURE_OUTPUT_ROOT',str(tmp_path/'captures'))
    assert auth.storage_state_path(tmp_path/'repo','owner_session') == tmp_path/'credentials/owner_session.json'
    assert not (tmp_path/'captures').exists()


@pytest.mark.parametrize('kind', ['symlink-root','symlink-parent','public-root','public-parent','file-root'])
def test_refuse_unsafe_private_directories(tmp_path,kind):
    target=tmp_path/'target';target.mkdir(mode=0o700)
    root=tmp_path/'root'
    if kind=='symlink-root': root.symlink_to(target,target_is_directory=True)
    elif kind=='symlink-parent':
        root.symlink_to(target,target_is_directory=True);root=root/'child'
    elif kind=='public-root': root.mkdir(mode=0o755)
    elif kind=='public-parent':
        root.mkdir(mode=0o777);root.chmod(0o777);root=root/'child'
    else: root.write_text('not-directory')
    with pytest.raises(RuntimeError): paths.ensure_private_directory(root)
    assert list(target.iterdir()) == []


@pytest.mark.parametrize('kind', ['symlink','hardlink','public-file'])
def test_refuse_unsafe_cookie_file(monkeypatch,tmp_path,kind):
    root=tmp_path/'auth';root.mkdir(mode=0o700)
    f=root/'owner_session.json';original=tmp_path/'original';original.write_text('untouched');original.chmod(0o600)
    if kind=='symlink':f.symlink_to(original)
    elif kind=='hardlink':os.link(original,f)
    else:f.write_text('{}');f.chmod(0o644)
    monkeypatch.setenv('MINIMOI_CAPTURE_AUTH_ROOT',str(root))
    with pytest.raises(RuntimeError): auth.storage_state_path(tmp_path/'repo','owner_session')
    assert original.read_text()=='untouched'


def test_legacy_cookie_remains_untouched(monkeypatch,tmp_path):
    old=tmp_path/'repo/_working/tour-capture/auth/owner_session.json'
    old.parent.mkdir(parents=True);old.write_text('old-private-state')
    monkeypatch.setenv('MINIMOI_CAPTURE_AUTH_ROOT',str(tmp_path/'new-private'))
    result=auth.storage_state_path(tmp_path/'repo','owner_session')
    assert not result.exists()
    assert old.read_text()=='old-private-state'


def test_login_persists_atomic_private_state_without_legacy_cookie(monkeypatch,tmp_path):
    monkeypatch.setenv('MINIMOI_CAPTURE_AUTH_ROOT',str(tmp_path/'auth'))
    monkeypatch.setattr(auth,'credentials_for',lambda _:('synthetic-user','synthetic-password'))
    state=auth.storage_state_path(tmp_path/'repo','owner_session')
    class Locator:
        def __init__(self,page):self.page=page
        def count(self):return 1
        def fill(self,_):pass
        def click(self):self.page.logged=True;self.page.url='https://dev.minimoi.ai/guild-next'
    class Page:
        logged=False
        url=''
        def goto(self,url,**kw):self.url=url if self.logged else 'https://dev.minimoi.ai/login'
        def locator(self,_):return Locator(self)
        def wait_for_url(self,predicate,**kw):assert predicate(self.url)
    class Context:
        def storage_state(self,**kw):
            assert not kw
            return {'cookies':[{'name':'synthetic','value':'synthetic'}],'origins':[]}
    auth.ensure_authenticated(Page(),Context(),'https://dev.minimoi.ai','/guild-next','owner_session',state)
    assert json.loads(state.read_text())['cookies'][0]['name']=='synthetic'
    assert state.stat().st_mode & 0o777 == 0o600
    assert sorted(p.name for p in state.parent.iterdir()) == ['owner_session.json']


def _runner(output_root):
    from scripts.tools.tour_capture.runner import CaptureRunner
    return CaptureRunner(
        {"id": "synthetic", "domain": "guild", "device_profile": "desktop"},
        "https://dev.minimoi.ai", output_root,
    )


def test_actual_run_directory_accepts_relative_output_without_resolving_links(monkeypatch,tmp_path):
    monkeypatch.chdir(tmp_path)
    run = _runner(Path("relative-output"))._new_run_dir()
    assert run.is_relative_to(tmp_path/"relative-output/synthetic")
    for path in [tmp_path/"relative-output", run.parent, run, *(run/name for name in ("raw","optimized","diagnostics"))]:
        assert path.stat().st_mode & 0o777 == 0o700


def test_relative_output_parent_symlink_is_not_resolved(monkeypatch,tmp_path):
    outside=tmp_path/"outside";outside.mkdir(mode=0o700)
    (tmp_path/"link").symlink_to(outside,target_is_directory=True)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError):_runner(Path("link/child"))._new_run_dir()
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("level",["scenario","run","raw"])
def test_actual_run_directory_refuses_existing_child_symlink(monkeypatch,tmp_path,level):
    from datetime import datetime, timezone
    from scripts.tools.tour_capture import runner
    fixed=datetime(2026,10,14,12,0,tzinfo=timezone.utc)
    class Clock:
        @staticmethod
        def now(_):return fixed
    monkeypatch.setattr(runner,"datetime",Clock)
    output=tmp_path/"output";output.mkdir(mode=0o700)
    outside=tmp_path/"outside";outside.mkdir(mode=0o755)
    scenario=output/"synthetic";run=scenario/"2026-10-14T120000Z"
    target={"scenario":scenario,"run":run,"raw":run/"raw"}[level]
    if level!="scenario":scenario.mkdir(mode=0o700)
    if level=="raw":run.mkdir(mode=0o700)
    target.symlink_to(outside,target_is_directory=True)
    with pytest.raises(RuntimeError):_runner(output)._new_run_dir()
    assert list(outside.iterdir()) == []


def test_relative_auth_override_still_refused(monkeypatch,tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MINIMOI_CAPTURE_AUTH_ROOT","repo/auth")
    with pytest.raises(RuntimeError):auth.storage_state_path(tmp_path/"repo","owner_session")
    assert not (tmp_path/"repo").exists()
