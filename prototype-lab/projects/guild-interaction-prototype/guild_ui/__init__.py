"""Guild interaction UI as a Flask blueprint (SPEC rev 2 §8).

The standalone prototype registers it with prototype=True and a no-op owner
guard. With prototype=False, registration fails closed unless a real owner
guard and the server write services are bound (Codex F5). Templates and
assets are intended for reuse in minimoi_portal; production binding is a
separate reviewed change.
"""
from __future__ import annotations

import functools
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, abort, current_app, jsonify, redirect, render_template, request, send_from_directory, url_for
from markupsafe import Markup, escape

from .adapters import ACTIVE, STATUSES, SampleSessions, FIXTURES, build_sources
from .lights import derive_lights
from .usage import DEFAULT_PRECEDENCE, summarize as usage_summary
from .evidence import cookie_name, parse_cookie

PROJECT = Path(__file__).resolve().parents[1]
CONFIG = PROJECT / "config"
STATIC = PROJECT / "static" / "guild-ui"
STORAGE_NAMES = ["bench.v1", "conversation.v1", "overlay.v1", "clock.v1", "nav.v1"]
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
       "frame-ancestors 'none'; form-action 'self'")

bp = Blueprint("guild_ui", __name__, template_folder=str(PROJECT / "templates"))


class GuildBindingError(RuntimeError):
    """Raised when production mode is registered without real guard/services."""


def _prototype_owner_noop(view):
    """Prototype-only identity guard: every local request is the owner.
    Used only when prototype=True AND no owner_guard is passed."""
    return view


def _sample_user():
    return {"display_name": "Robert", "tier": "owner", "sample": True}


def require_owner(view):
    """The single owner-guard seam. The bound guard is a decorator with the same
    shape as the portal's _require_owner; it is applied per request so the
    blueprint can be bound to different guards in different apps."""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        guard = current_app.extensions["guild_ui"]["owner_guard"]
        return guard(view)(*args, **kwargs)
    wrapped.guild_owner_seam = True
    return wrapped


def portal_nav_stub(user, active):
    """Stand-in for the portal's portal_nav_html Jinja global, registered only
    when the host app has none. Other workspaces are labels, not links."""
    name = escape((user or {}).get("display_name", ""))
    spaces = ["Curator", "Mein Deutsch", "Meu Português", "Guild", "CoS"]
    links = "".join(
        f'<span class="portal-workspace-link is-active" aria-current="page">{s}</span>' if s == "Guild"
        else f'<span class="portal-workspace-link is-off" title="Not part of this prototype">{s}</span>'
        for s in spaces)
    return Markup('<div id="portal-nav-bar"><span id="portal-nav-brand">mini-moi</span>'
                  '<span id="portal-nav-divider" aria-hidden="true">|</span>'
                  f'<span id="portal-nav-workspaces">{links}</span>'
                  f'<span class="portal-nav-account">{name}</span></div>')


def register_guild_ui(app, *, prototype: bool, owner_guard=None, write_services=None, url_prefix: str = "",
                      current_user=None, sources: str = "live", queue_path=None, repo_root=None, records_db=None):
    """Mount the Guild UI. prototype=False fails closed unless a real owner guard
    and write services are bound (rev 2 §8.3). A passed owner_guard is always
    used, also in prototype mode (dev mount). url_prefix moves every route and
    the asset path under the prefix (e.g. '/guild-proto')."""
    if not prototype:
        if owner_guard is None or owner_guard is _prototype_owner_noop:
            raise GuildBindingError("PROTOTYPE=False requires a real owner guard (bind the portal's _require_owner)")
        if write_services is None:
            raise GuildBindingError("PROTOTYPE=False requires server write services (proposal/receipt store)")
    url_prefix = (url_prefix or "").rstrip("/")
    if url_prefix and not url_prefix.startswith("/"):
        raise ValueError("url_prefix must start with '/'")
    app.extensions["guild_ui"] = {
        "prototype": prototype,
        "owner_guard": owner_guard or _prototype_owner_noop,
        "write_services": write_services,
        "url_prefix": url_prefix,
        "current_user": current_user or _sample_user,
        "sources": build_sources(sources, queue_path=queue_path, repo_root=repo_root, records_db=records_db),
    }
    if "portal_nav_html" not in app.jinja_env.globals:
        app.jinja_env.globals["portal_nav_html"] = portal_nav_stub
    app.register_blueprint(bp, url_prefix=url_prefix or None)
    return app


