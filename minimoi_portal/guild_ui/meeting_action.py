"""MC proposes; authenticated platform authority creates the recorded meeting."""
import hashlib
import json
import os
import time
import re
from flask import current_app, session, has_app_context
import sqlite3
from pathlib import Path

class RoomsUnavailable(RuntimeError):
    pass

REMINDER = ('When the owner explicitly asks to start, arrange, or set up a meeting, propose exactly '
            'MEETING mc,claude-code,codex: Short title as your entire reply. Include only named participants; '
            'all three means mc,claude-code,codex. The platform checks access and returns the link and status. '
            'Never say participants have joined before actual room replies. This is a recorded session.')
_PATTERN = re.compile(r'^MEETING (mc|claude-code|codex)(?:,(mc|claude-code|codex)){0,2}: ([^\r\n]{1,120})$')


def enabled():
    return has_app_context() and os.environ.get('MINIMOI_MC_MEETINGS') == '1' and bool(current_app.extensions.get('rooms_call'))


def authorized_text(text):
    # Model output alone never authorizes creation. Explicit owner action needed.
    if re.search(r"\b(don[’' ]?t|do not|never|not yet|cancel)\b", text, re.I):
        return False
    return bool(re.search(r'\b(start|create|arrange|open|set\s*up|setup|organize|call)\b.{0,100}\b(meeting|room)\b', text, re.I|re.S))


def parse(text):
    if not _PATTERN.fullmatch(text.strip()):
        raise ValueError('Invalid meeting proposal')
    names, title = text.strip()[8:].split(': ',1)
    participants=names.split(',')
    if len(set(participants)) != len(participants):
        raise ValueError('Duplicate participants')
    return participants,title


def execute(text, note, note_id, conv):
    if not authorized_text((note or {}).get('text','')):
        return 'No meeting was created. Please explicitly ask to start a recorded meeting.'
    participants,title=parse(text)
    owner_text=note['text'].lower()
    for name in participants:
        words={'mc':r'\b(mc|craftsman|you|all|everyone)\b','claude-code':r'\b(claude|all|everyone)\b','codex':r'\b(codex|all|everyone)\b'}
        if not re.search(words[name],owner_text):
            return 'No meeting was created: the proposed participants do not match your request.'
    decision,_=current_app.extensions['rooms_who']()
    principal=decision.principal
    if not decision.ok or principal != 'robert':
        return 'No meeting was created. Meeting setup requires your active owner Rooms grant.'
    class Client:
        def call(self,method,path,body=None,key=None):
            status,data=current_app.extensions['rooms_call'](method,'/api/v1'+path,body,operation_key=key)
            if status not in (200,201) or not isinstance(data,dict):
                raise RoomsUnavailable('Rooms action unconfirmed')
            return data
    client=Client()
    account='account:'+session['user']['username']
    from . import meeting_followup as MF
    access_dir=str(Path(current_app.extensions['rooms_access'].directory))
    # One room per owner REQUEST (the kept note), not per conversation: a retry of the same message finds the same room, a new request for a new meeting makes a new one.
    operation='mc-meeting:'+hashlib.sha256((principal+':'+conv['id']+':'+str(note_id)).encode()).hexdigest()
    clean_title=re.sub(r'\s+',' ',title).strip()[:120] or 'Meeting with Master Craftsman'
    payload={'title':clean_title, 'purpose':'Recorded meeting arranged through Master Craftsman. Topic: '+clean_title,
             'mode':'meeting','recording_acknowledged':True}
    try:
        with MF.database(access_dir) as db:
            existing=db.execute('SELECT room, seeded, invitees FROM meeting_rooms WHERE account=? AND conversation=? AND note_id=?',(account,conv['id'],str(note_id))).fetchone()
        if existing:
            room=existing['room']
            current=client.call('GET',f'/rooms/{room}')
            if current.get('room',current).get('state') == 'closed':
                return 'The linked meeting is closed. Continue it from Rooms or start a new chat for a new meeting.'
        else:
            created=client.call('POST','/rooms',payload,key=operation)
            room=created['result']['id']
            # Network IO never holds the grant/revocation database lock.
            with MF.database(access_dir) as db:
                db.execute('INSERT OR IGNORE INTO meeting_rooms(account,conversation,note_id,room,title,invitees,created_at) VALUES(?,?,?,?,?,?,?)',
                           (account,conv['id'],str(note_id),room,clean_title,'[]',time.time()))
                room=db.execute('SELECT room FROM meeting_rooms WHERE account=? AND conversation=? AND note_id=?',(account,conv['id'],str(note_id))).fetchone()['room']
        fresh,_=current_app.extensions['rooms_who']()
        if not fresh.ok or fresh.principal != principal:
            return 'Rooms access changed. Meeting setup is unconfirmed; no invitations were sent.'
    except (RoomsUnavailable, KeyError, TypeError):
        return 'Meeting setup could not be confirmed. Retry this request to check the same room.'
    failures=[]
    try:
        approvals=client.call('GET','/approved-agents')
        approved={r['agent'] for r in approvals.get('agents',[]) if r.get('approved') is True}
    except (RoomsUnavailable, KeyError, TypeError, AttributeError):
        return f'[Open your recorded meeting]({"/guild-next/guild/rooms?room="+room}). Agent approval could not be checked; no invitations were sent.'
    not_approved=[]
    invited=[]
    for name in participants:
        if name not in approved:
            not_approved.append(name)
            continue
        try:
            client.call('POST',f'/rooms/{room}/invite',{'actor':name},key=operation+':'+name)
            invited.append(name)
        except RoomsUnavailable:
            failures.append(name)
    link=f'/guild-next/guild/rooms?room={room}'
    # Seed the room so the agents have something to answer (one message per request: the key is the operation). The owner's own words and MC's last answer, not a model's rewrite.
    seeded=False
    already=bool(existing and existing['seeded'])
    if already:
        seeded=True
        invited=json.loads(existing['invitees'] or '[]') or invited      # a replay of the same request: the room is not seeded a second time
    elif invited:
        try:
            body=MF.opening_text(title=clean_title, owner_text=note.get('text',''), mc_last=_mc_last_answer(conv, note_id), items=_github_items(note.get('text','')), invitees=invited)
            posted=client.call('POST',f'/rooms/{room}/events',{'kind':'message','body':'@'+' @'.join(invited)+'\n'+body},key=operation+':open')
            seq=int((posted.get('result') or posted).get('seq') or 0)
            with MF.database(access_dir) as db:
                db.execute('UPDATE meeting_rooms SET invitees=?, seeded=1, seed_seq=? WHERE account=? AND conversation=? AND note_id=?',
                           (json.dumps(invited),seq,account,conv['id'],str(note_id)))
            seeded=True
        except (RoomsUnavailable, KeyError, TypeError, ValueError):
            seeded=False
    try:
        participants_now=client.call('GET',f'/rooms/{room}/turns').get('participants',[])
        line=MF.status_words(invited,participants_now,not_approved)
    except (RoomsUnavailable, KeyError, TypeError, AttributeError):
        line=''
    return (f'[Open your recorded meeting: {clean_title}]({link}). '
            + ('Some invitations could not be confirmed: '+', '.join(failures)+'. ' if failures else '')
            + (('Now: '+line+'. ') if line else '')
            + (('The room opens with the topic and context. ' if seeded else ('The room is open but its opening context could not be posted, so no one has been asked anything yet. ' if invited else '')))
            + ('Not approved: '+', '.join(not_approved)+'. No invitations sent to those agents. ' if not_approved and not line else '')
            + ('I will post here when each agent has replied or cannot. A reply can take a minute or two. Nobody counts as joined until they have replied in the room.' if seeded else ''))


