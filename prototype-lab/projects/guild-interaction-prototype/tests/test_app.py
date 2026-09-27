"""Server tests: routes, fixtures, adapters (live vs sample, missing vs failed),
no writes, fail-closed binding and the dev mount under a URL prefix."""
from __future__ import annotations

import builtins
import hashlib
import json
import re
import sqlite3
import subprocess
from functools import wraps
from pathlib import Path

import pytest
from flask import Flask, redirect, session

from app import create_app
from guild_ui import GuildBindingError, _prototype_owner_noop, config_digest, register_guild_ui
from guild_ui.adapters.activity import LiveActivity
from guild_ui.adapters.build_queue import LiveBuildQueue
from guild_ui.adapters.sessions import LiveSessions

PROJECT = Path(__file__).resolve().parents[1]
REPO = PROJECT.parents[2]
REAL_QUEUE = REPO / "data" / "guild" / "build_queue.json"
PAGES = ["/guild", "/guild/build", "/guild/build/bench", "/guild/build/queue", "/guild/build/items/158",
         "/guild/operate", "/guild/improve", "/guild/experiment"]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture(params=["sample", "live"])
def client(request):
    return create_app(sources=request.param, testing=True).test_client()


def allow_all(view):
    @wraps(view)
    def inner(*a, **k):
        return view(*a, **k)
    return inner


def deny_all(view):
    @wraps(view)
    def inner(*a, **k):
        return "owner required", 403
    return inner


# ── routes and fixtures ───────────────────────────────────────────────────

def test_routes_render(client):
    for path in PAGES + [p + "/" for p in PAGES[1:]]:
        r = client.get(path)
        assert r.status_code == 200, path
        html = r.get_data(as_text=True)
        if "/guild/build" == path.rstrip("/"):                     # Shop floor: one compact honesty line
            assert "Prototype · simulated replies · some sample data" in html, path
        elif "sources: live" in html:
            assert "Prototype · live + sample · simulated" in html, path
        else:
            assert "Prototype · sample data · simulated actions" in html, path
    assert "Prototype · simulated replies · some sample data" in client.get("/guild/build").get_data(as_text=True)
    assert client.get("/guild/ui-assets/js/main.js").status_code == 200


def test_unknown_item_404(client):
    r = client.get("/guild/build/items/99999")
    assert r.status_code == 404 and b"not found" in r.data


def test_fixtures_marked_sample():
    files = sorted((PROJECT / "fixtures").glob("*.json"))
    assert len(files) == 5                                    # rev 3.1 adds usage.sample.json
    for f in files:
        assert json.loads(f.read_text())["sample"] is True, f.name


def test_scenario_marked_simulated():
    assert json.loads((PROJECT / "config" / "scenario.json").read_text())["simulated"] is True
    json.loads((PROJECT / "config" / "layout.json").read_text())  # valid JSON


def _snapshot():
    files = {}
    for p in PROJECT.rglob("*"):
        if p.is_file() and "__pycache__" not in p.parts:
            files[str(p.relative_to(PROJECT))] = (p.stat().st_mtime_ns, p.stat().st_size)
    status = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "--untracked-files=all"],
                            capture_output=True, text=True, check=True).stdout
    status = "\n".join(l for l in status.splitlines() if "__pycache__" not in l)
    return files, sha(REAL_QUEUE), status


def test_no_route_writes_outside_prototype_dir():
    before = _snapshot()
    for mode in ("sample", "live"):
        c = create_app(sources=mode, testing=True).test_client()
        for path in PAGES + ["/guild/build/items/99999", "/"]:
            c.get(path)
        c.post("/guild/proto/reset", json={})
    after = _snapshot()
    assert after[1] == before[1], "build_queue.json changed"
    assert after[2] == before[2], "git status changed"
    assert after[0] == before[0], "prototype files changed"


def test_reset_endpoint_returns_defaults_and_writes_nothing(client):
    before = sha(REAL_QUEUE), config_digest()
    r = client.post("/guild/proto/reset", json={})
    body = r.get_json()
    assert r.status_code == 200 and body["reset"] is True
    assert body["defaults_digest"] == before[1]
    assert body["storage_keys"] == ["guild.bench.v1", "guild.conversation.v1", "guild.overlay.v1",
                                    "guild.clock.v1", "guild.nav.v1"]
    assert (sha(REAL_QUEUE), config_digest()) == before


# ── adapters: live vs sample, not configured vs unreadable ─────────────────