@bp.after_request
def _blueprint_headers(resp):
    """Scoped to this blueprint's responses only; host-app routes are untouched."""
    resp.headers["Content-Security-Policy"] = CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "no-store"
    return resp


def storage_namespace() -> str:
    return "guild" + (_cfg()["url_prefix"].replace("/", ".") if _cfg()["url_prefix"] else "")


def storage_keys() -> list[str]:
    ns = storage_namespace()
    return [f"{ns}.{n}" for n in STORAGE_NAMES]


# ── helpers ───────────────────────────────────────────────────────────────

def _cfg():
    return current_app.extensions["guild_ui"]


def _load(name):
    return json.loads((CONFIG / name).read_text(encoding="utf-8"))


def _hhmm(iso):
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%a %-H:%M")
    except ValueError:
        return iso


def badge(res):
    """Badge text for a SourceResult: live · time | sample | not instrumented (+ unknown/stale)."""
    if res.source == "live":
        text = f"live · read {_hhmm(res.observed_at).split(' ')[-1]}"
    elif res.source == "sample":
        text = "sample"
    else:
        text = "not instrumented"
    if res.status == "unknown" and res.source != "not_instrumented":
        text += " · unknown"
    elif res.status == "stale":
        text += " · stale"
    return {"text": text, "source": res.source, "status": res.status, "error": res.error,
            "reason": res.reason, "evidence": res.evidence, "note": getattr(res, "note", None)}


