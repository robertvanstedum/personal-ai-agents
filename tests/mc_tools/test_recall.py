import copy
import json
import pytest
from core.mc_tools import recall

C='c-aaaaaaaaaaaa'; D='c-bbbbbbbbbbbb'
def row(i,text):return dict(id=i,text=text,who='owner',author_kind='owner',created_at='2026-10-24T00:00:00Z')
class Source:
    def __init__(self,rows):self.rows=rows;self.checked=0;self.caps=[]
    def begin(self,d):pass
    def current(self,c,r):assert c==C and r=='request'
    def conversations(self):return [dict(id=c) for c in sorted(self.rows)]
    def snapshot(self,c,current,request,boundary,budget):
        cap=min(boundary if boundary is not None else 999,2 if c['id']==current else 999);self.caps.append(cap)
        rows=[r for r in self.rows[c['id']] if r['id']<=cap];b=max((r['id'] for r in rows),default=0)
        return (rows if sum(len(r['text'].encode()) for r in rows)<=budget else None,b)
    def recheck(self):self.checked+=1

def read(s,a):return recall.read(s,a,current_conversation=C,request_id='request')
def test_find_pages_do_not_skip_matches_inside_a_note():
    s=Source({C:[row(1,'needle one needle two needle three'),row(2,'needle four'),row(3,'needle forbidden')]})
    args=dict(action='find',query='needle',limit=1);items=[]
    for _ in range(10):
        out=read(s,args);items+=out['items']
        if out['complete']:break
        args['continuation']=out['continuation']
    assert len(items)==4
    assert [r['match_offset'] for r in items]==[0,11,22,0]
    assert all(r['id']<=2 for r in items)
    assert s.checked>=4

def test_open_unicode_pages_and_source_change():
    s=Source({C:[row(1,'🙂"\\'*4000),row(2,'end'),row(3,'secret later')]})
    args=dict(action='open',conversation_ref=C,limit=1);texts=[]
    for _ in range(20):
        out=read(s,args);assert len(recall.packed(out))<=recall.MAX_OUTPUT;texts += [x['text'] for x in out['items']]
        if out['complete']:break
        args['continuation']=out['continuation']
    assert ''.join(texts)==s.rows[C][0]['text']+'end'
    s.rows[C][0]['text']='changed'
    with pytest.raises(recall.Unavailable,match='source_changed'):read(s,args)

def test_forged_cursor_cannot_expand_current_boundary():
    s=Source({C:[row(1,'needle'),row(2,'needle'),row(3,'later needle')]})
    args=dict(action='find',query='needle',limit=1);out=read(s,args)
    sig,cur=recall.request({**args,'continuation':out['continuation']});cur['b']=999999
    again=read(s,{**args,'continuation':recall.token(cur)})
    assert all(r['id']<=2 for r in again['items']) and s.caps[-1]==2

def test_limits_and_query_binding():
    s=Source({C:[row(1,'literal * /CoS needle needle')]});a=dict(action='find',query='needle',limit=1)
    out=read(s,a)
    with pytest.raises(recall.Unavailable,match='cursor_invalid'):read(s,{**a,'query':'CoS','continuation':out['continuation']})
    with pytest.raises(recall.Unavailable):read(s,dict(action='open',conversation_ref='../private'))
    assert read(s,dict(action='find',query='* /CoS'))['items']

def test_deadline_no_false_empty_answer():
    s=Source({C:[row(1,'needle')]});ticks=iter([0,3])
    with pytest.raises(recall.Unavailable,match='deadline'):
        recall.read(s,dict(action='find',query='needle'),current_conversation=C,request_id='request',clock=lambda:next(ticks))

def test_work_budget_continuation_keeps_next_conversation():
    s=Source({C:[row(1,'a')],D:[row(3,'x'*100)]})
    from unittest.mock import patch
    with patch.object(recall,'MAX_WORK',100):out=read(s,dict(action='find',query='missing'))
    assert not out['complete'] and out['continuation']
    assert recall.request(dict(action='find',query='missing',continuation=out['continuation']))[1]['c']==D
    assert read(s,dict(action='find',query='missing',continuation=out['continuation']))['complete']


def test_more_than_100_eligible_conversations_continue_without_skipping():
    rows={f'c-{i:012x}':[row(1,'needle')] for i in range(110)}
    s=Source(rows);args=dict(action='find',query='absent');out=read(s,args)
    assert not out['complete']
    cur=recall.request({**args,'continuation':out['continuation']})[1]
    assert cur['c']=='c-000000000064'
    assert read(s,{**args,'continuation':out['continuation']})['complete']
