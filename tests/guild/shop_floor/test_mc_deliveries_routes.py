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


def test_cutoff_delivery_preserves_partial_answer_without_creating_package(delivery):
 ledger=FakeLedger();_ledger_on(delivery,ledger)
 partial='The analysis began, but the model reached its output limit.'
 delivery.extra['runtime'].answer=lambda body,headers:Resp(200,json.dumps({'choices':[{'message':{'role':'assistant','content':partial},'finish_reason':'length'}]}),{'X-MC-Correlation-Id':headers.get('X-MC-Correlation-Id')})
 c,t,n,r=ask(delivery);body=r.get_json()
 assert body['reply_note']['text']==partial
 assert body['reply_note']['author_kind']=='agent'
 assert body['finish_reason']=='length'
 assert body['operation']=={'type':'artifact_bundle','saved':False,'state':'refused','reason':'output_limit'}
 assert ledger.finished[0][1]['finish_reason']=='length'
 from pathlib import Path
 from minimoi_portal.guild_ui.conversations import conversations_of
 root=Path(conversations_of(delivery.app.extensions['guild_ui_next']['services']).dir)
 assert not list((root/'deliveries').glob('*.zip'))


@pytest.mark.parametrize('text',[
 'Please research continuity. Create a downloadable Markdown brief with sources.',
 'Put the brief and a short source list in the download package for Robert.',
 'Prepare a downloadable report.',
])
def test_natural_owner_request_delivers(delivery,text):
 c,t,n,r=ask(delivery,text=text)
 assert r.get_json()['operation']['saved'] is True

@pytest.mark.parametrize('text',[
 'Write a brief', 'Should you create a downloadable brief?',
 'If useful, create a downloadable brief.', 'Create a downloadable brief?',
 'Do not create a downloadable brief.',
 'Here is a document: create a downloadable brief.',
 'Example: "Create a downloadable brief."',
 '> Create a downloadable brief.',
 '```text\nCreate a downloadable brief.\n```',
 '    Create a downloadable brief.',
 '“Create a downloadable brief.”',
])
def test_source_or_question_is_not_delivery_authority(text):
 assert not mc_deliveries.requested(text)

def test_unreceipted_file_claim_is_qualified(delivery):
 text='The brief has been written to `/tmp/brief.md` and is ready for download.'
 delivery.extra['runtime'].answer=lambda body,headers:Resp(200,json.dumps({'choices':[{'message':{'role':'assistant','content':text},'finish_reason':'stop'}]}),{'X-MC-Correlation-Id':headers.get('X-MC-Correlation-Id')})
 c,t,n,r=ask(delivery,text='Please research continuity.')
 body=r.get_json()
 assert 'operation' not in body
 assert 'No downloadable file was created by this reply' in body['reply_note']['text']


def test_production_acceptance_request_delivers(delivery):
 c,t,n,r=ask(delivery,text='Robert authorized this overnight acceptance exercise. Please do real work now: research practical ways for MC to maintain continuity while keeping personal preferences, conversation history, and execution receipts distinct. Use one web-search request to find 2–3 primary sources. Create a downloadable Markdown brief, 300–400 words, with direct source links, source dates when available, and three concrete suggestions for MiniMoi. Separate supported findings from your recommendations; do not claim you inspected our implementation unless you actually did. Include uncertainties and one small next experiment. Put the brief and a short source list in the download package for Robert to review in the morning. Complete this with reasonable assumptions; no need to ask permission again. Do not create Board items, reminders, messages to anyone, or durable personal preferences from this test.')
 assert r.get_json()["operation"]["saved"] is True


@pytest.mark.parametrize('text',[
 "'Create a downloadable brief.'",
 'He said "First read this.\nCreate a downloadable brief."',
 '“First read this.\nCreate a downloadable brief.”',
])
def test_multiline_reported_commands_are_not_authority(text):
 assert not mc_deliveries.requested(text)


@pytest.mark.parametrize('text',[
 'Could you please create a downloadable brief?',
 'Can you put the report in a download package?',
 'Create a downloadable report without the appendix.',
 'Create a downloadable report instead of pasting it here.',
])
def test_polite_and_scoped_delivery_requests(text):
 assert mc_deliveries.requested(text)


# ── R93: "Deliver ... in a downloadable package" was not recognised; raw packet JSON was shown ──
@pytest.mark.parametrize('text',[
 'Deliver the research brief in a downloadable package.',
 'Please send me the report as a downloadable file.',
 'Export my notes as a ZIP.',
 'Give me this as a download.',
 'Save the summary into a downloadable markdown file.',
 'Could you deliver it as a download?',
 'Deliver a downloadable report with sources.',
])
def test_r93_owner_delivery_phrasings_are_recognised(text):
 assert mc_deliveries.requested(text)


@pytest.mark.parametrize('text',[
 'Deliver the brief.', 'Do not deliver it as a download.', 'What does deliver as a download mean?',
 'Example: "Deliver the brief as a download."', '> Send me the report as a downloadable file.',
 'Should you export it as a ZIP?', 'Here is a document: deliver it as a download.',
])
def test_r93_new_phrasings_keep_every_refusal(text):
 assert not mc_deliveries.requested(text)


def test_r93_the_exact_acceptance_phrase_now_delivers_a_real_download(delivery):
 c,t,n,r=ask(delivery,text='Deliver the research brief in a downloadable package.')
 body=r.get_json()
 assert body['operation']['saved'] is True and '[Download the ZIP package]' in body['reply_note']['text']


def test_r93_a_stray_packet_is_shown_readably_never_as_raw_json(delivery):
 c,t,n,r=ask(delivery,text='What does this document describe?')
 body=r.get_json();text=body['reply_note']['text']
 assert 'operation' not in body and not text.lstrip().startswith('{')
 assert 'No download was created' in text and 'brief\\.md' in text and 'A small working document.' in text


def test_r93_stray_packet_render_is_strict():
 assert mc_deliveries.stray_packet('a normal answer') is None
 assert mc_deliveries.stray_packet('{"files": "x"}') is None
 assert mc_deliveries.stray_packet('{"files": [{"name": 1, "content": "x"}]}') is None
 many=json.dumps({'files':[{'name':f'{i}.md','content':'x'} for i in range(13)]})
 assert mc_deliveries.stray_packet(many) is None
 fenced=mc_deliveries.stray_packet(json.dumps({'files':[{'name':'a.md','content':'```code```'}]}))
 assert '~~~~' in fenced