def _mc_last_answer(conv, note_id):
    """MC's most recent answer in this conversation before the owner's request (kept notes only), or an empty string."""
    try:
        from . import api
        notes=api._conv_floor(conv).list_notes(limit=12).data['notes']
        mine=[n for n in notes if n.get('who')=='master_craftsman' and str(n.get('request_id'))!=str(note_id)]
        return mine[-1]['text'] if mine else ''
    except Exception:
        return ''


def _github_items(owner_text):
    """(number, title, url) for the GitHub items the owner's words point at, from the same bounded public read MC uses; nothing when it is off or unreadable."""
    try:
        from . import mc_context as C
        if not C.enabled():
            return []
        import requests
        nums,_=C.wanted(owner_text)
        out=[]
        for n in nums:
            status,data=C._get(f"{C.API}/repos/{C.repo()}/issues/{n}", requests.get, time.time())
            if status=='ok' and isinstance(data,dict):
                out.append((n,str(data.get('title') or ''),str(data.get('html_url') or '')))
        return out
    except Exception:
        return []


def follow(conv, floor):
    """(notes, still_following) for this conversation's open meetings: one platform note per milestone (an agent replied, could not, and one digest), filed in the SOURCE
    conversation. Called from the page's regular poll; read-only toward Records; idempotent by note id. Returns ([], 0) when meetings are off or on any trouble."""
    if not enabled() or not conv or not session.get('user'):
        return [], 0
    try:
        from . import meeting_followup as MF
        from .stores import GUILD_PLATFORM
        call_rooms=current_app.extensions['rooms_call']

        def call(method,path):
            status,data=call_rooms(method,'/api/v1'+path)
            if status!=200 or not isinstance(data,dict):
                raise RoomsUnavailable('unconfirmed')
            return data

        def add_note(note_id,text):
            kept=floor.add_note(note_id,text,GUILD_PLATFORM)
            return floor.get_note(note_id) if getattr(kept,'outcome',None)=='kept' else None
        return MF.sync(conv,'account:'+session['user']['username'],str(Path(current_app.extensions['rooms_access'].directory)),call,add_note)
    except Exception:
        return [], 0
