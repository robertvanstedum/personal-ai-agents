import json
from pathlib import Path
import pytest
from core.work_registers import RegisterStore,RegisterRefused,RegisterUncertain,read,parse,atomic,encoded
from minimoi_portal.guild_ui.mc_registers import command,context

@pytest.fixture
def store(tmp_path,monkeypatch):
    # Synthetic kernel; actual host sysctl may be sandbox-denied. No runtime identity override.
    monkeypatch.setattr('core.work_registers.writer_domain',lambda:'Linux:11111111-1111-1111-1111-111111111111')
    s=RegisterStore(tmp_path/'registers',environment='dev');s.initialize();return s

def meta(**kw):return {'title':'Keep decisions','kind':'expectation','status':'ongoing','opened':'2026-10-16','closed':None,'authorized_by':'owner attribution only','references':[],**kw}
def save(s,body='Preserve the original reasoning.',request='one',**kw):return s.save('mc','mc',meta(),body,request_id=request,**kw)

def test_card_receipt_history_and_replay(store):
    r=save(store);assert Path(r['path']).is_file();assert read(r['path'])==read(r['history_path'])
    assert save(store)['repeated'];assert len(store.projection('mc')['entries'])==1
    with pytest.raises(RegisterRefused):save(store,'Different words')

def test_revision_history_and_stale(store):
    r=save(store);old=read(r['path']);new=save(store,'Revised reasoning.',request='two',entry_id=r['id'],expected_revision=1)
    assert new['revision']==2 and read(r['history_path'])==old
    assert store.operation('mc','one')['revision']==1
    with pytest.raises(RegisterRefused):save(store,request='three',entry_id=r['id'],expected_revision=1)

def test_scope_and_cross_register_authority(store):
    with pytest.raises(RegisterRefused):store.save('shared','shared',meta(), 'Never authority',request_id='bad')
    with pytest.raises(RegisterRefused):store.save('codex','mc',meta(authorized_by='Robert'), 'Cannot grant',request_id='bad')
    r=store.save('codex','mc',meta(status='proposed'), 'Proposal',request_id='proposal');assert r['id']=='MC-0001'

def test_shared_once_and_class_scope(store):
    store.authorizer=lambda actor,target:True
    store.save('mc','shared',meta(applies_to=['everyone']), 'Shared record',request_id='shared')
    assert len(store.projection('mc')['entries'])==len(store.projection('robert')['entries'])==1
    assert len(list(store.root.rglob('entries/*.md')))==1

def test_invalid_input_leaves_projection(store):
    r=save(store);before=read(store.root/'mc/register.json')
    p=Path(r['path']).with_name('MC-0001-other.md');p.write_bytes(read(r['path']))
    with pytest.raises(RegisterRefused):store.regenerate()
    assert read(store.root/'mc/register.json')==before

def test_yaml_alias_and_duplicate_keys():
    for raw in [b'---\na: 1\na: 2\n---\nx',b'---\na: &a hi\nb: *a\n---\nx']:
        with pytest.raises(RegisterRefused):parse(raw)

def test_lifecycle_and_title_before_write(store):
    for m in [meta(status='closed'),meta(title='bad\ntitle'),meta(supersedes='MC-9999')]:
        with pytest.raises(RegisterRefused):store.save('mc','mc',m,'Body',request_id='bad')
    assert not list(store.root.rglob('entries/*.md'))

def test_receipt_path_tampering(store,tmp_path):
    save(store);p=next((store.root/'receipts').iterdir());v=json.loads(read(p));v['history_path']=str(tmp_path/'private');p.write_bytes(encoded(v))
    with pytest.raises(RegisterRefused,match='receipt_path'):store.operation('mc','one')

def test_publication_failure_is_held(store,monkeypatch):
    def fail(cards):raise OSError('fixture failure')
    monkeypatch.setattr(store,'_publish',fail)
    with pytest.raises(RegisterUncertain):save(store)
    assert list((store.root/'attempts').iterdir()) and list(store.root.rglob('entries/*.md'))
    with pytest.raises(RegisterRefused,match='prior_publication'):save(store)

