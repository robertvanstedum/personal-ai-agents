"""Explicitly enabled Guildhall entry to the ordinary CoS coordination layer.

A trusted Records worker supplies authenticated event metadata, never a browser.
The durable ledger refuses reinference after uncertainty, including a restart.
Meeting sessions are separate from the private owner chat. F2 D8: CoS brings
Robert's private context and read tools only into an owner-private room whose
members a verifier confirmed from the authoritative Records snapshot; any other
room, or one not verified, is closed (no tools, no owner context). Until F2 step
4 installs the Records verifier, every room is closed.
"""
import hashlib
import hmac
import json
import sqlite3
from pathlib import Path
from uuid import UUID
from flask import Blueprint, jsonify, request


def validate(turn):
    if not isinstance(turn, dict): raise ValueError('Invalid turn')
    for key in ('id','room'):
        if str(UUID(turn[key])) != turn[key]: raise ValueError('Invalid identity')
    trigger=turn.get('trigger') or {}
    if (turn.get('addressee')!='cos' or trigger.get('actor')!='robert'
            or trigger.get('kind')!='message' or trigger.get('origin') is not None
            or not isinstance(trigger.get('text'),str) or not trigger['text'].strip()):
        raise ValueError('Authenticated owner message required')
    if 'origin' not in trigger: raise ValueError('Missing event provenance')
    if 'claim_id' in turn and not (isinstance(turn['claim_id'],str) and 0<len(turn['claim_id'])<=128):
        raise ValueError('Invalid claim')
    return turn


def install(app, *, token_file, data_dir, run_turn, is_ready=lambda: True, verify_members=None, stop_turn=None,
            verifier=None):
    """``verify_members(turn)`` returns the room's members from Records, or None; never the request body's.
    ``verifier`` (F2 step 4, ``meeting_verifier.RecordsMeetingVerifier``) supersedes it: the turn runs only when
    Records admits it, with the owner's text from the snapshot, the meeting binding and a once-a-second watch."""
    token=Path(token_file).read_text().strip()
    if len(token)<32:raise ValueError('Guildhall service credential required')
    root=Path(data_dir);root.mkdir(parents=True,mode=0o700,exist_ok=True)
    dbpath=root/'turns.sqlite3'
    def db():return sqlite3.connect(dbpath,timeout=10)
    with db() as c:
        c.execute('CREATE TABLE IF NOT EXISTS turns(id TEXT PRIMARY KEY,digest TEXT NOT NULL,state TEXT NOT NULL,result TEXT)')
    dbpath.chmod(0o600)
    bp=Blueprint('cos_guildhall',__name__)
    @bp.before_request
    def authorize():
        supplied=request.headers.get('Authorization','')
        if not hmac.compare_digest(supplied,'Bearer '+token):return jsonify(error='Unauthorized'),401
    @bp.get('/internal/guildhall/ready')
    def ready():
        available=bool(is_ready())
        return jsonify(ready=available), 200 if available else 503
    @bp.post('/internal/guildhall/turns/<turn_id>/stop')
    def stop(turn_id):
        # F2 D9: the rooms worker's Stop, with this blueprint's guildhall service token (never in an agent
        # container). Revokes the turn's tool binding at once; a Stop before the binding exists applies when it is
        # written. Never claims the agent stopped.
        try:
            if str(UUID(turn_id)) != turn_id: raise ValueError
        except ValueError:
            return jsonify(error='Invalid turn'),400
        if stop_turn is None:
            return jsonify(state='not_bound'),202
        stop_turn(turn_id,'meeting_stop')
        return jsonify(state='tools_revoked'),202
    @bp.post('/internal/guildhall/turn')
    def turn():
        if request.content_length is None or request.content_length>256_000:return jsonify(error='Turn too large'),413
        try:
            value=validate(request.get_json(silent=True))
            # The claim changes when Records re-issues a claim for the same turn; the turn's identity does not.
            encoded=json.dumps({k:v for k,v in value.items() if k!='claim_id'},sort_keys=True,separators=(',',':'))
            digest=hashlib.sha256(encoded.encode()).hexdigest()
        except (ValueError,KeyError,TypeError,AttributeError):return jsonify(error='Invalid authenticated meeting turn'),400
        facts=None
        if verifier is not None:
            with db() as c:
                old=c.execute('SELECT digest,state,result FROM turns WHERE id=?',(value['id'],)).fetchone()
            if old is None:
                # Records decides before anything is recorded here: a refused turn leaves no uncertain row.
                facts=verifier.admit(value)
                if facts is None:return jsonify(error='Records did not admit this meeting turn'),409
                if facts['trigger_text']!=value['trigger']['text']:
                    return jsonify(error='Turn text does not match the Records snapshot'),409
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            old=c.execute('SELECT digest,state,result FROM turns WHERE id=?',(value['id'],)).fetchone()
            if old:
                if old[0]!=digest:return jsonify(error='Turn identity conflict'),409
                if old[1]=='done':return jsonify(json.loads(old[2]))
                return jsonify(error='Turn outcome needs reconciliation; no retry was started'),409
            c.execute('INSERT INTO turns VALUES(?,?,?,NULL)',(value['id'],digest,'started'))
        # Other members' attributed speech stays quoted context. Only the
        # authenticated owner trigger below is the current request.
        context=(facts['context'] if facts is not None
                 else {k:value.get(k) for k in ('room','brief','transcript','coverage')})
        prompt=json.dumps(context,ensure_ascii=False)
        try:
            if value['trigger']['text'].strip() == '/new':
                from types import SimpleNamespace
                result=SimpleNamespace(reply='Each Guildhall turn has its own CoS session. Start a new meeting for a fresh shared conversation; your private CoS chat is unchanged.')
            else:
                from core.room_scope import meeting_scope
                if facts is not None:
                    members=facts['members']
                    scope=meeting_scope(members)
                    binding=facts['binding'] if scope=='owner_private' else None
                    text=facts['trigger_text']
                else:
                    members=verify_members(value) if verify_members is not None else None
                    scope,binding,text=meeting_scope(members),None,value['trigger']['text']
                stop_watch=(verifier.watch(value['id'],facts['claim_id'],facts['room_generation'],
                                           (lambda turn,reason:stop_turn(turn,reason)) if stop_turn
                                           else (lambda turn,reason:None))
                            if facts is not None and binding is not None else (lambda:None))
                try:
                    result=run_turn(text,meeting_context=prompt,channel='guildhall',conversation_id='guildhall:'+value['room']+':'+value['id'],request_id=value['id'],turn_id=value['id'],meeting_scope=scope,binding=binding)
                finally:
                    stop_watch()
            answer={'text':result.reply,'turn_id':value['id'],'room':value['room']}
            if not isinstance(answer['text'],str) or not answer['text'].strip():raise ValueError('Empty reply')
            with db() as c:c.execute('UPDATE turns SET state=?,result=? WHERE id=?',('done',json.dumps(answer),value['id']))
            return jsonify(answer)
        except Exception:
            # Never expose provider errors or retry a possibly action-bearing turn.
            with db() as c:c.execute('UPDATE turns SET state=? WHERE id=?',('uncertain',value['id']))
            return jsonify(error='CoS outcome uncertain; no automatic retry'),502
    app.register_blueprint(bp)
