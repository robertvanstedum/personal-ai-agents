"""Owner-editable operational wiki. Content and immutable revisions live on the data volume."""
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import jsonify, request
from . import cfg, owner_api

TITLE_MAX = 160
BODY_MAX = 24000


def database():
    # Same persistent, environment-specific volume as the guarded queue store.
    store = cfg()["services"].store
    if store.write_problem():
        raise OSError("Persistent wiki storage is unavailable")
    path = store.path
    if not path:
        raise OSError("Wiki storage is not configured")
    db = sqlite3.connect(str(Path(path).parent / "runbooks.sqlite3"), timeout=5)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("CREATE TABLE IF NOT EXISTS revisions (page TEXT NOT NULL, revision INTEGER NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, author TEXT NOT NULL, updated TEXT NOT NULL, PRIMARY KEY(page, revision))")
    db.execute("CREATE TABLE IF NOT EXISTS saves (author TEXT NOT NULL, key TEXT NOT NULL, digest TEXT NOT NULL, page TEXT NOT NULL, revision INTEGER NOT NULL, PRIMARY KEY(author,key))")
    db.commit()
    return db


def catalog(db, query=""):
    rows = db.execute("SELECT r.* FROM revisions r JOIN (SELECT page, MAX(revision) AS revision FROM revisions GROUP BY page) x USING(page,revision) ORDER BY updated DESC").fetchall()
    q = query.casefold()
    return [dict(row) for row in rows if not q or q in (row["title"] + " " + row["body"]).casefold()]


def read(db, page, revision=None):
    if revision is None:
        row = db.execute("SELECT * FROM revisions WHERE page=? ORDER BY revision DESC LIMIT 1", (page,)).fetchone()
    else:
        row = db.execute("SELECT * FROM revisions WHERE page=? AND revision=?", (page, revision)).fetchone()
    return dict(row) if row else None


def save(db, data, author):
    title, body = data.get("title"), data.get("body")
    page, expected = data.get("page"), data.get("revision")
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= TITLE_MAX or not isinstance(body, str) or len(body) > BODY_MAX:
        return {"message": "A title and content within the size limits are required."}, 422
    if page is not None and (not isinstance(page, str) or len(page) != 32 or any(c not in "0123456789abcdef" for c in page)):
        return {"message": "Invalid page."}, 422
    if type(expected) is not int or expected < 0 or (page is None and expected != 0):
        return {"message": "Invalid revision."}, 422
    digest = hashlib.sha256(json.dumps([page, expected, title, body]).encode()).hexdigest()
    try:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT * FROM saves WHERE author=? AND key=?", (author, data["_key"])).fetchone()
        if old:
            db.rollback()
            if old["digest"] != digest:
                return {"message": "This save key was already used for another edit. Reload before saving."}, 409
            return {"message": "Saved", "page": old["page"], "revision": old["revision"]}, 200
        current = read(db, page) if page else None
        if page and not current:
            db.rollback()
            return {"message": "Page not found."}, 404
        if current and current["revision"] != expected:
            db.rollback()
            return {"message": "Someone saved a newer revision. Your draft is still here. Open the latest page in another tab to compare before saving.", "current_revision": current["revision"]}, 409
        page = page or uuid.uuid4().hex
        revision = expected + 1
        db.execute("INSERT INTO revisions VALUES (?,?,?,?,?,?)", (page, revision, title.strip(), body, author, datetime.now(timezone.utc).isoformat()))
        db.execute("INSERT INTO saves VALUES (?,?,?,?,?)", (author, data["_key"], digest, page, revision))
        db.commit()
        return {"message": "Saved", "page": page, "revision": revision}, 200
    except Exception:
        db.rollback()
        raise


@owner_api
def save_page():
    from .api import _write_body, _principal
    refusal, data = _write_body()
    if refusal is not None:
        return refusal
    db = None
    try:
        db = database()
        body, code = save(db, data, _principal())
        return jsonify(body), code
    except (OSError, sqlite3.Error):
        return jsonify(message="Wiki storage is unavailable. Your draft has not been cleared; reload the page separately to check whether the save completed."), 503
    finally:
        if db is not None:
            db.close()
