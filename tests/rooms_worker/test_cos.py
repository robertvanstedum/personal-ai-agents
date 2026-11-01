"""CoS transport keeps authority and dispatch admission ahead of inference."""
from types import SimpleNamespace
from services.rooms_worker.cos import CoS

class Session:
    def __init__(self): self.calls=[]
    def post(self,url,**kwargs):
        self.calls.append(kwargs)
        t=kwargs['json']
        return SimpleNamespace(status_code=200,json=lambda:{'text':'Reply','room':t['room'],'turn_id':t['id']})

def test_owner_admission_and_stable_payload(tmp_path):
    key=tmp_path/'key';key.write_text('x'*48);key.chmod(0o600)
    session=Session();c=CoS(key,session=session)
    t={'id':'turn','room':'room','addressee':'cos','trigger':{'actor':'robert','kind':'message','origin':None},'brief':{},'transcript':[],'coverage':{},'claim_id':'changing'}
    assert c.stream([],None,None,turn=t,admit=lambda:(False,'cancel_requested'))['outcome']=='not_admitted'
    assert not session.calls
    assert c.stream([],None,None,turn=t,admit=lambda:(True,None))['outcome']=='done'
    assert 'claim_id' not in session.calls[0]['json']
    assert c.stop_guaranteed is False
    t['trigger']['actor']='mc'
    assert c.stream([],None,None,turn=t,admit=lambda:(_ for _ in ()).throw(AssertionError()))['outcome']=='refused'
    assert len(session.calls)==1
