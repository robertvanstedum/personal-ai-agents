import pytest
from domains.cos.runtime_policy import background_jobs_enabled

def test_default_preserves_existing_background_work():
    assert background_jobs_enabled({}) is True
    assert background_jobs_enabled({"MINIMOI_ROLE":"production"}) is True

def test_only_explicit_standby_can_disable_background_work():
    assert background_jobs_enabled({"MINIMOI_ROLE":"standby","COS_BACKGROUND_JOBS":"0"}) is False
    for role in (None,"production","dev",""):
        with pytest.raises(RuntimeError):
            background_jobs_enabled({"MINIMOI_ROLE":role,"COS_BACKGROUND_JOBS":"0"})

def test_invalid_switch_fails_closed():
    for value in ("false","",None,"2"):
        with pytest.raises(RuntimeError):
            background_jobs_enabled({"MINIMOI_ROLE":"standby","COS_BACKGROUND_JOBS":value})

def test_real_startup_body_returns_before_threads_and_scheduler(monkeypatch):
    # Compile the actual startup body without importing the service (whose import
    # itself starts it). This makes moving the guard below either start fail.
    import ast, threading
    from pathlib import Path
    from types import SimpleNamespace
    source=Path('domains/cos/chief_of_staff.py').read_text()
    function=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='_start_cos')
    def forbidden(*args,**kwargs):raise AssertionError('background work attempted')
    monkeypatch.setenv('MINIMOI_ROLE','standby')
    monkeypatch.setenv('COS_BACKGROUND_JOBS','0')
    monkeypatch.delenv('DISABLE_TELEGRAM_POLL',raising=False)
    state={};context={'BASE_DIR':Path.cwd(),'sys':__import__('sys'),'os':__import__('os'),
      'PORT':8769,'_check_db':lambda:False,'init_backend':lambda:None,
      '_backend':SimpleNamespace(backend_label='test'),'_state':state,'_state_lock':threading.Lock(),
      'threading':SimpleNamespace(Thread=forbidden),'BackgroundScheduler':forbidden}
    exec(compile(ast.Module(body=[function],type_ignores=[]),'actual_startup','exec'),context)
    context['_start_cos']()
    assert state['state']=='running'
