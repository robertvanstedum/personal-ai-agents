"""Records application; production requires explicit validated prepared runtime."""
from __future__ import annotations

import argparse
from datetime import timedelta
from io import BytesIO
import json
import logging
from pathlib import Path
import re
from urllib.parse import urlsplit

from flask import Flask, g, jsonify, request, send_file, session
from werkzeug.exceptions import HTTPException, NotFound

from store import Problem, Store
from platform_access import AccessError, request_credential, request_operation
import portal_identity

# Routes the signed-in mini-moi person may reach through the portal (the browser's own routes). The worker and
# credential-management routes are not on it: an installation or a worker is not a person. Unknown routes fail closed.
PORTAL_HUMAN_ROUTES = frozenset({
    "archive_reconcile","archive_check","archive_preview","archive_session","restore_session","approved_agents_brief","approved_agents_list","approved_agents_change","me","status","rooms","persistent_rooms","create_persistent_room","persistent_room","create_session","session_record","operation",
    "create_room","get_room","events","disclosure","transfer","import_conversation","state","moderator",
    "coordination_inbox","coordination_list","coordination_create","coordination_transition","executive_snapshot",
    "cos_status","cos_request","cos_reconcile","cos_auto","acknowledge_join","principals","principal","portal_principal","membership",
    "documents","notes","artifact_link","artifact_history","document","search","export","backup",
    "meeting_invite","meeting_rsvp","meeting_presence","meeting_turns","meeting_turn_cancel","meeting_turn_retry",
    "meeting_stop","meeting_continue","meeting_renew","teammates","teammate_card","teammate_prove","teammate_proof"})


def configured_hosts(value):
    """Extra exact Host values (host:port) this app answers, from
    RECORDS_ALLOWED_HOSTS: for the staging container behind the portal bridge
    (for example minimoi-records:18880). Each must be a plain lowercase
    hostname with a port; anything else refuses to start."""
    hosts=set()
    for item in (value or "").split(","):
        item=item.strip()
        if not item: continue
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,62}:[0-9]{2,5}",item):
            raise ValueError("RECORDS_ALLOWED_HOSTS takes exact host:port values")
        hosts.add(item)
    return hosts


