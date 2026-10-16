"""Candidate attach only: fake sockets; no SDK/network/budget/provider calls."""
import copy,importlib.util,sys,threading,unittest
from pathlib import Path
from types import SimpleNamespace
from core.realtime_voice import server_openai as m
GOOD={'type':'session.updated','session':{'type':'realtime','model':m.MODEL,
 'audio':{'input':{'turn_detection':{'create_response':False}}},
 'tools':[{'type':'function','name':name} for name in sorted(m.TOOLS)]}}
class Socket:
 def __init__(self,events):self.events=list(events);self.sent=[];self.closed=threading.Event();self.received=0
 def send(self,event):self.sent.append(copy.deepcopy(event))
 def recv(self):
  assert self.sent==[{'type':'session.update','session':{'type':'realtime'}}], 'must solicit before receiving'
  self.received+=1
  event=self.events.pop(0)
  if isinstance(event,BaseException):raise event
  return SimpleNamespace(model_dump=lambda **kw:copy.deepcopy(event))
 def __iter__(self):
  self.closed.wait(.5)
  return iter(())
 def close(self):self.closed.set()
class Attach(unittest.TestCase):
 def provider(self,events):
  socket=Socket(events);manager=SimpleNamespace(__enter__=lambda:socket,__exit__=lambda *args:socket.close())
  # Special methods must be defined on class, not instance.
  class Manager:
   def __enter__(self):return socket
   def __exit__(self,*args):socket.close()
  provider=m.OpenAIControlledProvider(SimpleNamespace(realtime=SimpleNamespace(connect=lambda **kw:Manager(),calls=SimpleNamespace(hangup=lambda *a,**kw:None))))
  provider.call_id='rtc_synthetic';self.addCleanup(socket.close)
  return provider,socket
 def test_no_initial_event_only_update_ack(self):
  p,s=self.provider([GOOD]);p.attach('rtc_synthetic',lambda event:None)
  self.assertEqual(s.received,1);self.assertEqual(s.sent[0]['session'],{'type':'realtime'})
 def test_created_then_updated_ack(self):
  p,s=self.provider([{'type':'session.created','session':{'model':m.MODEL}},GOOD]);p.attach('rtc_synthetic',lambda event:None)
  self.assertEqual(s.received,2)
 def test_created_alone_is_not_readiness(self):
  p,s=self.provider([{'type':'session.created'},TimeoutError()])
  with self.assertRaises(TimeoutError):p.attach('rtc_synthetic',lambda event:None)
 def test_error_and_unexpected_and_malformed_refused(self):
  for value in ({'type':'error','error':{'message':'private synthetic diagnostic'}},{'type':'response.created'},[],{'type':'session.updated','session':None}):
   with self.subTest(value=value):
    p,s=self.provider([value])
    with self.assertRaises(m.SessionRefused) as error:p.attach('rtc_synthetic',lambda event:None)
    self.assertNotIn('private synthetic diagnostic',str(error.exception))
 def test_config_drift_refused(self):
  mutations=[lambda s:s.update(model='different-model'),lambda s:s.update(type='transcription'),
   lambda s:s['audio']['input']['turn_detection'].update(create_response=True),lambda s:s.update(tools=[]),
   lambda s:s['tools'][0].update(type='mcp'),lambda s:s['tools'][0].update(name=s['tools'][1]['name'])]
  for mutate in mutations:
   with self.subTest(mutate=mutate):
    bad=copy.deepcopy(GOOD);mutate(bad['session']);p,s=self.provider([bad])
    with self.assertRaises(m.SessionRefused):p.attach('rtc_synthetic',lambda event:None)
 def test_created_flood_is_bounded(self):
  p,s=self.provider([{'type':'session.created'}]*3)
  with self.assertRaises(m.SessionRefused):p.attach('rtc_synthetic',lambda event:None)
  self.assertEqual(s.received,2)
 def test_wrong_call_id_before_connection(self):
  p,s=self.provider([GOOD])
  with self.assertRaises(m.SessionRefused):p.attach('rtc_foreign',lambda event:None)
  self.assertEqual(s.sent,[])
class Hangup(unittest.TestCase):
 def provider(self, *, fail_hangup=False, fail_cleanup=False):
  events=[]
  def hangup(call, **kwargs):
   events.append('hangup')
   if fail_hangup:raise TimeoutError('private provider payload')
  class Manager:
   def __exit__(self,*args):
    events.append('cleanup')
    if fail_cleanup:raise RuntimeError('private socket details')
  p=m.OpenAIControlledProvider(SimpleNamespace(realtime=SimpleNamespace(calls=SimpleNamespace(hangup=hangup))))
  p.call_id='rtc_synthetic';p.manager=Manager()
  return p,events
 def test_successful_hangup_remains_confirmed_when_cleanup_fails(self):
  p,events=self.provider(fail_cleanup=True)
  with self.assertLogs(m.__name__,level='WARNING') as logs:p.hangup('rtc_synthetic')
  self.assertEqual(events,['hangup','cleanup']);self.assertFalse(p.sideband_cleanup_confirmed)
  self.assertNotIn('private socket',str(logs.output))
 def test_failed_hangup_still_uncertain_and_cleanup_attempted_once(self):
  p,events=self.provider(fail_hangup=True,fail_cleanup=True)
  with self.assertLogs(m.__name__,level='WARNING') as logs:
   with self.assertRaises(TimeoutError):p.hangup('rtc_synthetic')
  self.assertEqual(events,['hangup','cleanup'])
  self.assertNotIn('private provider',str(logs.output));self.assertNotIn('private socket',str(logs.output))
 def test_wrong_call_refused_before_any_request_or_cleanup(self):
  p,events=self.provider()
  with self.assertRaises(m.SessionRefused):p.hangup('rtc_foreign')
  self.assertEqual(events,[])
if __name__=='__main__':unittest.main()
