"""Loopback-only development application. Never mount on the production portal."""
from __future__ import annotations

import argparse
from datetime import timedelta
from io import BytesIO
import json
import logging
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, g, jsonify, request, send_file, session
from werkzeug.exceptions import HTTPException

from store import Problem, Store
from platform_access import AccessError, request_credential, request_operation


def create_app(data_dir, port=18880, testing=False, cos_sessions=None):
    app=Flask(__name__,static_folder="static",static_url_path="/static")
    store=Store(data_dir)
    app.config.update(SECRET_KEY=store.session_key,MAX_CONTENT_LENGTH=3_000_000,
                      SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE="Strict",
                      SESSION_COOKIE_NAME="minimoi_room_poc",PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
                      TESTING=testing)
    app.extensions["records_store"]=store
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
    allowed_hosts={f"127.0.0.1:{port}",f"localhost:{port}"}
    if testing: allowed_hosts.add("localhost")

    @app.before_request
    def guard():
        if request.host not in allowed_hosts:
            raise Problem("This test application only serves its loopback origin",403)
        origin=request.headers.get("Origin")
        if origin and origin != f"http://{request.host}":
            raise Problem("Cross-origin requests are not allowed",403)
        if request.path in {"/","/health"} or request.path.startswith("/static/"):
            return
        if request.method in {"POST","PUT","PATCH","DELETE"}:
            if request.mimetype != "application/json":
                raise Problem("Use application/json",415)
            payload=request.get_json(silent=True)
            if not isinstance(payload,dict): raise Problem("Expected a JSON object")
        if request.path == "/api/login": return
        authorization=request.headers.get("Authorization","")
        actor=None
        if authorization.startswith("Bearer "):
            actor=store.authenticate(authorization[7:])
        elif session.get("credential_id"):
            actor=store.platform_access.authenticate(credential_id=session["credential_id"])
        if not actor: raise Problem("Sign in with a local access key",401)
        g.actor=actor
        g.auth_context=request_credential.set(actor["credential_id"])
        g.operation_context=request_operation.set("read")
        if not actor["legacy"]:
            # Explicit allowlist for installation clients. Unknown routes fail closed.
            endpoint=request.endpoint
            operations={"get_room":"read","session_record":"read","events":"post","import_conversation":"post","transfer":"post",
                        "documents":"upload","artifact_link":"link","operation":"receipt",
                        "export":"export","document":"read","rooms":"read","me":"read",
                        "logout":"read"}
            if endpoint not in operations: raise Problem("Route unavailable to installation clients",403)
            request_operation.set(operations[endpoint])
            if endpoint=="operation" and not request.args.get("destination"):
                raise Problem("Receipt lookup requires an explicit destination")

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

    @app.post("/api/login")
    def login():
        principal=store.authenticate(body().get("token"))
        if not principal: raise Problem("Invalid local access key",401)
        session.clear(); session["actor"]=principal["id"]; session["credential_id"]=principal["credential_id"]; session.permanent=True
        return jsonify(principal)

    @app.post("/api/logout")
    def logout():
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
        result=store.rooms(actor())
        if not g.actor["legacy"]:
            grants=json.loads(g.actor["grants"])
            result=[room for room in result if "read" in grants.get(room["id"],[])]
        return jsonify(rooms=result)

    # v1 /rooms remains the stable session alias for existing clients/receipts.
    @app.get("/api/v2/rooms")
    def persistent_rooms(): return jsonify(rooms=store.persistent_rooms(actor()))

    @app.post("/api/v2/rooms")
    def create_persistent_room(): return jsonify(store.create_persistent_room(actor(),key(),body())),201

    @app.get("/api/v2/rooms/<room>")
    def persistent_room(room): return jsonify(store.persistent_room(actor(),room))

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

    return app


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir",required=True)
    parser.add_argument("--port",type=int,default=18880)
    args=parser.parse_args()
    if not 1024<=args.port<=65535: parser.error("Use an unprivileged local port")
    application=create_app(args.data_dir,args.port)
    print(f"LOCAL TEST ONLY: http://127.0.0.1:{args.port}",flush=True)
    print(f"Access key file: {application.extensions['records_store'].root/'owner-key.txt'}",flush=True)
    # Avoid recording paths/search terms or request bodies in general access logs.
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    application.run(host="127.0.0.1",port=args.port,debug=False,use_reloader=False,threaded=True)
