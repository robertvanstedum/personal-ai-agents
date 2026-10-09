"""Owner technology bookmarks: links, tags and notes; no fetching or inference.

Reuses the Guild persistent volume and write/privacy guards. Curator's existing
bookmark path starts a deep dive, so it is deliberately not used for these saves.
"""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from flask import jsonify
from . import cfg, owner_api


def canonical_url(value):
    if not isinstance(value, str) or len(value) > 3000 or any(ord(c) < 33 for c in value):
        raise ValueError("Enter a complete http or https link without spaces.")
    try:
        u = urlsplit(value)
        if u.scheme.lower() not in {"https", "http"} or not u.hostname or u.username or u.password:
            raise ValueError()
        port = u.port
        host = u.hostname.lower()
        if ":" in host: host = "[" + host + "]"
        if port and (u.scheme.lower(), port) not in {("https", 443), ("http", 80)}: host += ":" + str(port)
        # Preserve query order, values and fragments: they may identify distinct content.
        return urlunsplit((u.scheme.lower(), host, u.path or "/", u.query, u.fragment))
    except ValueError:
        raise ValueError("Enter a complete http or https link without credentials.") from None


def database():
    store = cfg()["services"].store
    if store.write_problem() or not store.path:
        raise OSError("Library storage unavailable")
    db = sqlite3.connect(str(Path(store.path).parent / "technology-library.sqlite3"), timeout=5)
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE IF NOT EXISTS links (owner TEXT NOT NULL, url TEXT NOT NULL, title TEXT NOT NULL, tags TEXT NOT NULL, note TEXT NOT NULL, original_url TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL, revision INTEGER NOT NULL, PRIMARY KEY(owner,url))")
    db.execute("CREATE TABLE IF NOT EXISTS saves (owner TEXT NOT NULL, key TEXT NOT NULL, digest TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(owner,key))")
    db.commit()
    return db


def listing(db, owner, query="", tag=""):
    rows = [dict(r) for r in db.execute("SELECT * FROM links WHERE owner=? ORDER BY updated DESC", (owner,))]
    for r in rows: r['tags'] = json.loads(r['tags'])
    tags = sorted({t for r in rows for t in r['tags']})
    query = query.casefold()
    return [r for r in rows if (not tag or tag in r['tags']) and (not query or query in
        (r['title'] + ' ' + r['url'] + ' ' + r['note'] + ' ' + ' '.join(r['tags'])).casefold())], tags


def save(db, owner, data):
    try:
        url = canonical_url(data.get('url'))
    except ValueError as exc:
        return {'message': str(exc)}, 422
    title, note, tags = data.get('title'), data.get('note', ''), data.get('tags', [])
    revision = data.get('revision', 0)
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 200 or not isinstance(note, str) or len(note) > 4000:
        return {'message': 'Use a title up to 200 characters and notes up to 4,000.'}, 422
    if not isinstance(tags, list) or len(tags) > 20 or any(not isinstance(t, str) or not 1 <= len(t.strip()) <= 50 for t in tags):
        return {'message': 'Use up to 20 topic tags of 50 characters each.'}, 422
    if type(revision) is not int or revision < 0:
        return {'message': 'Invalid revision.'}, 422
    tags = sorted({t.strip().casefold() for t in tags})
    digest = hashlib.sha256(json.dumps([url, title, note, tags, revision]).encode()).hexdigest()
    try:
        db.execute('BEGIN IMMEDIATE')
        prior = db.execute('SELECT * FROM saves WHERE owner=? AND key=?', (owner, data['_key'])).fetchone()
        if prior:
            db.rollback()
            return (json.loads(prior['result']), 200) if prior['digest'] == digest else ({'message': 'This save key was already used for another edit.'}, 409)
        old = db.execute('SELECT * FROM links WHERE owner=? AND url=?', (owner, url)).fetchone()
        if old and old['revision'] != revision:
            db.rollback()
            return {'message': 'This link is already saved or was updated. Open its current entry before editing.', 'url': url}, 409
        if not old and revision:
            db.rollback()
            return {'message': 'The original entry is unavailable.'}, 409
        now = datetime.now(timezone.utc).isoformat()
        db.execute('INSERT OR REPLACE INTO links VALUES (?,?,?,?,?,?,?,?,?)',
            (owner, url, title.strip(), json.dumps(tags), note, old['original_url'] if old else data['url'],
             old['created'] if old else now, now, revision + 1))
        result = {'result': 'saved', 'message': 'Saved', 'url': url, 'revision': revision + 1}
        db.execute('INSERT INTO saves VALUES (?,?,?,?)', (owner, data['_key'], digest, json.dumps(result)))
        db.commit()
        return result, 200
    except Exception:
        db.rollback()
        raise


@owner_api
def save_link():
    from .api import _write_body, _principal
    refusal, data = _write_body()
    if refusal is not None: return refusal
    db = None
    try:
        db = database()
        body, code = save(db, _principal(), data)
        return jsonify(body), code
    except (OSError, sqlite3.Error):
        return jsonify(message='Library storage is unavailable. Your draft is still here.'), 503
    finally:
        if db is not None: db.close()
