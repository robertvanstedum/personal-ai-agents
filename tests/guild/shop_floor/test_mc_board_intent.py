import pytest
from types import SimpleNamespace
from minimoi_portal.guild_ui.mc import board_outcomes as O
PROMPT='Codex build verification, authorized by Robert’s dev test plan: Please pick up Board note #14, check which MiniMoi domains are available in dev, and record the result and any unavailable services on the Board. Use the current supplied service evidence. Do not change configuration, deploy, or message other agents. This is builder verification, not Robert’s acceptance.'
class Floor:
 def __init__(self,state='active',ok=True):self.calls=[];self.state=state;self.ok=ok
 def referenced_postits(self,ids):self.calls.append(ids);return SimpleNamespace(ok=self.ok,data={'postits':[{'id':14,'version':7,'state':self.state}]})
@pytest.mark.parametrize('text',[PROMPT,'Pick up Board note #14. Record the result on the Board. Do not deploy.','Please review Board item #14 and save the outcome to the Board. Use actual evidence.','Check Board #14 and record the result on the Board.'])
def test_direct_owner_requests(text):
 floor=Floor();plan=O.prepare(text,floor,'actual-owner-turn');assert plan.sources==((14,7),);assert plan.turn_key=='actual-owner-turn';assert floor.calls==[[14]]
@pytest.mark.parametrize('text',[
 'Board note #14. Do not record the result on the Board.',
 'Board note #14. Record the result on the Board. Actually, do not save it.',
 'Board note #14. Never record the result on the Board.',
 'He said: Please pick up Board note #14 and record the result on the Board.',
 'The model recommends: Please pick up Board note #14 and record the result on the Board.',
 'Copied text: Please pick up Board note #14 and record the result on the Board.',
 'Document: Board note #14. Record the result on the Board.',
 'Model response: Board note #14. Record the result on the Board.',
 'Board note #14. "Record the result on the Board."',
 'Board note #14. `Record the result on the Board.`',
 '> Board note #14. Record the result on the Board.',
 'What does Board note #14 say about recording results?',
 'Pick up Board note #14 and perhaps record the result on the Board.',
 'Pick up Board note #14 and record the result on the Boardroom.',
 'Pick up issue #14 and record the result on the Board.',
])
def test_quoted_reported_negative_or_noncommand_denied_before_lookup(text):
 floor=Floor();assert O.prepare(text,floor,'owner-turn')is None;assert not floor.calls
@pytest.mark.parametrize('state,ok',[('binned',True),('active',False)])
def test_bound_floor_still_required(state,ok):assert O.prepare(PROMPT,Floor(state,ok),'owner-turn')is None
from test_mc_board_outcomes import floored, _turn_on, _keep, _ask, MASTER_CRAFTSMAN, MasterCraftsmanBackend, Health, TurnResult, proposal
@pytest.mark.parametrize('finish',['stop','length'])
def test_direct_portal_turn_receives_contract_and_keeps_clean_gate(floored,finish):
 floor=floored.app.extensions['guild_ui_next']['services'].floor
 row=floor.add_postit('Check the dev domains',MASTER_CRAFTSMAN,idempotency_key='direct-intent-source').value
 class Backend(MasterCraftsmanBackend):
  kind='cos'
  def health(self):return Health('ready',reachable=True)
  def turn(self,req,cancel=None):
   assert 'report_board_outcome' in req.text and 'Check the dev domains' in req.text
   return TurnResult('answered','cos',text=proposal(row),trace={'finish_reason':finish})
 _turn_on(floored,Backend());floored.app.extensions['guild_ui_next']['feature_environ']['MINIMOI_MC_BOARD_OUTCOMES']='1'
 client=floored.owner();token=floored.csrf(client);note=_keep(client,token,PROMPT.replace('#14',f"#{row['id']}"));response=_ask(client,token,note['request_id']);body=response.get_json()
 assert response.status_code==200 and body['reply_note']['text']=='Three backends could not be resolved.'
 assert body['board_outcome']['state']==('saved'if finish=='stop'else'not_saved')
 assert len(floor.board().data['active'])==(2 if finish=='stop'else 1)
from floor_helpers import load_portal
from floor_db_helpers import floor_db