def _age(iso):
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return ""
    days = int((datetime.now(timezone.utc) - dt).total_seconds() // 86400)
    return "today" if days <= 0 else f"{days}d"


def _eval_show_when(cond, state):
    return all(state.get(k) == v for k, v in (cond or {}).items())


DEFAULT_WORLD = {"clock": "sat", "decision": False, "needs_posted": False, "invited": False}


def _hill_points(items):
    """Spec Ready on the uphill (figuring out), In Build on the downhill (executing)."""
    up = [i for i in items if i["status"] == "spec_ready"]
    down = [i for i in items if i["status"] == "in_build"]
    pts = []
    for group, (x0, x1) in ((up, (60, 200)), (down, (270, 420))):
        for n, item in enumerate(group):
            x = x0 + (x1 - x0) * (n + 1) / (len(group) + 1)
            t = (x - 10) / 440  # 0..1 along the hill
            y = 84 - 68 * (1 - (2 * t - 1) ** 2)
            pts.append({"x": round(x), "y": round(y), "id": item["id"], "title": item["title"],
                        "status": item["status"], "phase": "figuring out" if item["status"] == "spec_ready" else "executing"})
    return pts


def _queue_summary(res):
    if not res.ok:
        return {"badge": badge(res), "spec_ready": [], "in_build": []}
    return {"badge": badge(res), "note": res.note or "",
            "spec_ready": [f"#{i['id']} {i['title']}" for i in res.data if i["status"] == "spec_ready"],
            "in_build": [f"#{i['id']} {i['title']}" for i in res.data if i["status"] == "in_build"]}


def _filed_evidence(doc):
    """Vendor warnings and refill receipts filed in this browser (cookie), validated."""
    balances = {s["id"] for s in doc["sources"] if s["kind"] == "balance"}
    return parse_cookie(request.cookies.get(cookie_name(storage_namespace())), doc.get("vendor_tools", {}), balances)


def _usage(src, layout):
    """Usage & limits summaries for both simulated clocks (rev 3.1)."""
    res = src.operate.usage()
    cfg = next((l for l in layout.get("floor", {}).get("lights", []) if l.get("rule") == "usage"), {})
    prec = tuple(cfg.get("precedence") or DEFAULT_PRECEDENCE)
    filed = _filed_evidence(res.data)
    return {k: usage_summary(res.data, k, prec, filed["vendor"], filed["refills"]) for k in ("sat", "mon")}


def _capture_cfg(src):
    """What the browser needs to file vendor warnings and refill receipts (rev 3.1)."""
    res = src.operate.usage()
    doc = res.data or {}
    prefix = _cfg()["url_prefix"]
    return {"cookie": cookie_name(storage_namespace()), "cookie_path": prefix or "/",
            "tools": list(doc.get("vendor_tools", {}).keys()),
            "balances": [{"id": s["id"], "label": s["label"]} for s in doc.get("sources", []) if s["kind"] == "balance"],
            "now": doc.get("now", {})}


def _tile(tile_cfg, src, queue_res, usage=None):
    if tile_cfg.get("rule") == "usage" and usage:
        variants = []
        for k, u in usage.items():
            lt, w = u["light"], u["light"]["worst"]
            value = f"{w['short']} {w['used_text']}" if lt["state"] in ("red", "yellow") and w else lt["word"]
            variants.append({"when": {"clock": k}, "value": value, "sub": lt["reason"], "state": lt["state"],
                             "word": lt["word"], "shape": lt["shape"], "observed": u["observed"]})
        return {**tile_cfg, "variants": variants, "value": variants[0]["value"], "sub": variants[0]["sub"],
                "observed": "", "unknown": False, "phone": variants[0]["value"],
                "badge": {"text": "sample", "source": "sample", "status": "ok"}}
    if tile_cfg.get("live") == "queue_tile":
        if not queue_res.ok:
            return {**tile_cfg, "value": "—", "sub": "queue read failed", "badge": badge(queue_res),
                    "observed": "", "unknown": True}
        active = [i for i in queue_res.data if i["status_known"] and i["status"] in ACTIVE]
        blocked = [i for i in queue_res.data if i["status_known"] and i["status"] == "blocked"]
        sub = f"{len(blocked)} blocked" + (f" · {queue_res.note}" if queue_res.note else "")
        return {**tile_cfg, "value": f"{len(active)} active", "sub": sub,
                "badge": badge(queue_res), "observed": _hhmm(queue_res.observed_at), "unknown": False}
    res = src.operate.tile(tile_cfg["id"])
    d = res.data or {}
    return {**tile_cfg, "value": d.get("value", "—"), "sub": d.get("sub", "no source configured"),
            "observed": d.get("observed", ""), "badge": badge(res), "phone": d.get("phone", d.get("value", "—")),
            "stale_reason": d.get("stale_reason", ""), "unknown": res.status != "ok"}


def _context(area, page_id, context_item="", **extra):
    cfg = _cfg()
    src = cfg["sources"]
    layout, scenario = _load("layout.json"), _load("scenario.json")
    queue_all = src.queue.list_items()
    usage = _usage(src, layout)
    tiles = {t["id"]: _tile(t, src, queue_all, usage) for t in layout["operate"]["tiles"]}
    phone_numbers = [{"id": n, "label": layout["phone"]["labels"][n], "value": tiles[n].get("phone", tiles[n]["value"]),
                      "variants": tiles[n].get("variants"),
                      "badge": tiles[n]["badge"], "unknown": tiles[n]["unknown"]} for n in layout["phone"]["numbers"]]
    urls = {
        "landing": url_for("guild_ui.landing"), "bench": url_for("guild_ui.bench"),
        "queue": url_for("guild_ui.queue"), "operate": url_for("guild_ui.operate"),
        "build": url_for("guild_ui.floor"), "floor": url_for("guild_ui.floor"), "improve": url_for("guild_ui.improve"),
        "experiment": url_for("guild_ui.experiment"),
        "item": url_for("guild_ui.item", item_id=987654321).replace("987654321", "__ID__"),
        "reset": url_for("guild_ui.proto_reset") if cfg["prototype"] else "",
    }
    page = {
        "prototype": cfg["prototype"],
        "base": cfg["url_prefix"], "urls": urls, "storage_ns": storage_namespace(),
        "area": area, "page": page_id, "item": context_item,
        "layout": {"version": layout["layout_version"], "bench": layout["bench"]},
        "scenario": scenario if cfg["prototype"] else None,
        "queue_summary": _queue_summary(queue_all),
        "usage": {k: {"state": u["light"]["state"], "reason": u["light"]["reason"], "reply": u["reply"],
                      "shift": u["shift"]} for k, u in usage.items()},
        "capture": _capture_cfg(src),
        "storage_keys": storage_keys(),
    }
    return dict(area=area, page_id=page_id, context_item=context_item, layout=layout, scenario=scenario,
                prototype=cfg["prototype"], sources_mode=src.mode, tiles=tiles, phone_numbers=phone_numbers, usage=usage,
                needs_rows=scenario["bench"]["needs"], world=DEFAULT_WORLD, show=_eval_show_when,
                user=cfg["current_user"](), page_json=page, urls=urls, **extra)


# ── pages ─────────────────────────────────────────────────────────────────

@bp.route("/guild", strict_slashes=False)
@require_owner
def landing():
    ctx = _context("Guild", "landing", "nothing selected")
    q = _cfg()["sources"].queue.list_items(ACTIVE)
    doors = []
    for d in ctx["layout"]["doors"]:
        parts = []
        for s in d["signals"]:
            if s.get("live") == "queue_active":
                if q.ok:
                    text = s["template"].format(n=len(q.data)) + (f" ({q.note})" if q.note else "")
                    parts.append({"text": text, "badge": badge(q)})
                else:
                    parts.append({"text": "queue: treat as unknown", "badge": badge(q)})
            else:
                parts.append({"text": s["text"], "badge": {"text": s["source"], "source": s["source"], "status": "ok"}})
        doors.append({**d, "parts": parts})
    return render_template("guild/ui_landing.html", doors=doors, **ctx)


@bp.route("/guild/build", strict_slashes=False)
@require_owner
def floor():
    """Shop floor (SPEC rev 3 §2a): conversation first, status strip, Needs you,
    Continue, Post-its. Under a mount prefix this never shadows the portal's
    /guild/build (Build Log)."""
    src = _cfg()["sources"]
    ctx = _context("Build", "floor", "Shop floor")
    fl = ctx["layout"]["floor"]
    queue_all = src.queue.list_items()
    lights = derive_lights(fl["lights"], src, queue_all)
    for light in lights:
        kind, _, tile = light["detail"].partition(":")
        light["href"] = url_for("guild_ui.queue") if kind == "queue" else url_for("guild_ui.operate", tile=tile)
    work_item = ctx["scenario"]["work_item"]
    details = {"queue_item": url_for("guild_ui.queue", focus=work_item),
               "bench": url_for("guild_ui.bench"),
               "decision": url_for("guild_ui.bench") + "#decision"}
    reminders = [{**r, "href": details[r["detail"]]} for r in ctx["scenario"]["reminders"]]
    limit_rows = [{"tag": "Limit", "text": u["light"]["reason"], "href": url_for("guild_ui.operate", tile="usage"),
                   "show_when": {"clock": k}, "usage_alert": True}
                  for k, u in ctx["usage"].items() if u["light"]["state"] in ("red", "yellow")]
    check_rows = [{"tag": "Check", "text": f"reset window: {m['text'].split(' — ')[0]}",
                   "href": url_for("guild_ui.operate", tile="usage"), "show_when": {"clock": k}}
                  for k, u in ctx["usage"].items() for m in u["mismatches"]]
    at = 1 if reminders and reminders[0]["detail"] == "decision" else 0
    reminders[at:at] = limit_rows + check_rows
    postits = ctx["scenario"]["bench"]["postits"][: fl["max_postits"]]
    return render_template("guild/ui_floor.html", lights=lights, reminders=reminders, postits=postits,
                           floor_cfg=fl, **ctx)


@bp.route("/guild/build/bench", strict_slashes=False)
@require_owner
def bench():
    src = _cfg()["sources"]
    ctx = _context("Build", "bench", "“file this” rollout")
    panels_cfg = {p["id"]: p for p in ctx["layout"]["bench"]["panels"]}
    work_item_id = ctx["scenario"]["work_item"]
    work_item = src.queue.get_item(work_item_id)
    active = src.queue.list_items(ACTIVE)
    blocked = src.queue.list_items(("blocked",))
    sessions = src.sessions.list_sessions()
    sessions_optin = False
    if sessions.reason == "not_configured" and panels_cfg["discussions"].get("when_not_configured") == "sample":
        sessions = SampleSessions(FIXTURES / "sessions.sample.json").list_sessions()
        sessions_optin = True
    commits = src.activity.recent_commits(5)
    op = src.operate
    data = {
        "subject": {"rollout": op.rollout(), "evidence": op.evidence_marks(), "work_item": work_item,
                    "work_item_id": work_item_id},
        "needs": {"rows": ctx["scenario"]["bench"]["needs"]},
        "motion": {"res": active, "points": _hill_points(active.data) if active.ok else []},
        "discussions": {"res": sessions, "optin": sessions_optin},
        "since": {"res": commits, "label": ctx["scenario"]["bench"]["since_label_sample"] if commits.source == "sample" else "recent commits"},
        "blocked": {"res": blocked, "rows": ctx["scenario"]["bench"]["blocked"]},
        "postits": {"notes": ctx["scenario"]["bench"]["postits"]},
    }
    return render_template("guild/ui_bench.html", panels_cfg=panels_cfg, data=data, badge=badge, age=_age,
                           hhmm=_hhmm, **ctx)


@bp.route("/guild/build/queue", strict_slashes=False)
@require_owner
def queue():
    src = _cfg()["sources"]
    res = src.queue.list_items()
    ctx = _context("Build Queue", "queue", "Build Queue")
    rows = res.data or []
    items = sorted([i for i in rows if i["status_known"] and i["status"] in ACTIVE],
                   key=lambda i: i["last_transition_at"] or "", reverse=True)
    unknown_rows = [i for i in rows if not i["status_known"]]
    return render_template("guild/ui_build_queue.html", res=res, items=items, unknown_rows=unknown_rows, statuses=STATUSES,
                           badge=badge, age=_age, focus_id=request.args.get("focus", type=int), **ctx)


@bp.route("/guild/build/items/<int:item_id>", strict_slashes=False)
@require_owner
def item(item_id):
    src = _cfg()["sources"]
    res = src.queue.get_item(item_id)
    if res.ok and res.data is None:
        ctx = _context("Build Queue", "item", f"#{item_id} (not found)")
        return render_template("guild/ui_not_found.html", item_id=item_id, **ctx), 404
    title = res.data["title"] if res.ok else "unknown"
    ctx = _context("Build Queue", "item", f"#{item_id} {title}")
    ctx["page_json"]["item_id"] = item_id
    return render_template("guild/ui_build_item.html", res=res, it=res.data, item_id=item_id,
                           history=src.queue.history(item_id), statuses=STATUSES, badge=badge, age=_age, **ctx)


@bp.route("/guild/operate", strict_slashes=False)
@require_owner
def operate():
    src = _cfg()["sources"]
    ctx = _context("Operate", "operate", "Capabilities → “file this”")
    op = src.operate
    tile_ids = [t["id"] for t in ctx["layout"]["operate"]["tiles"]]
    wanted = request.args.get("tile")
    ctx["selected_tile"] = wanted if wanted in tile_ids else ctx["layout"]["operate"]["default_selected"]
    return render_template("guild/ui_operate.html", rollout=op.rollout(), evidence=op.evidence_marks(),
                           next_steps=op.next_steps(), journeys=op.journeys(), badge=badge, **ctx)


@bp.route("/guild/improve", strict_slashes=False)
@require_owner
def improve():
    return render_template("guild/ui_quiet.html", heading="Improve", **_context("Improve", "improve", "nothing selected"))


@bp.route("/guild/experiment", strict_slashes=False)
@require_owner
def experiment():
    return render_template("guild/ui_quiet.html", heading="Experiment", **_context("Experiment", "experiment", "nothing selected"))


def config_digest() -> str:
    h = hashlib.sha256()
    for p in sorted(list(CONFIG.glob("*.json")) + list(FIXTURES.glob("*.json"))):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


@bp.route("/guild/proto/reset", methods=["POST"])
@require_owner
def proto_reset():
    """Prototype only. The server holds no mutable state: it confirms the
    defaults (by digest) and tells the client which keys to clear."""
    if not _cfg()["prototype"]:
        abort(404)
    return jsonify({"reset": True, "storage_keys": storage_keys(), "evidence_cookie": cookie_name(storage_namespace()),
                    "layout_version": _load("layout.json")["layout_version"],
                    "defaults_digest": config_digest()})


@bp.route("/guild/ui-assets/<path:filename>")
@require_owner
def asset(filename):
    """Blueprint assets under the blueprint's own (prefixable) path, behind the
    owner seam; never shadows the host app's /static."""
    return send_from_directory(STATIC, filename, max_age=0)
