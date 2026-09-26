"""Actual local HTTP/CLI integration; synthetic records, no vendor model calls."""
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import subprocess
import sys
import threading
from uuid import uuid4

from werkzeug.serving import make_server, WSGIRequestHandler
from app import create_app


class QuietHandler(WSGIRequestHandler):
    def log_request(self,*args,**kwargs): pass


def test_provision_import_upload_receipt_rotate_revoke(tmp_path):
    server=make_server('127.0.0.1',0,lambda env,start: [],request_handler=QuietHandler)
    app=create_app(tmp_path/'private',port=server.server_port,testing=True)
    server.app=app
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    store=app.extensions['records_store'];root=Path(__file__).resolve().parents[1]
    room=store.create_room('robert','room',dict(title='Synthetic CLI',purpose='Transport acceptance',recording_acknowledged=True))['result']['id']
    store.add_principal('robert','principal',dict(id='claude-code',label='Claude Code'))
    store.membership('robert','member',room,dict(actor='claude-code'))
    url=f'http://127.0.0.1:{server.server_port}'
    owner=[sys.executable,str(root/'accessctl.py'),'--url',url,'--owner-token-file',str(store.root/'owner-key.txt')]
    request=tmp_path/'issue.json';token=tmp_path/'client.token'
    request.write_text(json.dumps(dict(principal='claude-code',label='Synthetic CLI',expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),grants={room:['read','post','upload','receipt']})))
    def run(command,ok=True):
        result=subprocess.run(command,capture_output=True,text=True,timeout=20)
        assert (result.returncode==0)==ok,(result.stdout,result.stderr)
        return json.loads(result.stdout) if ok else result
    try:
        issued=run(owner+['issue','--request',str(request),'--credential-file',str(token)])
        assert 'access_token' not in issued and token.stat().st_mode&0o077==0
        client=[sys.executable,str(root/'roomctl.py'),'--url',url,'--token-file',str(token)]
        joined=run(client+['--operation-id','cli-join','join',room])
        assert joined['result']['principal']=='claude-code'
        assert run(client+['receipt',room,'cli-join'])==joined
        payload=tmp_path/'import.json';payload.write_text(json.dumps(dict(source_application='claude-code-cli-synthetic',coverage='One test turn, no live vendor history',turns=[dict(speaker='robert',text='Declared only')],handoff='Suggested next step')))
        saved=run(client+['--operation-id','cli-import','import',room,str(payload)])
        assert run(client+['receipt',room,'cli-import'])==saved
        assert run(client+['read',room])['events'][-1]['actor']=='claude-code'
        artifact=tmp_path/'source.txt';artifact.write_text('Synthetic source')
        assert run(client+['--operation-id','file-1','file',room,str(artifact),'--source','Synthetic fixture'])['receipt']
        inventory=run(owner+['list'])
        assert any(x['credential_id']==issued['credential_id'] for x in inventory['credentials'])
        assert token.read_text().strip() not in json.dumps(inventory)
        run(owner+['revoke',issued['credential_id']])
        run(client+['receipt',room,'cli-import'],ok=False)
        assert store.authenticate(token.read_text().strip()) is None
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)
