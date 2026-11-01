"""Explicitly enabled Guildhall entry to the ordinary CoS coordination layer.

A trusted Records worker supplies authenticated event metadata, never a browser.
The durable ledger refuses reinference after uncertainty, including a restart.
Meeting sessions are separate from the private owner chat; the normal context
builder and runtime retain CoS's usual memory and tools.
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
    return turn


def install(app, *, token_file, data_dir, run_turn, is_ready=lambda: True):
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
    @bp.post('/internal/guildhall/turn')
    def turn():
        if request.content_length is None or request.content_length>256_000:return jsonify(error='Turn too large'),413
        try:
            value=validate(request.get_json(silent=True))
            encoded=json.dumps(value,sort_keys=True,separators=(',',':'))
            digest=hashlib.sha256(encoded.encode()).hexdigest()
        except (ValueError,KeyError,TypeError,AttributeError):return jsonify(error='Invalid authenticated meeting turn'),400
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
        context={k:value.get(k) for k in ('room','brief','transcript','coverage')}
        prompt=json.dumps(context,ensure_ascii=False)
        try:
            if value['trigger']['text'].strip() == '/new':
                from types import SimpleNamespace
                result=SimpleNamespace(reply='Each Guildhall turn has its own CoS session. Start a new meeting for a fresh shared conversation; your private CoS chat is unchanged.')
            else:
                result=run_turn(value['trigger']['text'],meeting_context=prompt,channel='guildhall',conversation_id='guildhall:'+value['room']+':'+value['id'],request_id=value['id'])
            answer={'text':result.reply,'turn_id':value['id'],'room':value['room']}
            if not isinstance(answer['text'],str) or not answer['text'].strip():raise ValueError('Empty reply')
            with db() as c:c.execute('UPDATE turns SET state=?,result=? WHERE id=?',('done',json.dumps(answer),value['id']))
            return jsonify(answer)
        except Exception:
            # Never expose provider errors or retry a possibly action-bearing turn.
            with db() as c:c.execute('UPDATE turns SET state=? WHERE id=?',('uncertain',value['id']))
            return jsonify(error='CoS outcome uncertain; no automatic retry'),502
    app.register_blueprint(bp)