def test_owner_command_exact_and_private(store):
    for text in ['Example: add this to your register: do it','> Add this to your register: do it']:
        assert command(text,store=store,principal='robert',on_record=True,operation_id='x')is None
    assert command('Add this to your register: Do it',store=store,principal='robert',on_record=False,operation_id='x')['status']=='refused'
    assert not list(store.root.rglob('entries/*.md'))
    args=dict(store=store,principal='robert',on_record=True,operation_id='x')
    r=command('Please add this to your register: Keep this exact wording.',**args)
    assert r['status']=='saved'and 'Keep this exact wording.'in read(r['operation']['path']).decode()
    assert command('Please add this to your register: Keep this exact wording.',**args)['operation']['repeated']
    assert command('Please add this to your register: Changed.',**args)['status']=='refused'
    assert 'DATA'in context(store,startup=True)and context(store,owner_text='hello')==''

def test_environment_binding(store):
    with pytest.raises(RegisterRefused,match='scope_changed'):RegisterStore(store.root,environment='production').projection('mc')

def test_sequence_and_duplicate_title_preserve_existing(store):
    r=save(store)
    with pytest.raises(RegisterRefused,match='duplicate_title'):save(store,request='two')
    assert read(r['path'])==read(r['history_path']) and len(store.projection('mc')['entries'])==1


def test_foreign_proposal_cannot_replace_existing_owner_card(store):
    original=save(store);before=read(original['path'])
    with pytest.raises(RegisterRefused,match='new_proposal'):
        store.save('codex','mc',meta(status='proposed'), 'Foreign overwrite',request_id='replace',entry_id=original['id'],expected_revision=1)
    assert read(original['path'])==before and len(store.projection('mc')['entries'])==1
    assert not (store.root/'attempts'/'replace.json').exists()


def test_kernel_domain_fences_writes_but_allows_readonly(store,monkeypatch):
    save(store);scope=read(store.root/'scope.json');card=read(next(store.root.rglob('entries/*.md')))
    monkeypatch.setattr('core.work_registers.writer_domain',lambda:'Linux:00000000-0000-0000-0000-000000000000')
    assert len(store.projection('mc')['entries'])==1
    for operation in (lambda:save(store,request='other'),store.regenerate):
        with pytest.raises(RegisterRefused,match='writer_domain_changed'):operation()
    assert read(store.root/'scope.json')==scope and read(next(store.root.rglob('entries/*.md')))==card


def test_domain_failure_precedes_initialization(tmp_path,monkeypatch):
    def fail():raise RegisterRefused('fixture_domain_unavailable')
    monkeypatch.setattr('core.work_registers.writer_domain',fail)
    root=tmp_path/'uncreated'
    with pytest.raises(RegisterRefused):RegisterStore(root,environment='dev').initialize()
    assert not root.exists()

def test_darwin_domain_reader_is_fixed_bounded(monkeypatch):
    from core.work_registers import writer_domain
    import core.work_registers as module
    monkeypatch.setattr(module.platform,'system',lambda:'Darwin')
    def fake(args,**kw):
        assert args==['/usr/sbin/sysctl','-n','kern.bootsessionuuid']and kw['timeout']==2 and kw['capture_output']
        return type('Reply',(),{'stdout':b'22222222-2222-2222-2222-222222222222\n'})()
    monkeypatch.setattr(module.subprocess,'run',fake)
    assert writer_domain()=='Darwin:22222222-2222-2222-2222-222222222222'


def snapshot(root):return {str(p.relative_to(root)):p.read_bytes()for p in root.rglob('*')if p.is_file()}

@pytest.mark.parametrize('limit',['count','bytes','projection'])
def test_prospective_capacity_has_no_canonical_mutation(store,monkeypatch,limit):
    import core.work_registers as module
    save(store);before=snapshot(store.root)
    if limit=='count':monkeypatch.setattr(module,'MAX_CARDS',1)
    elif limit=='bytes':
        # Existing source fits; proposed source set does not.
        monkeypatch.setattr(module,'MAX_TOTAL',len(read(next(store.root.rglob('entries/*.md'))))+10)
    else:
        original=store._projection
        def large(cards,*args,**kw):
            result=original(cards,*args,**kw)
            if len(cards)>1:result['fixture_extra']='x'*module.MAX_TOTAL
            return result
        monkeypatch.setattr(store,'_projection',large)
    with pytest.raises(RegisterRefused):
        store.save('mc','mc',meta(title='Different title'),'Second body',request_id='capacity')
    assert snapshot(store.root)==before
