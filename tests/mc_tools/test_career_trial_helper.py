"""Fixed public fixture only; MC_CAREER_TEST_KIT points to a pinned operator staging copy."""
import importlib.util,json,os,shutil,tempfile
from pathlib import Path
from unittest.mock import patch
import pytest
from test_host_artifacts import request
from core.mc_tools.career_trials import prepared
from core.mc_tools import catalogue
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('career_helper',ROOT/'scripts/mc_host/career_trial.py')
H=importlib.util.module_from_spec(spec);spec.loader.exec_module(H)
KIT=os.environ.get('MC_CAREER_TEST_KIT')

@pytest.fixture
def root():
    with tempfile.TemporaryDirectory(dir=('/private/tmp' if os.path.isdir('/private/tmp') else None)) as tmp:yield Path(tmp)

@pytest.fixture
def kit():
    if not KIT:pytest.skip('requires operator-staged public kit snapshot')
    return KIT

def test_only_fixed_fixture_and_no_model_paths():
    assert catalogue.validate('minimoi_career_trial',{'fixture':'synthetic-v1'})
    for args in ({'fixture':'real'},{'fixture':'synthetic-v1','path':'/private'},{}, {'fixture':'synthetic-v1','command':'run'}):
        with pytest.raises(ValueError):catalogue.validate('minimoi_career_trial',args)

def test_exact_golden_outputs_and_recovery_never_regenerates(root,kit):
    payload,_=prepared();r=request(payload);out=H.run(r,str(root),kit)
    assert out['verified'] and out['files']==json.loads(payload)['career_trial']['expected']
    folder=root/'mc-artifacts'/r['operation_id'];checks=json.loads((folder/'checks.json').read_text())
    assert sorted(checks['checks'])==json.loads(payload)['career_trial']['check_names'] and all(checks['checks'].values())
    assert checks['page_count'].startswith('not checked')
    with patch.object(H,'generate',side_effect=AssertionError('must never execute twice')):
        assert not H.run(r,str(root),kit)['verified']
        assert H.run(dict(r,mode='verify'),str(root),kit)==out
    (folder/'letter.md').write_text('foreign bytes')
    assert not H.run(dict(r,mode='verify'),str(root),kit)['verified']

@pytest.mark.parametrize('fault',['expired','missing','partial','escape','kit_changed','dependency_changed'])
def test_failures_never_verify_or_retry(root,kit,fault):
    payload,_=prepared();r=request(payload)
    if fault=='expired':r['expires_at']=0
    elif fault=='missing':r['mode']='verify'
    elif fault=='escape':r['operation_id']='../escape'
    elif fault=='kit_changed':
        local=root/'kit';shutil.copytree(kit,local);(local/'workbook.py').write_text('changed');kit=str(local)
    elif fault=='dependency_changed':
        with patch.object(H._recipe.importlib.metadata,'version',return_value='wrong'):
            assert not H.run(r,str(root),kit)['verified'];return
    else:
        with patch.object(H.os,'write',side_effect=OSError):assert not H.run(r,str(root),kit)['verified']
        with patch.object(H,'generate',side_effect=AssertionError('no repeat')):
            assert not H.run(r,str(root),kit)['verified']
        r['mode']='verify'
    assert not H.run(r,str(root),kit)['verified']

def test_symlink_and_hardlink_refused(root,kit):
    payload,_=prepared();r=request(payload);outside=root/'outside';outside.mkdir();(root/'mc-artifacts').symlink_to(outside)
    assert not H.run(r,str(root),kit)['verified'] and not list(outside.iterdir())
    (root/'mc-artifacts').unlink();assert H.run(r,str(root),kit)['verified']
    os.link(root/'mc-artifacts'/r['operation_id']/'letter.md',root/'alias')
    assert not H.run(dict(r,mode='verify'),str(root),kit)['verified']

def test_canonicalization_changes_packaging_only():
    import io,zipfile
    def archive(year,xml=b'<document>synthetic</document>'):
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as z:
            z.writestr(zipfile.ZipInfo('word/document.xml',(year,1,1,0,0,0)),xml)
            z.writestr(zipfile.ZipInfo('[Content_Types].xml',(year,1,1,0,0,0)),b'<types/>')
        return stream.getvalue()
    first,second=archive(2001),archive(2026)
    assert first!=second
    canonical=H._recipe.canonical_docx(first)
    assert canonical==H._recipe.canonical_docx(second)
    with zipfile.ZipFile(io.BytesIO(first)) as a,zipfile.ZipFile(io.BytesIO(canonical)) as b:
        assert {n:a.read(n) for n in a.namelist()}=={n:b.read(n) for n in b.namelist()}
    assert canonical!=H._recipe.canonical_docx(archive(2001,b'<document>altered</document>'))
