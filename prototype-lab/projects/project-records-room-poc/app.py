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


def create_app(data_dir, port=18880, testing=False):
    app=Flask(__name__,static_folder="static",static_url_path="/static")
    store=Store(data_dir)
    app.config.update(SECRET_KEY=store.session_key,MAX_CONTENT_LENGTH=3_000_000,
                      SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE="Strict",
                      SESSION_COOKIE_NAME="minimoi_room_poc",PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
                      TESTING=testing)
    app.extensions["records_store"]=store
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
        elif session.get("actor"):
            with store.connect() as db:
                row=db.execute("SELECT id,label,kind FROM principals WHERE id=?",(session["actor"],)).fetchone()
                actor=dict(row) if row else None
        if not actor: raise Problem("Sign in with a local access key",401)
        g.actor=actor

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"]="no-store"
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["Referrer-Policy"]="no-referrer"
        response.headers["Content-Security-Policy"]="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

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
        session.clear(); session["actor"]=principal["id"]; session.permanent=True
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
        return jsonify(environment="local-test",schema_version=4,counts=counts,journal_mode=mode,
                       production="not_connected",replication="not_implemented",models="not_connected",
                       independent_backup=False,data_directory=str(store.root))

    @app.get("/api/v1/rooms")
    def rooms(): return jsonify(rooms=store.rooms(actor()))

    @app.get("/api/v1/operations/<operation_key>")
    def operation(operation_key): return jsonify(store.operation(actor(),operation_key))

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

    @app.post("/api/v1/rooms/<room>/state")
    def state(room): return jsonify(store.state(actor(),key(),room,body()))

    @app.post("/api/v1/rooms/<room>/moderator")
    def moderator(room): return jsonify(store.moderator(actor(),key(),room,body()))

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
