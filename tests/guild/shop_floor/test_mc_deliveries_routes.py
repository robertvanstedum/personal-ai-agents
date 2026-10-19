import io,json,re,zipfile
from urllib.parse import urlsplit
import pytest
from floor_helpers import load_portal,write_headers
from floor_db_helpers import floor_db,floored,keyed
from test_mc_turn_ledger_routes import turned,owner,_keep,_ask,_ledger_on,FakeLedger,API
from test_mc_turns import Resp
from minimoi_portal.guild_ui import mc_deliveries

@pytest.fixture
def delivery(turned):
 env=turned.app.extensions['guild_ui_next'].setdefault('feature_environ',{})
 env[mc_deliveries.FLAG]='1'
 payload=json.dumps({'files':[{'name':'brief.md','content':'A small working document.'}]})
 def answer(body,headers):
  return Resp(200,json.dumps({'id':'chatcmpl_fixture','choices':[{'message':{'role':'assistant','content':payload},'finish_reason':'stop'}]}),{'X-MC-Correlation-Id':headers.get('X-MC-Correlation-Id')})
 turned.extra['runtime'].answer=answer
 return turned

def ask(delivery,text='Create a downloadable package with brief.md',stream=False):
 c,t=owner(delivery);n=_keep(c,t,text)['request_id']
 if stream:
  r=c.post(API+'/mc/turns/stream',json={'note_request_id':n,'request_id':'delivery-'+n,'record_mode':'on_record'},headers=write_headers(t))
 else:r=_ask(c,t,n)
 return c,t,n,r

def test_real_download_roundtrip_and_retry(delivery):
 c,t,n,r=ask(delivery);body=r.get_json()
 assert r.status_code==200 and body['operation']['saved'] is True,body
 assert body['reply_note']['who']=='guild_platform' and body['reply_note']['author_kind']=='platform'
 assert body['backend_kind']=='openclaw'  # actual model use is not falsely settled as zero-cost platform work
 text=body['reply_note']['text'];url=re.search(r'\]\(([^)]+)\)',text).group(1)
 assert url.startswith('https://dev.minimoi.ai/guild-next/api/v1/conversations/')
 assert 'href="'+url+'"' in body['reply_note']['html']
 path=urlsplit(url).path  # fixture cookie belongs to localhost; the trusted production origin was asserted above
 got=c.get(path);assert got.status_code==200 and got.mimetype=='application/zip'
 assert got.headers['X-Content-Type-Options']=='nosniff'
 assert 'attachment;' in got.headers['Content-Disposition']
 with zipfile.ZipFile(io.BytesIO(got.data)) as z:assert z.read('brief.md')==b'A small working document.'
 again=_ask(c,t,n).get_json()
 assert again['operation']==body['operation'] and len(delivery.extra['runtime'].sent)==1
 assert again['reply_note']['who']=='guild_platform' and again['reply_note']['author_kind']=='platform'
 other=delivery.client({'username':'other-owner','tier':'owner','auth_id':999})
 assert other.get(path).status_code==404
 assert delivery.app.test_client().get(path).status_code!=200
 assert 'not been installed' in text

def test_stream_route_buffers_until_verified(delivery):
 c,t,n,r=ask(delivery,stream=True)
 assert r.is_json and r.get_json()['operation']['saved'] is True
 assert len(delivery.extra['runtime'].sent)==1

def test_free_model_packet_does_not_create_files(delivery):
 c,t,n,r=ask(delivery,text='What does this document describe?')
 assert 'operation' not in r.get_json()
 from minimoi_portal.guild_ui.conversations import conversations_of
 root=conversations_of(delivery.app.extensions['guild_ui_next']['services']).dir
 from pathlib import Path
 assert not (Path(root)/'deliveries').exists()