def create_app(data_dir, port=18880, testing=False, cos_sessions=None, ui_preview=False, allowed_hosts=None, portal_key=None, installer_key=None, archive_external_pending=None, runtime_environ=None):
    import os as _os
    runtime=dict(_os.environ if runtime_environ is None else runtime_environ)
    production=runtime.get("RECORDS_PORTAL_ENV","dev")=="production" or runtime.get("MINIMOI_RECORDS_PROFILE")=="production" or runtime.get("MINIMOI_ROLE")=="production"
    if production:
        from services.records_runtime import preflight
        if testing or ui_preview or portal_key is not None or installer_key is not None or allowed_hosts not in (None, {"minimoi-records:18880"}):
            raise ValueError("production_runtime_overrides_refused")
        preflight(data_dir,runtime,port=port)
    elif runtime.get("RECORDS_PORTAL_ENV","dev")!="dev":
        raise ValueError("records_environment_refused")
    app=Flask(__name__,static_folder="static",static_url_path="/static")
    store=Store(data_dir)
    app.config.update(SECRET_KEY=store.session_key,MAX_CONTENT_LENGTH=12_000_000,
                      SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE="Strict",
                      SESSION_COOKIE_NAME="minimoi_room_poc",PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
                      TESTING=testing)
    app.extensions["records_store"]=store
    store.archive.external_pending=archive_external_pending
    app.extensions["archive"]=store.archive
    from web_sessions import WebSessions
    web_sessions=WebSessions(store.connect,app.config["PERMANENT_SESSION_LIFETIME"])
    app.extensions["web_sessions"]=web_sessions
    import os as _os
    with store.connect() as _db: _db.executescript(portal_identity.SCHEMA)
    portal_env=runtime.get("RECORDS_PORTAL_ENV","dev")
    def current_portal_key():
        # Read at each use, so putting the key file in place (or rotating it) needs no code change; no file means a portal identity is refused.
        return portal_key() if callable(portal_key) else (portal_key if portal_key is not None else portal_identity.load_key(runtime.get("RECORDS_PORTAL_KEY_FILE")))
    previous_hook=store.platform_access.on_revoke
    def on_revoke(db,principal,credential_id=None):
        if previous_hook: previous_hook(db,principal)
        if credential_id: WebSessions.end_for_credential(db,credential_id)
    store.platform_access.on_revoke=on_revoke
    from cos_requests import CoSRequests
    if cos_sessions is None:
        import os
        cos_sessions=[]
        config_path=os.environ.get("RECORDS_COS_CONFIG")
        if config_path:
            from integration.cos_records_bridge import private_file
            cos_sessions=json.loads(private_file(config_path).read_text())["allowed_sessions"]
    cos_queue=CoSRequests(store,cos_sessions)
    app.extensions["cos_requests"]=cos_queue
    from coordination import Coordination
    coordination=Coordination(store)
    app.extensions["coordination"]=coordination
    import os
    extra_hosts=configured_hosts(runtime.get("RECORDS_ALLOWED_HOSTS")) if allowed_hosts is None else set(allowed_hosts)
    allowed_hosts=extra_hosts if production else {f"127.0.0.1:{port}",f"localhost:{port}"}|extra_hosts
    if testing: allowed_hosts.add("localhost")

    @app.before_request
    def guard():
        if request.host not in allowed_hosts:
            raise Problem("Records does not serve this host",403)
        origin=request.headers.get("Origin")
        if origin and origin != f"http://{request.host}":
            raise Problem("Cross-origin requests are not allowed",403)
        if request.path in {"/preview","/preview/"} or request.path.startswith("/static/v2/"):
            # Opt-in UI preview (package U): simulated fixtures only, no login, never reads the store.
            if not ui_preview: raise NotFound()
            return
        if request.path in {"/","/health"} or request.path.startswith("/static/"):
            return
        if request.method in {"POST","PUT","PATCH","DELETE"}:
            if request.mimetype != "application/json":
                raise Problem("Use application/json",415)
            payload=request.get_json(silent=True)
            if not isinstance(payload,dict): raise Problem("Expected a JSON object")
        if request.path == "/api/login":
            if production:raise Problem("Production Records has no standalone browser login",403)
            return
        if request.endpoint in {'agent_install_pending','agent_install_issue','agent_install_ack'}:
            import hmac
            expected=installer_key
            if expected is None:
                try:
                    key_path=Path(os.environ.get('RECORDS_AGENT_INSTALLER_KEY','/run/secrets/agent-installer/token'))
                    if key_path.is_symlink(): raise OSError()
                    expected=key_path.read_text().strip()
                except OSError: expected=''
            supplied=request.headers.get('Authorization','')
            if not expected or len(expected)<32 or not hmac.compare_digest(supplied.encode('utf-8'),('Bearer '+expected).encode('utf-8')):
                raise Problem('Installer authentication required',401)
            if request.headers.get(portal_identity.HEADER) is not None or origin:
                raise Problem('Installer channel is not a browser or portal route',403)
            return
        authorization=request.headers.get("Authorization","")
        actor=None
        portal_header=request.headers.get(portal_identity.HEADER)
        if portal_header is not None:
            # Signed in to mini-moi: the portal vouches for exactly this request. A bad assertion is refused outright; it never falls back
            # to a cookie or a key, and a person never acquires an agent's identity.
            try:
                principal=portal_identity.verify(portal_header,current_portal_key(),env=portal_env,method=request.method,
                    target=portal_identity.canonical_target(request.path,request.query_string),body=request.get_data(),connect=store.connect)
                with store.connect() as db: actor=portal_identity.actor_for(db,principal)
            except portal_identity.Refused as refused:
                raise Problem(refused.message,refused.status)
        elif authorization.startswith("Bearer "):
            actor=store.authenticate(authorization[7:])
        elif session.get("credential_id"):
            # Rooms R3b: a browser session must be live on the server too. A cookie
            # from before R3b (no session id), a logged-out or revoked one, is refused.
            if web_sessions.valid(session.get("ws"),session["credential_id"]):
                actor=store.platform_access.authenticate(credential_id=session["credential_id"])
            else:
                session.clear()
        if not actor: raise Problem("Sign in with a local access key",401)
        g.actor=actor
        g.auth_context=request_credential.set(actor["credential_id"])
        g.operation_context=request_operation.set("read")
        if actor.get("scope")=="portal":
            if request.endpoint not in PORTAL_HUMAN_ROUTES: raise Problem("Route unavailable to a signed-in person through the portal",403)
        elif not actor["legacy"]:
            # Explicit allowlist for installation clients. Unknown routes fail closed.
            endpoint=request.endpoint
            with store.connect() as db:
                approved_key=db.execute("SELECT 1 FROM agent_credential_generations WHERE credential=?", (actor["credential_id"],)).fetchone()
            if approved_key and endpoint not in {
                "get_room","session_record","events","documents","document","artifact_link","operation",
                "rooms","me","logout","meeting_rsvp","turns_claim","turns_start","turns_heartbeat",
                "turns_fail","turns_cancel_ack","turns_recover","turns_reconciled","hosted_teammates","hosted_reachable"}:
                raise Problem("Route unavailable to approved Rooms agents",403)
            operations={"get_room":"read","session_record":"read","events":"post","import_conversation":"post","transfer":"post",
                        "documents":"upload","artifact_link":"link","operation":"receipt",
                        "export":"export","document":"read","rooms":"read","me":"read",
                        "logout":"read","coordination_inbox":"read","coordination_list":"read","coordination_create":"post","coordination_transition":"post","acknowledge_join":"read",
                        # Rooms R1: a teammate answers its own invitation; the worker's routes need a work scope.
                        "meeting_rsvp":"rsvp","turns_claim":"work","turns_start":"work","turns_heartbeat":"work",
                        "turns_fail":"work","turns_cancel_ack":"work","turns_recover":"work","turns_reconciled":"work",
                        "hosted_teammates":"work","hosted_reachable":"work"}
            if endpoint not in operations: raise Problem("Route unavailable to installation clients",403)
            request_operation.set(operations[endpoint])
            if endpoint=="operation" and not request.args.get("destination"):
                raise Problem("Receipt lookup requires an explicit destination")

    @app.get('/api/v1/approved-agents')
    def approved_agents_list():
        return jsonify({'agents':store.approved_agents.inventory(g.actor['id'])})

    @app.post('/api/v1/approved-agents/<agent>')
    def approved_agents_change(agent):
        payload=request.get_json()
        return jsonify(store.approved_agents.change(g.actor['id'],agent,payload.get('enabled'),payload.get('brief')))

    @app.post('/api/v1/approved-agents/<agent>/brief')
    def approved_agents_brief(agent):
        return jsonify(store.approved_agents.edit_brief(g.actor['id'],agent,request.get_json().get('brief')))

    @app.get('/api/v1/agent-installer/pending')
    def agent_install_pending():
        return jsonify({'pending':store.approved_agents.pending()})

    @app.post('/api/v1/agent-installer/<agent>/issue')
    def agent_install_issue(agent):
        response=jsonify(store.approved_agents.issue(agent,request.get_json().get('generation')))
        response.headers['Cache-Control']='no-store'
        return response

    @app.post('/api/v1/agent-installer/<agent>/ack')
    def agent_install_ack(agent):
        return jsonify(store.approved_agents.acknowledge(agent,request.get_json()))

    @app.teardown_request
    def clear_authority(error):
        if hasattr(g,"auth_context"): request_credential.reset(g.auth_context)
        if hasattr(g,"operation_context"): request_operation.reset(g.operation_context)


    @app.after_request
    def headers(response):
        response.headers["Cache-Control"]="no-store"
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["Referrer-Policy"]="no-referrer"
        response.headers["Content-Security-Policy"]="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.errorhandler(AccessError)
    @app.errorhandler(Problem)
    def problem(error): return jsonify(error=error.message),error.status

    @app.errorhandler(HTTPException)
    def http_error(error): return jsonify(error=error.description),error.code

    @app.errorhandler(Exception)
    def unexpected(error):
        if testing: raise error
        app.logger.error("Request failed: %s",type(error).__name__)
        return jsonify(error="Internal failure; the operation was not confirmed. Retry with the same idempotency key."),500

    def actor(): return g.actor["id"]
    def key(): return request.headers.get("Idempotency-Key","")
    def body(): return request.get_json()

    @app.get("/")
    def index(): return app.send_static_file("index.html")

    @app.get("/health")
    def health(): return jsonify(status="ok",environment="local-test",production_connected=False)

    if ui_preview:
        @app.get("/preview/")
        def ui_preview_page(): return app.send_static_file("v2/preview.html")

    @app.post("/api/login")
    def login():
        principal=store.authenticate(body().get("token"))
        if not principal: raise Problem("Invalid local access key",401)
        session.clear(); session["actor"]=principal["id"]; session["credential_id"]=principal["credential_id"]; session.permanent=True
        session["ws"]=web_sessions.start(principal["id"],principal["credential_id"])
        return jsonify(principal)

    @app.post("/api/logout")
    def logout():
        web_sessions.end(session.get("ws"))          # Rooms R3b: ends it on the server, not only in this browser
        session.clear()
        return jsonify(ok=True)

    @app.get("/api/v1/me")
    def me(): return jsonify(g.actor)

    @app.get("/api/v1/status")
    def status():
        store.owner(actor())
        with store.connect() as db:
            counts={table:db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in ("rooms","events","documents","operations")}
            mode=db.execute("PRAGMA journal_mode").fetchone()[0]
        return jsonify(environment="local-test",schema_version=5,counts=counts,journal_mode=mode,
                       production="not_connected",replication="not_implemented",models="not_connected",
                       independent_backup=False,data_directory=str(store.root))

    @app.get("/api/v1/rooms")
    def rooms():
        result=store.rooms(actor(), request.args.get("view", "active"))
        if g.actor.get("scope")=="portal":
            pass        # the owner sees every room; anyone else only rooms they belong to (store.rooms)
        elif not g.actor["legacy"]:
            if g.actor.get("scope")=="work": raise Problem("A worker credential does not list rooms",403)
            if g.actor.get("scope")=="membership":
                from meetings import rsvp_of
                with store.connect() as db:
                    result=[room for room in result if rsvp_of(db,room["id"],actor())["rsvp"]=="accepted"]
            else:
                grants=json.loads(g.actor["grants"])
                result=[room for room in result if "read" in grants.get(room["id"],[])]
        return jsonify(rooms=result)

    @app.post("/api/v1/rooms/<room>/archive-reconcile")
    def archive_reconcile(room): return jsonify(store.archive.reconcile(actor(),room,body()))

    @app.get("/api/v1/rooms/<room>/archive-check")
    def archive_check(room): return jsonify(store.archive.work_snapshot(actor(),room))

    @app.get("/api/v1/rooms/archive-preview")
    def archive_preview(): return jsonify(store.archive.preview(actor()))

    @app.post("/api/v1/rooms/<room>/archive")
    def archive_session(room):
        from archive import VERIFIED_EXTERNAL, portal_proof
        payload=dict(body());proof=payload.pop('_portal_archive_proof',None)
        token=None
        try:
            if proof is not None:
                if g.actor.get('scope')!='portal' or actor()!='robert':
                    raise Problem('Portal archive evidence required',403)
                token=VERIFIED_EXTERNAL.set(portal_proof(proof,room))
            return jsonify(store.archive.change(actor(),key(),room,payload))
        finally:
            if token is not None:VERIFIED_EXTERNAL.reset(token)

    @app.post("/api/v1/rooms/<room>/restore")
    def restore_session(room): return jsonify(store.archive.change(actor(),key(),room,body(),restore=True))

    # v1 /rooms remains the stable session alias for existing clients/receipts.
    @app.get("/api/v2/rooms")
    def persistent_rooms(): return jsonify(rooms=store.persistent_rooms(actor(),request.args.get("view","active")))

    @app.post("/api/v2/rooms")
    def create_persistent_room(): return jsonify(store.create_persistent_room(actor(),key(),body())),201

    @app.get("/api/v2/rooms/<room>")
    def persistent_room(room): return jsonify(store.persistent_room(actor(),room,request.args.get("view","active")))

    @app.post("/api/v2/rooms/<room>/sessions")
    def create_session(room): return jsonify(store.create_session(actor(),key(),room,body())),201

    @app.get("/api/v2/sessions/<room>")
    def session_record(room): return jsonify(store.room(actor(),room))

    @app.get("/api/v1/operations/<operation_key>")
    def operation(operation_key): return jsonify(store.operation(actor(),operation_key,request.args.get("destination")))

    @app.post("/api/v1/rooms")
    def create_room(): return jsonify(store.create_room(actor(),key(),body())),201

    @app.get("/api/v1/rooms/<room>")
    def get_room(room):
        try: after=int(request.args.get("after",0))
        except (TypeError,ValueError): raise Problem("Invalid event cursor")
        if after<0: raise Problem("Invalid event cursor")
        return jsonify(store.room(actor(),room,after))

    @app.post("/api/v1/rooms/<room>/events")
    def events(room): return jsonify(store.append(actor(),key(),room,body())),201

    @app.post("/api/v1/rooms/<room>/disclosures")
    def disclosure(room): return jsonify(store.disclosure(actor(),key(),room,body())),201

    @app.post("/api/v1/rooms/<room>/transfers")
    def transfer(room): return jsonify(store.transfer(actor(),key(),room,body())),201

    @app.post("/api/v1/rooms/<room>/imports")
    def import_conversation(room): return jsonify(store.import_conversation(actor(),key(),room,body())),201

    @app.post("/api/v1/rooms/<room>/state")
    def state(room): return jsonify(store.state(actor(),key(),room,body()))

    @app.post("/api/v1/rooms/<room>/moderator")
    def moderator(room): return jsonify(store.moderator(actor(),key(),room,body()))

    @app.get("/api/v1/coordination/inbox")
    def coordination_inbox(): return jsonify(coordination.inbox(actor()))

    @app.get("/api/v1/rooms/<room>/coordination")
    def coordination_list(room): return jsonify(coordination.list(actor(),room))

    @app.post("/api/v1/rooms/<room>/coordination")
    def coordination_create(room): return jsonify(coordination.create(actor(),key(),room,body())),201

    @app.post("/api/v1/rooms/<room>/coordination/<item>")
    def coordination_transition(room,item): return jsonify(coordination.transition(actor(),key(),room,item,body()))

    @app.post("/api/v1/rooms/<room>/executive-snapshots")
    def executive_snapshot(room): return jsonify(coordination.snapshot(actor(),key(),room,body())),201

    @app.get("/api/v1/rooms/<room>/cos-requests")
    def cos_status(room): return jsonify(cos_queue.status(actor(),room))

    @app.post("/api/v1/rooms/<room>/cos-requests")
    def cos_request(room): return jsonify(cos_queue.submit(actor(),room,key(),body())),202

    @app.post("/api/v1/rooms/<room>/cos-requests/<request_id>/reconcile")
    def cos_reconcile(room,request_id):
        if body(): raise Problem("Reconciliation takes no new request content")
        return jsonify(cos_queue.reconcile(actor(),room,request_id)),202

    @app.post("/api/v1/rooms/<room>/cos-auto")
    def cos_auto(room): return jsonify(cos_queue.set_auto(actor(),room,body()))

    @app.post("/api/v1/rooms/<room>/join")
    def acknowledge_join(room):
        return jsonify(store.acknowledge_join(actor(), key(), room, body())), 201

    @app.get("/api/v1/platform/credentials")
    def credential_inventory(): return jsonify(credentials=store.platform_access.inventory(actor()))

    @app.post("/api/v1/platform/credentials")
    def issue_credential(): return jsonify(store.platform_access.issue(actor(),body())),201

    @app.post("/api/v1/platform/credentials/<credential>/revoke")
    def revoke_credential(credential): return jsonify(store.platform_access.revoke(actor(),credential))

    @app.get("/api/v1/principals")
    def principals(): return jsonify(principals=store.principals(actor()))

    @app.post("/api/v1/principals")
    def principal(): return jsonify(store.add_principal(actor(),key(),body())),201

    @app.post("/api/v1/portal/principals")
    def portal_principal():
        # Only the owner's portal, speaking for the owner, creates the person a mini-moi grant needs (portal_identity); no bearer or cookie may.
        if g.actor.get("scope")!="portal": raise Problem("Only the portal creates mini-moi people",403)
        b=body()
        return jsonify(store.add_human_principal(actor(),b.get("id"),b.get("label")))

    @app.post("/api/v1/rooms/<room>/members")
    def membership(room): return jsonify(store.membership(actor(),key(),room,body()))

    @app.post("/api/v1/rooms/<room>/documents")
    def documents(room): return jsonify(store.document(actor(),key(),room,body())),201

    @app.post("/api/v1/rooms/<room>/notes")
    def notes(room): return jsonify(store.note(actor(),key(),room,body())),201

    @app.post("/api/v1/rooms/<room>/artifact-refs")
    def artifact_link(room): return jsonify(store.link_artifact(actor(),key(),room,body())),201

    @app.get("/api/v1/artifact-history")
    def artifact_history():
        return jsonify(records=store.artifact_history(actor(),request.args.get("kind"),
                       request.args.get("value"),request.args.get("revision")))

    @app.get("/api/v1/documents/<doc_id>")
    def document(doc_id):
        doc=store.get_document(actor(),doc_id)
        return send_file(BytesIO(doc["content"]),mimetype="application/octet-stream",
                         as_attachment=True,download_name=doc["name"])

    @app.get("/api/v1/search")
    def search(): return jsonify(results=store.search(actor(),request.args.get("q","")))

    @app.get("/api/v1/rooms/<room>/export")
    def export(room):
        if request.args.get("format")=="markdown":
            data=store.transcript(actor(),room).encode(); name=f"session-{room}.md"
        else:
            data=json.dumps(store.export(actor(),room),indent=2,ensure_ascii=False).encode(); name=f"session-{room}.json"
        return send_file(BytesIO(data),mimetype="application/octet-stream",as_attachment=True,download_name=name)

    @app.post("/api/v1/backup")
    def backup(): return jsonify(store.backup(actor()))

    # ── Rooms R1 (docs/specs/minimoi-connected-work/ROOMS_R1.md) ─────────────
    meetings=store.meetings

    @app.post("/api/v1/rooms/<room>/invite")
    def meeting_invite(room): return jsonify(meetings.invite(actor(),key(),room,body())),201

    @app.post("/api/v1/rooms/<room>/rsvp")
    def meeting_rsvp(room): return jsonify(meetings.rsvp(actor(),key(),room,body()))

    @app.put("/api/v1/rooms/<room>/presence")
    def meeting_presence(room): return jsonify(meetings.presence_here(actor(),room,body()))

    @app.get("/api/v1/rooms/<room>/turns")
    def meeting_turns(room): return jsonify(meetings.status(actor(),room))

    @app.post("/api/v1/rooms/<room>/turns/<turn>/cancel")
    def meeting_turn_cancel(room,turn): return jsonify(meetings.owner_cancel(actor(),key(),room,turn))

    @app.post("/api/v1/rooms/<room>/turns/<turn>/retry")
    def meeting_turn_retry(room,turn): return jsonify(meetings.owner_retry(actor(),key(),room,turn,body())),201

    @app.post("/api/v1/rooms/<room>/stop")
    def meeting_stop(room): return jsonify(meetings.stop(actor(),key(),room))

    @app.post("/api/v1/rooms/<room>/continue")
    def meeting_continue(room): return jsonify(meetings.continue_conversation(actor(),key(),room)),201

    @app.get("/api/v1/teammates")
    def teammates(): return jsonify(teammates=meetings.teammates(actor()))

    @app.put("/api/v1/teammates/<principal>")
    def teammate_card(principal): return jsonify(meetings.put_teammate(actor(),principal,body()))

    @app.post("/api/v1/teammates/<principal>/prove")
    def teammate_prove(principal): return jsonify(meetings.prove(actor(),key(),principal,body())),202

    @app.get("/api/v1/teammates/<principal>/prove")
    def teammate_proof(principal): return jsonify(meetings.proof_status(actor(),principal))

    @app.get("/api/v1/hosted-teammates")
    def hosted_teammates(): return jsonify(meetings.hosted_view(g.actor))

    @app.post("/api/v1/hosted-teammates/<principal>/reachable")
    def hosted_reachable(principal): return jsonify(meetings.mark_reachable(g.actor,principal,body()))

    @app.post("/api/v1/turns/claim")
    def turns_claim(): return jsonify(meetings.claim(g.actor,body()))

    @app.post("/api/v1/turns/<turn>/start")
    def turns_start(turn): return jsonify(meetings.start(g.actor,turn,body()))

    @app.post("/api/v1/turns/<turn>/heartbeat")
    def turns_heartbeat(turn): return jsonify(meetings.heartbeat(g.actor,turn,body()))

    @app.post("/api/v1/turns/<turn>/fail")
    def turns_fail(turn): return jsonify(meetings.fail(g.actor,turn,body()))

    @app.post("/api/v1/turns/<turn>/cancel-ack")
    def turns_cancel_ack(turn): return jsonify(meetings.cancel_ack(g.actor,turn,body()))

    @app.post("/api/v1/turns/<turn>/reconciled")
    def turns_reconciled(turn): return jsonify(meetings.reconciled(g.actor,turn,body()))

    @app.post("/api/v1/rooms/<room>/renew")
    def meeting_renew(room): return jsonify(meetings.renew(actor(),key(),room))

    @app.post("/api/v1/turns/<turn>/recover")
    def turns_recover(turn): return jsonify(meetings.recover(g.actor,turn,body()))

    return app


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir",required=True)
    parser.add_argument("--port",type=int,default=18880)
    parser.add_argument("--host",default="127.0.0.1",choices=["127.0.0.1","0.0.0.0"],
                        help="0.0.0.0 only inside the selected private container, with no public host port")
    parser.add_argument("--ui-preview",action="store_true",
                        help="Also serve the simulated-data UI preview at /preview/ (no login, no store reads)")
    args=parser.parse_args()
    if not 1024<=args.port<=65535: parser.error("Use an unprivileged local port")
    import os as _runtime_os
    if _runtime_os.environ.get("MINIMOI_ROLE")=="production" or _runtime_os.environ.get("RECORDS_PORTAL_ENV")=="production" or _runtime_os.environ.get("MINIMOI_RECORDS_PROFILE")=="production":
        parser.error("Use python -m services.records_runtime for prepared production state")
    application=create_app(args.data_dir,args.port,ui_preview=args.ui_preview)
    print(f"LOCAL TEST ONLY: http://127.0.0.1:{args.port}",flush=True)
    if args.ui_preview: print(f"UI PREVIEW (simulated data): http://127.0.0.1:{args.port}/preview/",flush=True)
    print(f"Access key file: {application.extensions['records_store'].root/'owner-key.txt'}",flush=True)
    # Avoid recording paths/search terms or request bodies in general access logs.
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    application.run(host=args.host,port=args.port,debug=False,use_reloader=False,threaded=True)