def test_live_queue_adapter_reads_real_shape_read_only(monkeypatch):
    modes = []
    real_open = builtins.open

    def spy(file, mode="r", *a, **k):
        if Path(str(file)).name == "build_queue.json":
            modes.append(mode)
        return real_open(file, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", spy)
    before = sha(REAL_QUEUE)
    res = LiveBuildQueue(REAL_QUEUE).list_items(("spec_ready", "in_build"))
    assert res.source == "live" and res.status == "ok" and res.reason is None
    assert all(isinstance(i["id"], int) for i in res.data)
    assert modes and all(m == "r" for m in modes)
    assert sha(REAL_QUEUE) == before


def _between(html, start, end):
    return html.split(start, 1)[1].split(end, 1)[0]


def _queue_surfaces(c):
    """Every surface that prints a queue-derived count."""
    queue = c.get("/guild/build/queue").get_data(as_text=True)
    landing = c.get("/guild").get_data(as_text=True)
    operate = c.get("/guild/operate").get_data(as_text=True)
    bench = c.get("/guild/build/bench").get_data(as_text=True)
    return {
        "queue_meta": _between(queue, "data-queue-meta", "</p>"),
        "queue_page": _between(queue, '<main id="main"', "</main>"),
        "build_door": _between(landing, 'data-door="build"', "</li>"),
        "queue_tile": _between(operate, 'data-tile="build_queue"', "</button>"),
        "in_motion": _between(bench, 'data-panel="motion"', "</section>"),
    }


@pytest.mark.parametrize("content,reason", [
    (None, "read_failed"),                       # missing file
    ("{not json", "invalid"),                    # not JSON
    ('{"items": []}', "invalid"),                # wrong top-level shape
    ('[{"status": "idea"}]', "invalid"),         # row without an id
    ('[{"id": "7", "status": "idea"}]', "invalid"),  # id not an integer
    ('[1, 2]', "invalid"),                       # rows not objects
])
def test_queue_adapter_missing_or_invalid_file_is_unknown_not_zero(tmp_path, content, reason):
    path = tmp_path / "build_queue.json"
    if content is not None:
        path.write_text(content)
    res = LiveBuildQueue(path).list_items()
    assert (res.source, res.status, res.reason, res.data) == ("live", "unknown", reason, None)
    assert res.error
    c = create_app(sources="live", testing=True, queue_path=path).test_client()
    s = _queue_surfaces(c)
    # A failed read never renders a count: no digits-as-count, no zero, anywhere.
    for name, html in s.items():
        assert not re.search(r"\b0 (active|blocked)", html), name
        assert not re.search(r"\b\d+ active", html), name
    assert "Active items: <strong>unknown</strong> · read failed" in s["queue_meta"]
    assert "data-col-count" not in s["queue_page"] and "data-active-count" not in s["queue_page"]
    assert re.search(r"queue: treat as unknown <span class=\"src-badge\" data-source=\"live\" data-status=\"unknown\"", s["build_door"])
    for name in ("queue_meta", "queue_tile", "in_motion"):          # never the sample value instead
        assert "sample" not in s[name], name
    assert "treat as unknown" in s["queue_tile"] and "queue read failed" in s["queue_tile"]
    assert "treat as unknown" in s["in_motion"] and "<circle" not in s["in_motion"]


def test_queue_adapter_valid_empty_list_is_zero(tmp_path):
    path = tmp_path / "build_queue.json"
    path.write_text("[]")
    res = LiveBuildQueue(path).list_items()
    assert (res.status, res.data, res.note) == ("ok", [], None)
    s = _queue_surfaces(create_app(sources="live", testing=True, queue_path=path).test_client())
    assert "0 active" in s["queue_tile"] and "0 blocked" in s["queue_tile"]
    assert '<span data-active-count>0</span> active items' in s["queue_meta"]
    assert "0 active in queue" in s["build_door"]
    assert "treat as unknown" not in s["queue_tile"] + s["queue_meta"] + s["build_door"]


def test_queue_mixed_file_keeps_rows_with_unknown_status(tmp_path):
    path = tmp_path / "build_queue.json"
    path.write_text(json.dumps([
        {"id": 1, "status": "spec_ready", "spec_title": "Ready one"},
        {"id": 2, "status": "in_build", "spec_title": "Building two"},
        {"id": 3, "status": "done", "spec_title": "Done three"},
        {"id": 136, "status": "open", "spec_title": "Open one-three-six"},
        {"id": 140, "spec_title": "No status"},
        {"id": 141, "status": 5, "spec_title": "Numeric status"},
    ]))
    before = sha(path)
    q = LiveBuildQueue(path)
    res = q.list_items()
    assert (res.source, res.status, res.reason) == ("live", "ok", None)
    assert res.note == "3 rows with unknown status"
    by_id = {i["id"]: i for i in res.data}
    assert len(by_id) == 6                                           # rows kept, not dropped
    assert by_id[136]["status_known"] is False and by_id[136]["status"] is None
    assert by_id[136]["status_label"] == "unknown status 'open'"
    assert by_id[140]["status_label"] == "unknown status (missing)"
    assert by_id[141]["status_label"] == "unknown status (not text)"
    assert [i["id"] for i in q.list_items(("spec_ready", "in_build")).data] == [1, 2]
    assert q.list_items(("spec_ready", "in_build")).note == "3 rows with unknown status"

    c = create_app(sources="live", testing=True, queue_path=path).test_client()
    s = _queue_surfaces(c)
    assert '<span data-active-count>2</span> active items' in s["queue_meta"]
    assert re.search(r'data-source="live" data-status="ok">live · read', s["queue_meta"])
    assert "3 rows with unknown status" in s["queue_meta"]
    assert "treat as unknown" not in s["queue_meta"]
    unk = _between(s["queue_page"], "data-unknown-rows", "</section>")
    for label in ("unknown status &#39;open&#39;", "unknown status (missing)", "unknown status (not text)"):
        assert label in unk, label
    assert unk.count("data-unknown-row ") == 3
    cards = re.findall(r'data-queue-card data-item-id="(\d+)"', s["queue_page"])
    assert sorted(cards) == ["1", "2"]
    assert "2 active" in s["queue_tile"] and "3 rows with unknown status" in s["queue_tile"]
    assert "treat as unknown" not in s["queue_tile"]
    assert "2 active in queue (3 rows with unknown status)" in s["build_door"]
    assert "3 rows with unknown status" in s["in_motion"] and s["in_motion"].count("<circle") == 2
    item = c.get("/guild/build/items/136")
    html = item.get_data(as_text=True)
    assert item.status_code == 200 and "unknown status &#39;open&#39;" in html
    assert '<option value="open" selected disabled>' in html
    assert sha(path) == before


def test_git_adapter_local_only(monkeypatch, tmp_path):
    calls = []
    real_run = subprocess.run

    def spy(args, *a, **k):
        calls.append(args)
        return real_run(args, *a, **k)

    monkeypatch.setattr(subprocess, "run", spy)
    res = LiveActivity(REPO).recent_commits(3)
    assert res.source == "live" and res.status == "ok" and len(res.data) == 3
    assert all(set(c) >= {"ref", "title", "at"} for c in res.data)
    for args in calls:
        assert args[0] == "git" and not ({"fetch", "pull", "push", "remote", "clone"} & set(args))
    bad = LiveActivity(tmp_path).recent_commits(3)   # not a git repo → unknown, not empty
    assert (bad.status, bad.data, bad.reason) == ("unknown", None, "read_failed")


def test_sessions_not_configured_vs_unreadable(tmp_path):
    nc = LiveSessions(None).list_sessions()
    assert (nc.source, nc.status, nc.reason) == ("not_instrumented", "unknown", "not_configured")
    missing = LiveSessions(str(tmp_path / "nope.sqlite3")).list_sessions()
    assert (missing.source, missing.status, missing.reason) == ("live", "unknown", "read_failed")
    garbage = tmp_path / "garbage.sqlite3"
    garbage.write_bytes(b"this is not sqlite" * 100)
    bad = LiveSessions(str(garbage)).list_sessions()
    assert (bad.status, bad.reason, bad.data) == ("unknown", "invalid", None)
    # Not configured → the Discussions panel's explicit sample opt-in, badged as such.
    page = create_app(sources="live", testing=True, records_db="").test_client().get("/guild/build/bench").get_data(as_text=True)
    assert "no Records source configured" in page and "s-021" in page
    # Configured but unreadable → unknown; never the sample rows.
    page = create_app(sources="live", testing=True, records_db=str(garbage)).test_client().get("/guild/build/bench").get_data(as_text=True)
    disc = page.split('data-panel="discussions"')[1].split("</section>")[0]
    assert "treat as unknown" in disc and "s-021" not in disc and "Operate matrix density" not in disc


def test_sessions_adapter_reads_readonly_sqlite(tmp_path):
    db = tmp_path / "records.sqlite3"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE rooms(id TEXT PRIMARY KEY, title TEXT, purpose TEXT, mode TEXT, state TEXT, version INT, moderator TEXT, created TEXT, updated TEXT)")
    con.execute("INSERT INTO rooms VALUES('s-900','Live session','p','m','open',1,'robert','2026-09-26','2026-09-26T10:00:00')")
    con.commit(); con.close()
    before = sha(db)
    res = LiveSessions(str(db)).list_sessions()
    assert res.source == "live" and res.status == "ok" and res.data[0]["id"] == "s-900"
    assert sha(db) == before and not (tmp_path / "records.sqlite3-wal").exists()
    page = create_app(sources="live", testing=True, records_db=str(db)).test_client().get("/guild/build/bench").get_data(as_text=True)
    assert "Live session" in page


# ── fail-closed binding (Codex F5) ─────────────────────────────────────────

def test_production_mode_fails_closed_without_owner_guard():
    with pytest.raises(GuildBindingError):
        create_app(prototype=False, testing=True)
    with pytest.raises(GuildBindingError):
        create_app(prototype=False, testing=True, owner_guard=_prototype_owner_noop, write_services=object())
    with pytest.raises(GuildBindingError):
        create_app(prototype=False, testing=True, owner_guard=allow_all)   # no write services
    create_app(prototype=False, testing=True, owner_guard=allow_all, write_services=object())


def test_production_mode_guard_applies_to_every_route():
    app = create_app(prototype=False, testing=True, owner_guard=deny_all, write_services=object())
    rules = [r for r in app.url_map.iter_rules() if r.endpoint.startswith("guild_ui.")]
    assert len(rules) >= 10
    c = app.test_client()
    for rule in rules:
        url = re.sub(r"<int:\w+>", "158", re.sub(r"<(path:)?\w+>", "tokens.css", rule.rule))
        method = "POST" if "POST" in rule.methods and "GET" not in rule.methods else "GET"
        assert c.open(url, method=method).status_code == 403, url
        assert getattr(app.view_functions[rule.endpoint], "guild_owner_seam", False), rule.endpoint


def test_prototype_flag_off_hides_prototype_controls():
    c = create_app(prototype=False, testing=True, owner_guard=allow_all, write_services=object()).test_client()
    page = c.get("/guild/build/bench").get_data(as_text=True)
    for marker in ("data-proto-banner", "data-mc-voice", "data-return-monday", "data-mc-sample-attach",
                   "Prototype · sample data"):
        assert marker not in page
    assert "Master Craftsman is not connected." in page
    assert c.post("/guild/proto/reset", json={}).status_code == 404


def test_loopback_host_guard_and_csp():
    c = create_app(sources="sample", testing=True).test_client()
    assert c.get("/guild", headers={"Host": "evil.example"}).status_code == 403
    assert c.post("/guild/proto/reset", json={}, headers={"Origin": "http://evil.example"}).status_code == 403
    r = c.get("/guild/build/bench")
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert b"style=" not in r.data, "no inline styles in templates"


# ── dev mount under a URL prefix ───────────────────────────────────────────

def host_app(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "host.css").write_text("/* host */")
    app = Flask("host", static_folder=str(static))
    app.secret_key = "test-only"

    @app.route("/guild/build/queue")
    def host_queue():
        return "HOST QUEUE"

    @app.route("/guild")
    def host_guild():
        return "HOST GUILD"

    @app.route("/login")
    def login():
        return "login"
    return app


def portal_style_guard(view):
    """Same shape as the portal's _require_owner: redirect unless an owner session."""
    @wraps(view)
    def decorated(*a, **k):
        user = session.get("user")
        if not user or user.get("tier") != "owner":
            return redirect("/login")
        return view(*a, **k)
    return decorated


def test_mounted_under_prefix_on_host_app(tmp_path):
    app = host_app(tmp_path)
    register_guild_ui(app, prototype=True, url_prefix="/guild-proto", sources="sample")
    c = app.test_client()
    for path in PAGES:
        r = c.get("/guild-proto" + path)
        assert r.status_code == 200, path
        html = r.get_data(as_text=True)
        for ref in re.findall(r'(?:href|src)="(/[^"]*)"', html):
            assert ref.startswith("/guild-proto/"), (path, ref)
        page = json.loads(html.split('id="guild-page">')[1].split("</script>")[0])
        assert page["base"] == "/guild-proto" and page["urls"]["bench"] == "/guild-proto/guild/build/bench"
        assert page["storage_ns"] == "guild.guild-proto"
    assert c.get("/guild-proto/guild/ui-assets/js/main.js").status_code == 200
    assert c.get("/guild/build/queue").get_data(as_text=True) == "HOST QUEUE"
    assert c.get("/guild").get_data(as_text=True) == "HOST GUILD"
    assert c.get("/static/host.css").status_code == 200
    assert c.get("/static/guild-ui/tokens.css").status_code == 404


def test_owner_guard_refuses_anonymous_serves_owner(tmp_path):
    app = host_app(tmp_path)
    register_guild_ui(app, prototype=True, owner_guard=portal_style_guard, url_prefix="/guild-proto", sources="sample")
    urls = ["/guild-proto" + p for p in PAGES] + ["/guild-proto/guild/ui-assets/tokens.css",
                                                   "/guild-proto/guild/ui-assets/js/main.js"]
    anon = app.test_client()
    for u in urls:
        r = anon.get(u)
        assert r.status_code == 302 and r.headers["Location"].endswith("/login"), u
    assert anon.post("/guild-proto/guild/proto/reset", json={}).status_code == 302
    owner = app.test_client()
    with owner.session_transaction() as s:
        s["user"] = {"display_name": "Robert", "tier": "owner"}
    for u in urls:
        assert owner.get(u).status_code == 200, u
    assert owner.post("/guild-proto/guild/proto/reset", json={}).get_json()["storage_keys"][0] == "guild.guild-proto.bench.v1"


def test_portal_nav_global_not_overridden(tmp_path):
    app = host_app(tmp_path)

    def real_nav(user, active):
        return '<div id="portal-nav-bar">REAL-PORTAL-NAV</div>'
    app.jinja_env.globals["portal_nav_html"] = real_nav
    register_guild_ui(app, prototype=True, url_prefix="/guild-proto", sources="sample")
    assert app.jinja_env.globals["portal_nav_html"] is real_nav
    html = app.test_client().get("/guild-proto/guild").get_data(as_text=True)
    assert '<div id="portal-nav-bar">REAL-PORTAL-NAV</div>' in html
    assert "&lt;div id=" not in html


def test_csp_absent_on_host_routes(tmp_path):
    app = host_app(tmp_path)
    register_guild_ui(app, prototype=True, url_prefix="/guild-proto", sources="sample")
    c = app.test_client()
    assert "Content-Security-Policy" not in c.get("/guild/build/queue").headers
    assert "Content-Security-Policy" not in c.get("/static/host.css").headers
    assert "Content-Security-Policy" in c.get("/guild-proto/guild/operate").headers


# ── rev 3: Shop floor lights ────────────────────────────────────────────────

from guild_ui import lights as L  # noqa: E402
from guild_ui.adapters.contract import SourceResult  # noqa: E402


def _q(rows, status="ok", source="live"):
    return SourceResult(source, status, rows if status == "ok" else None)


def _row(i, st):
    known = st in ("idea", "design", "backlog", "spec_ready", "in_build", "blocked", "deferred", "cancelled", "superseded", "done")
    return {"id": i, "status": st if known else None, "status_known": known}


def _counts(i, e, r, a):
    return [{"label": "Intended", "n": i}, {"label": "Enabled", "n": e}, {"label": "Reached", "n": r}, {"label": "Active", "n": a}]


def test_light_rules():
    # every state carries a word and a shape; unknown is never green
    assert {k: (v["word"], v["shape"]) for k, v in L.STATES.items()} == {
        "green": ("OK", "circle"), "yellow": ("Watch", "triangle"), "red": ("Problem", "square"), "unknown": ("Unknown", "ring")}
    # build queue
    assert L.queue_light(_q(None, status="unknown"))["state"] == "unknown"          # read failed → unknown
    assert L.queue_light(_q([_row(1, "in_build"), _row(2, "blocked"), _row(3, "open")]))["state"] == "red"
    assert L.queue_light(_q([_row(1, "in_build"), _row(3, "open")]))["state"] == "yellow"
    g = L.queue_light(_q([_row(1, "in_build"), _row(2, "spec_ready")]))
    assert (g["state"], g["reason"], g["source_mark"]) == ("green", "2 active · 0 blocked", "live")
    assert L.queue_light(_q([]))["state"] == "green"                                  # valid empty queue is a real OK
    # rollouts (the Sat → Mon change)
    assert L.rollout_light(_counts(3, 2, 2, 1))["state"] == "yellow"
    assert "access gap 1" in L.rollout_light(_counts(3, 2, 2, 1))["reason"]
    assert L.rollout_light(_counts(3, 3, 3, 1))["state"] == "green"
    assert L.rollout_light(_counts(3, 3, 2, 1))["state"] == "yellow"
    assert L.rollout_light(_counts(3, 2, 2, 0))["state"] == "red"
    assert L.rollout_light(None)["state"] == "unknown"
    assert L.rollout_light([{"label": "Intended", "n": 3}])["state"] == "unknown"
    # tiles: not instrumented / stale / missing numbers are unknown, never green
    ni = SourceResult("not_instrumented", "unknown", None, reason="not_configured")
    for fn in (L.tile_light, L.agents_light):
        assert fn(ni)["state"] == "unknown" and fn(ni)["source_mark"] == "not instrumented"
        stale = SourceResult("sample", "stale", {"needs_robert": 0, "failed": 0, "mtd": 1, "budget": 100})
        assert fn(stale)["state"] == "unknown"
    ok = lambda d: SourceResult("sample", "ok", d)
    assert L.agents_light(ok({"needs_robert": 0, "failed": 0, "running": 2}))["state"] == "green"
    assert L.agents_light(ok({"needs_robert": 1, "failed": 0}))["state"] == "yellow"
    assert L.agents_light(ok({"needs_robert": 1, "failed": 1}))["state"] == "red"
    assert L.agents_light(ok({"value": "2 running"}))["state"] == "unknown"
    assert not hasattr(L, "spend_light")                                          # rev 3.1: budget rule retired
    assert L.tile_light(ok({"value": "7"}))["state"] == "unknown"                    # no rule → unknown, not green


def _lights_html(html):
    return {m[0]: m[1] for m in re.findall(r'<li class="light" data-light="(\w+)" data-light-state="(\w+)"(?![^>]*hidden)', html)}


def test_shop_floor_route_and_lights_render():
    c = create_app(sources="sample", testing=True).test_client()
    r = c.get("/guild/build")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "<title>Shop floor · Master Craftsman" in html
    assert "data-panel=" not in html                                                # no bench panels on arrival
    assert _lights_html(html) == {"build_queue": "green", "rollouts": "yellow", "systems": "unknown",
                                  "agents": "yellow", "usage": "yellow"}
    # the Monday Rollouts variant is present but hidden until the simulated clock moves
    assert re.search(r'data-light="rollouts" data-light-state="green" data-show-when="[^"]*mon[^"]*" hidden', html)
    for block in re.findall(r'<li class="light".*?</li>', html, re.S):
        assert re.search(r'data-shape="(circle|triangle|square|ring)"', block)
        assert re.search(r'data-light-word>(OK|Watch|Problem|Unknown)<', block)
    assert 'href="/guild/operate?tile=systems"' in html and 'href="/guild/build/queue"' in html
    assert html.count("data-note>") == 4 and html.count("data-reminder ") + html.count("data-reminder>") >= 1
    op = c.get("/guild/operate?tile=usage").get_data(as_text=True)
    assert re.search(r'data-tile="usage"[^>]*aria-pressed="true"', op)


def test_shop_floor_postits_capped(monkeypatch):
    import guild_ui
    real = guild_ui._load

    def more_notes(name):
        d = real(name)
        if name == "scenario.json":
            d["bench"]["postits"] = [f"note {i}" for i in range(7)]
        return d
    monkeypatch.setattr(guild_ui, "_load", more_notes)
    html = create_app(sources="sample", testing=True).test_client().get("/guild/build").get_data(as_text=True)
    assert html.count("data-note>") == 4


def test_shop_floor_read_failed_queue_light_unknown(tmp_path):
    path = tmp_path / "build_queue.json"
    path.write_text("{broken")
    html = create_app(sources="live", testing=True, queue_path=path).test_client().get("/guild/build").get_data(as_text=True)
    assert _lights_html(html)["build_queue"] == "unknown"
    block = re.search(r'<li class="light" data-light="build_queue".*?</li>', html, re.S).group(0)
    assert "queue read failed" in block and "OK" not in block and "active" not in block


# ── rev 3.1: Usage & limits ─────────────────────────────────────────────────

from guild_ui import usage as U  # noqa: E402


def _doc(sources, now=1000):
    return {"now": {"sat": now}, "observed": {"sat": "Sat"}, "min_window_hours": 6, "balance_horizon_hours": 48,
            "sources": sources, "agents": [], "shift": {}}


def _plan(used, reset_at, hours=24, pct=0, **kw):
    return {"id": kw.pop("id", "p"), "kind": "plan", "label": kw.pop("label", "Plan"), "warn_pct": 80,
            "sat": {"used_pct": used, "reset_at": reset_at, "window_hours": hours, "window_pct": pct}, **kw}


def _bal(balance, hours=24, spent=0, **kw):
    return {"id": kw.pop("id", "b"), "kind": "balance", "label": kw.pop("label", "Balance"), "refill_at": 5, "warn_at": 10,
            "sat": {"balance": balance, "window_hours": hours, "window_spent": spent}, **kw}


def _ev(src, now=1000):
    return U.evaluate(src, "sat", _doc([src], now))


def test_usage_rules_each_branch():
    # plans
    assert _ev(_plan(100, 5000))["state"] == "red"                                  # at the limit
    assert _ev(_plan(85, 5000))["state"] == "yellow"                                # ≥ 80 %
    early = _ev(_plan(60, 5000, hours=24, pct=24))                                 # 1 %/h → limit at 1000+2400 < 5000
    assert early["state"] == "yellow" and early["before_reset"] and "projected limit" in early["projection_text"]
    late = _ev(_plan(60, 2000, hours=24, pct=24))                                  # limit at 3400, after reset 2000
    assert late["state"] == "green" and not late["before_reset"]                    # no escalation after reset
    assert _ev(_plan(40, 5000, hours=24, pct=2))["state"] == "green"
    # balances
    assert _ev(_bal(5.0))["state"] == "red"                                         # at or below refill threshold
    assert _ev(_bal(9.0))["state"] == "yellow"                                      # warning band
    soon = _ev(_bal(20.0, hours=24, spent=12))                                     # 0.5 $/h → refill in 30 h ≤ 48 h
    assert soon["state"] == "yellow" and "refill needed by" in soon["projection_text"]
    assert _ev(_bal(20.0, hours=24, spent=1))["state"] == "green"                   # refill in 360 h
    # unknown
    ni = _ev({"id": "g", "kind": "plan", "label": "Grok plan", "instrumented": False, "planned_source": "manual entry planned"})
    assert ni["state"] == "unknown" and ni["source_mark"] == "not instrumented" and ni["projected_at"] is None
    assert _ev(_plan(10, 5000, stale=True))["state"] == "unknown"
    # aggregate precedence
    red, yel, unk, grn = (_ev(_plan(100, 5000)), _ev(_plan(85, 5000, label="Codex plan")), ni, _ev(_plan(10, 5000)))
    assert U.aggregate([grn, unk, red])["state"] == "red"                           # red beats unknown
    assert U.aggregate([grn, yel, unk])["state"] == "yellow"                        # default: known warning shown
    assert "1 not instrumented" in U.aggregate([grn, yel, unk])["reason"]
    assert U.aggregate([grn, unk])["state"] == "unknown"                            # unknown beats green
    assert U.aggregate([grn, grn])["state"] == "green"
    brief = ("red", "unknown", "yellow", "green")                                   # the brief's order, one config edit away
    assert U.aggregate([grn, yel, unk], brief)["state"] == "unknown"
    assert "worst known: Codex plan 85 %" in U.aggregate([grn, yel, unk], brief)["reason"]
    assert U.aggregate([red, unk], brief)["state"] == "red"
    # reason names the worst item: the earliest projected limit before reset outranks a higher plain usage
    worst = U.aggregate([_ev(_plan(85, 9000, label="A plan")), _ev(_plan(60, 5000, hours=24, pct=24, label="B plan"))])
    assert worst["reason"].startswith("B plan 60 % · projected limit")


def test_usage_projection_insufficient_data():
    short = _ev(_plan(60, 5000, hours=2, pct=10))
    assert short["projected_at"] is None and short["projection_text"] == "no projection — only 2 h of data (need 6 h)"
    idle = _ev(_plan(60, 5000, hours=24, pct=0))
    assert idle["projected_at"] is None and idle["projection_text"] == "no projection — no usage in the window"
    b = _ev(_bal(22.0, hours=24, spent=0))
    assert b["projected_at"] is None and "no projection — no spend" in b["projection_text"]
    assert not re.search(r"\d+:\d\d", short["projection_text"] + idle["projection_text"] + b["projection_text"])


def test_usage_table_renders_every_agent():
    import json as _json
    doc = _json.loads((PROJECT / "fixtures" / "usage.sample.json").read_text())
    assert doc["sample"] is True
    html = create_app(sources="sample", testing=True).test_client().get("/guild/operate?tile=usage").get_data(as_text=True)
    sat = _between(html, 'data-usage-clock="sat"', "</section>")
    rows = re.findall(r'<tr data-usage-row data-agent="([^"]+)" data-light-state="(\w+)">(.*?)</tr>', sat, re.S)
    assert [r[0] for r in rows] == [a["agent"] for a in doc["agents"]]
    words = {"green": "OK", "yellow": "Watch", "red": "Problem", "unknown": "Unknown"}
    for agent, state, body in rows:
        assert re.search(r'data-shape="(circle|triangle|square|ring)"', body), agent
        assert f"data-usage-status-word>{words[state]}<" in body, agent
    assert dict((r[0], r[1]) for r in rows)["Grok CLI"] == "unknown" and "not instrumented" in sat
    assert "Shift work: Move reviews to Claude Code until the Codex plan resets Mon 6:00" in sat   # vendor-stated reset
    assert "Overall: $42.80" in sat                                                 # secondary line, not the headline
    assert "projected limit Sun 13:15 (last 24 h pace)" in sat


def test_usage_monday_reset_changes_light():
    c = create_app(sources="sample", testing=True).test_client()
    html = c.get("/guild/build").get_data(as_text=True)
    sat = re.search(r'data-light="usage" data-light-state="(\w+)" data-show-when="[^"]*sat', html).group(1)
    mon = re.search(r'data-light="usage" data-light-state="(\w+)" data-show-when="[^"]*mon', html).group(1)
    assert (sat, mon) == ("yellow", "unknown")
    assert "Codex plan 72 % · projected limit Sun 13:15, before reset Mon 6:00 · 1 not instrumented" in html
    # the Limit reminder exists for Saturday only, ahead of Decide/Approve
    rem = _between(html, "data-reminders", "</ol>")
    tags = re.findall(r'<span class="reminder-tag">(\w+)</span>', rem)
    assert tags[0] == "Limit" or (tags[0] == "Decision" and tags[1] == "Limit")
    assert rem.count(">Limit<") == 1 and rem.count(">Check<") == 1                    # reset mismatch is a candidate too


# ── rev 3.1: vendor-reported warnings, reset sanity, refill receipts ────────

from guild_ui.evidence import cookie_name as _cookie_name, parse_cookie, strip_payment  # noqa: E402


def _vdoc(src, now=1000):
    d = _doc([src], now)
    d.update(vendor_fresh_hours=12, reset_tolerance_min=30, remaining_tolerance_pct=10)
    return d


def _vw(kind="warning", seen=990, reset=None, left=None, tool="Codex"):
    return {"tool": tool, "kind": kind, "text": "sample warning", "seen_at": seen,
            "stated_reset_at": reset, "stated_remaining_pct": left}


def test_vendor_warning_fresh_yellow_limit_reached_red():
    ok_plan = _plan(30, 5000, hours=24, pct=2)                                     # our numbers look fine
    assert U.evaluate(ok_plan, "sat", _vdoc(ok_plan))["state"] == "green"
    warned = {**ok_plan, "vendor": [_vw()]}
    ev = U.evaluate(warned, "sat", _vdoc(warned))
    assert ev["state"] == "yellow" and ev["vendor"]["fresh"] and ev["vendor"]["mark"] == "reported"
    hit = {**ok_plan, "vendor": [_vw("limit_reached")]}
    assert U.evaluate(hit, "sat", _vdoc(hit))["state"] == "red"
    ni = {"id": "g", "kind": "plan", "label": "Grok plan", "instrumented": False, "vendor": [_vw(tool="Grok CLI")]}
    assert U.evaluate(ni, "sat", _vdoc(ni))["state"] == "yellow"                   # evidence beats "no numbers"


def test_vendor_warning_stale_ignored_with_reason():
    plan = {**_plan(30, 5000, hours=24, pct=2), "vendor": [_vw(seen=100)]}          # 15 h before now=1000
    ev = U.evaluate(plan, "sat", _vdoc(plan))
    assert ev["state"] == "green" and ev["vendor"]["fresh"] is False
    assert ev["vendor"]["ignored"] == "ignored — seen Sat 1:40, older than 12 h"
    future = {**_plan(30, 5000, hours=24, pct=2), "vendor": [_vw(seen=2000)]}       # not yet seen at this clock
    assert U.evaluate(future, "sat", _vdoc(future))["vendor"] is None


def test_reset_mismatch_row_yellow_vendor_time_used():
    # configured reset 3000 is before the projected limit (3400) → green on our numbers;
    # the vendor says 4000 (> 30 min apart) → mismatch, yellow, and the projection uses 4000
    plan = {**_plan(60, 3000, hours=24, pct=24), "vendor": [_vw(reset=4000)]}
    assert U.evaluate(_plan(60, 3000, hours=24, pct=24), "sat", _vdoc(plan))["state"] == "green"
    ev = U.evaluate(plan, "sat", _vdoc(plan))
    assert ev["state"] == "yellow" and ev["reset_source"] == "vendor-stated" and ev["before_reset"]
    assert ev["mismatches"][0].startswith("Codex says resets Mon 18:40 · we assumed Mon 2:00")
    assert "(vendor-stated)" in ev["reset_text"]
    left = {**_plan(60, 5000, hours=24, pct=2), "vendor": [_vw(left=20)]}           # we measured 40 % left
    ev = U.evaluate(left, "sat", _vdoc(left))
    assert "Codex says 20 % left · we measured 40 % left" in ev["mismatches"] and ev["state"] == "yellow"


def test_reset_within_tolerance_no_mismatch():
    plan = {**_plan(30, 5000, hours=24, pct=2), "vendor": [_vw(reset=5020, left=65)]}   # 20 min and 5 % apart
    ev = U.evaluate(plan, "sat", _vdoc(plan))
    assert ev["mismatches"] == [] and ev["reset_source"] == "configured"
    assert ev["state"] == "yellow"                                                    # still a fresh warning, but no mismatch


def test_refill_clears_and_reprojects():
    low = {"id": "x", "kind": "balance", "label": "xAI API balance", "refill_at": 5, "warn_at": 10,
           "sat": {"balance": 4.80, "read_at": 900, "window_hours": 24, "window_spent": 2.40}}
    ev = U.evaluate(low, "sat", _vdoc(low))
    assert ev["state"] == "red" and ev["projection_text"].startswith("refill needed now")
    paid = {**low, "refills": [{"vendor": "Sample vendor", "amount": 20.0, "paid_at": 950}]}
    ev = U.evaluate(paid, "sat", _vdoc(paid))
    assert ev["state"] == "green" and ev["used_text"] == "$24.80 (after refill)"
    assert "from refill Sat 15:50" in ev["projection_text"]                           # projection restarts at the refill
    before = {**low, "refills": [{"vendor": "Sample vendor", "amount": 20.0, "paid_at": 800}]}   # before the reading
    assert U.evaluate(before, "sat", _vdoc(before))["state"] == "red"
    doc = _vdoc(paid)
    doc["overall_spend"] = {"mtd": 42.8, "note": "sample"}
    assert U.summarize(doc, "sat")["overall"]["refills"] == 20.0


def test_strip_payment_and_cookie_validation():
    text = "Receipt from xAI · $25.00 · Paid with Mastercard - 1234 · card ending in 9876 · billing a.b@example.com · **** 4321"
    clean = strip_payment(text)
    for bad in ("1234", "9876", "4321", "Mastercard", "example.com"):
        assert bad not in clean, bad
    assert "$25.00" in clean and "xAI" in clean
    import base64 as _b64
    tools = {"Codex": "codex_plan"}
    enc = lambda d: _b64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    got = parse_cookie(enc({"v": [{"tool": "Codex", "kind": "warning", "text": "Visa 4242 limit", "seen_at": 560},
                                  {"tool": "Nope", "kind": "warning", "text": "x", "seen_at": 1},
                                  {"tool": "Codex", "kind": "bogus", "text": "x", "seen_at": 1}],
                            "r": [{"source": "xai_api", "vendor": "xAI", "amount": 25, "paid_at": 560},
                                  {"source": "xai_api", "vendor": "xAI", "amount": -5, "paid_at": 560},
                                  {"source": "nope", "vendor": "x", "amount": 5, "paid_at": 1}]}), tools, {"xai_api"})
    assert len(got["vendor"]) == 1 and "4242" not in got["vendor"][0]["text"]
    assert len(got["refills"]) == 1
    assert parse_cookie("%%%not-base64", tools, {"xai_api"}) == {"vendor": [], "refills": []}
    assert parse_cookie(enc([1, 2]), tools, {"xai_api"}) == {"vendor": [], "refills": []}
    c = create_app(sources="sample", testing=True).test_client()
    c.set_cookie(_cookie_name("guild"), "garbage!!")
    assert c.get("/guild/operate?tile=usage").status_code == 200                      # malformed cookie never breaks a page
