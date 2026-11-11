from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
import pytest
from core.lead_wake.runtime import Runtime
from core.lead_wake.hosts import Admission
from services.helper_seats.spend import Ledger
from services.partnership_tasks.auth import token_sha256
from tests.partnership.test_helper_ask import rig  # noqa
from tests.partnership.test_lead_wake_authority import delivered
from tests.partnership.test_build_item import Client
from tests.partnership.synth import Flow,ref

class Host:
    @contextmanager
    def lane(self,*a):yield Admission(lambda:None)


def setup(rig,tmp_path,reviewer=None):
    _,wake,inbox=delivered(rig,tmp_path)
    svc=rig.svc
    svc.helper_returns=True
    b=svc.bindings.resolve('tok-mc',svc._clock())
    svc.bindings._by_hash[token_sha256('tok-return')]=replace(b,binding_id='bind-return',
        operations=frozenset({'task.commit_helper_return'}),helper_return_commit=True)
    calls=[]
    def review(packet,context):
        calls.append(context)
        return {'verdict':'approve','reasons':'Verified exact helper evidence.', 'answer':'Apprentice found build_log.html; MC checked the evidence.'}
    ledger=Ledger(tmp_path/'review-spend',daily_cap=1)
    rt=Runtime(inbox=inbox,reader=Client(svc,'tok-mc'),committer=Client(svc,'tok-return'),
        principal='principal',lead='mc',environment='synthetic',host=Host(),
        reviewer=reviewer or review,charters=svc.charters,ledger_for=lambda c:ledger,
        review_bound=.001,review_reserve=.002)
    return rt,wake,calls


def test_real_worker_evidence_review_atomic_commit_and_restart_no_second_model(rig,tmp_path):
    rt,wake,calls=setup(rig,tmp_path)
    assert rt.pass_once()==[(wake['operation_id'],'committed')]
    assert len(calls)==1 and 'build_log.html' in str(calls[0])
    assert rt.publication(wake['operation_id'])['packet']['accepted'] is True
    assert rt.pass_once()==[(wake['operation_id'],'committed')]
    assert len(calls)==1


def test_stop_after_immutable_commit_does_not_revalidate_wake(rig,tmp_path):
    rt,wake,calls=setup(rig,tmp_path)
    assert rt.pass_once()[0][1]=='committed'
    # Explicitly poison current wake lookup: committed publication is sufficient.
    rt.inbox.resolve=lambda op: (_ for _ in ()).throw(AssertionError('must not resolve old wake'))
    assert rt.pass_once()[0][1]=='committed'
    assert len(calls)==1


def test_unknown_model_never_replayed(rig,tmp_path):
    calls=[]
    def fail(*a):
        calls.append(1)
        raise TimeoutError()
    rt,wake,_=setup(rig,tmp_path,fail)
    assert rt.pass_once()==[(wake['operation_id'],'uncertain')]
    assert rt.pass_once()==[] and len(calls)==1


def test_stop_before_review_means_no_model_no_commit(rig,tmp_path):
    rt,wake,calls=setup(rig,tmp_path)
    assert Flow(rig.svc).transition(wake['task_id'],'canceled',token='tok-principal',reason='stop',evidence=[ref('records','stop')])['outcome']=='accepted'
    assert rt.pass_once()==[(wake['operation_id'],'superseded')]
    assert not calls and rt.publication(wake['operation_id']) is None


def test_definitive_commit_refusal_keeps_review_and_stops_retry_loop(rig,tmp_path):
    rt,wake,calls=setup(rig,tmp_path)
    submitted=[]
    def refused(cmd):
        submitted.append(cmd)
        return {'outcome':'conflict','code':'revision_conflict'}
    rt.committer=SimpleNamespace(submit=refused)
    assert rt.pass_once()==[(wake['operation_id'],'commit_refused')]
    assert rt.inbox.status(wake['operation_id'])=='commit_blocked'
    assert rt.inbox.review(wake['operation_id']) is not None
    assert rt.pass_once()==[] and len(calls)==len(submitted)==1


def test_inline_review_suppresses_extra_model_without_claiming_presentation(rig,tmp_path):
    from tests.partnership.test_helper_ask import review
    rt,wake,calls=setup(rig,tmp_path)
    task=rig.svc.get('task',wake['task_id'],'tok-mc')
    outcome=review(rig,{'data':{'task_id':task['task_id'],'set_digest':wake['packet']['result_digest']}})
    assert outcome['status']=='done'
    assert rt.pass_once()==[(wake['operation_id'],'reviewed_elsewhere')]
    assert calls==[] and rt.inbox.status(wake['operation_id'])!='presented'


@pytest.mark.parametrize('flag,enabled',[(None,False),('0',False),('1',True)])
def test_task_http_main_requires_explicit_return_activation(tmp_path,monkeypatch,flag,enabled):
    from services.partnership_tasks import http_api,charters,auth
    import werkzeug.serving
    import os
    (tmp_path/'journal').mkdir();(tmp_path/'journal'/'journal.jsonl').touch()
    for key in list(os.environ):
        if key.startswith('PARTNERSHIP_'):monkeypatch.delenv(key)
    monkeypatch.setenv('PARTNERSHIP_ROOT',str(tmp_path))
    monkeypatch.setenv('PARTNERSHIP_ENVIRONMENT','dev')
    monkeypatch.setenv('PARTNERSHIP_BINDINGS','synthetic-bindings')
    if flag is not None:monkeypatch.setenv('PARTNERSHIP_HELPER_RETURNS',flag)
    captured=[]
    monkeypatch.setattr(auth.Bindings,'load',lambda _:None)
    monkeypatch.setattr(charters,'load_if_approved',lambda _:None)
    monkeypatch.setattr(http_api,'TaskService',lambda *a,**kw:captured.append(kw))
    monkeypatch.setattr(http_api,'create_app',lambda *a,**kw:None)
    monkeypatch.setattr(werkzeug.serving,'make_server',lambda *a,**kw:SimpleNamespace(serve_forever=lambda:None))
    assert http_api.main()==0
    assert captured[0]['helper_returns'] is enabled