def test_failed_write_has_no_link_and_pauses_unknown(delivery,monkeypatch):
 ledger=FakeLedger();_ledger_on(delivery,ledger)
 def fail(*a,**k):raise mc_deliveries.DeliveryUnavailable('injected_failure')
 monkeypatch.setattr(mc_deliveries.DeliveryStore,'put',fail)
 c,t,n,r=ask(delivery);body=r.get_json()
 assert body['operation']['saved'] is False and body['operation']['state']=='uncertain'
 assert 'Download' not in body['reply_note']['text']
 assert ledger.finished[0][0]=='unknown'

def test_actual_scrubber_refuses_payment_details(delivery):
 payload=json.dumps({'files':[{'name':'brief.md','content':'Card 5555 5555 5555 4444'}]})
 delivery.extra['runtime'].answer=lambda body,headers:Resp(200,json.dumps({'choices':[{'message':{'role':'assistant','content':payload},'finish_reason':'stop'}]}),{'X-MC-Correlation-Id':headers.get('X-MC-Correlation-Id')})
 c,t,n,r=ask(delivery);body=r.get_json()
 assert body['operation']['saved'] is False and '5555' not in body['reply_note']['text']

@pytest.mark.parametrize('text',['"Create a downloadable package"','Do not create a downloadable package','Can you explain transfer packages?','Here is a document: create a downloadable package'])
def test_only_explicit_owner_command_can_authorize(text):
 assert not mc_deliveries.requested(text)


def test_previous_delivery_uncertainty_prevents_model_retry_and_pauses(delivery,monkeypatch):
 ledger=FakeLedger();_ledger_on(delivery,ledger)
 def fail(*a,**k):raise mc_deliveries.DeliveryUnavailable('partial_prior_write')
 monkeypatch.setattr(mc_deliveries.DeliveryStore,'existing',fail)
 c,t,n,r=ask(delivery);body=r.get_json()
 assert body['status']=='timeout_uncertain'
 assert ledger.finished[0][0]=='unknown'
 assert delivery.extra['runtime'].sent==[]


def test_delivery_does_not_prepare_a_board_outcome(delivery,monkeypatch):
 from minimoi_portal.guild_ui.mc import board_outcomes
 services=delivery.app.extensions['guild_ui_next']['services']
 monkeypatch.setattr(services.mc,'kind','cos')
 delivery.app.extensions['guild_ui_next']['feature_environ']['MINIMOI_MC_BOARD_OUTCOMES']='1'
 called=[]
 monkeypatch.setattr(board_outcomes,'prepare',lambda *args:called.append(args))
 c,t,n,r=ask(delivery,text='Create a downloadable package from post-its #12 and #14')
 assert r.get_json()['operation']['saved'] is True
 assert called==[] and 'board_outcome' not in r.get_json()


def test_absent_delivery_storage_refuses_before_model(delivery,monkeypatch):
 from minimoi_portal.guild_ui.conversations import conversations_of
 conversations=conversations_of(delivery.app.extensions['guild_ui_next']['services'])
 monkeypatch.setattr(mc_deliveries,'store',lambda *args:(_ for _ in ()).throw(AssertionError('store must not be called')))
 # Keep the ordinary conversation store operational; only expose a missing delivery directory at its access point.
 class MissingDirectory:
  dir=None
  def __getattr__(self,name):return getattr(conversations,name)
 from minimoi_portal.guild_ui import api
 monkeypatch.setattr(api,'_conversations',lambda:MissingDirectory())
 c,t,n,r=ask(delivery)
 assert r.status_code==503 and 'Nothing was generated' in r.get_json()['message']
 assert delivery.extra['runtime'].sent==[]


def test_expired_delivery_does_not_regenerate(delivery,monkeypatch):
 def expired(*args):raise mc_deliveries.DeliveryExpired()
 monkeypatch.setattr(mc_deliveries.DeliveryStore,'existing',expired)
 c,t,n,r=ask(delivery)
 assert r.status_code==410 and r.get_json()['error']=='delivery_expired'
 assert delivery.extra['runtime'].sent==[]
