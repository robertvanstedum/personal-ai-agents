"""A local sample instance of the portal for review captures (loopback only).

    python -m scripts.tools.tour_capture.local_sample --port 8791

Serves this checkout's portal (Guild with the Shop floor on, /guild-next, and
CoS through its real /app/cos proxy) on 127.0.0.1 with sample data, so a
scenario with ``auth_profile: "none"`` can capture pages that normally need
Robert's login. It reuses the Shop floor browser harness's setup
(tests/guild/shop_floor: the sample queue, the floor store on SQLite, the
signed-in owner) and adds, for captures only:

* every loopback request is signed in as the sample owner; nothing else can
  reach the server, which binds 127.0.0.1 only;
* no secret is read (the secret lookup raises), no database URL is used, and
  no model is called: Master Craftsman talks to a scripted relay in this
  process, and CoS to a stand-in cos-scheduler that echoes the message;
* ``POST /__tour_sample/<action>`` sets up a scene (reset, queue, mc,
  mc_release, postit, continue, voice), driven by a scenario's ``sample`` step.

Everything it shows is sample data; captions and PDFs must say so.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
LOOPBACK_ADDRS = {"127.0.0.1", "::1"}
SAMPLE_PREFIX = "/__tour_sample/"

# A stand-in for the OpenAI WebRTC voice adapter: the same interface, no
# microphone and no network. A start "connects" at once; the bootstrap answer
# (sample action ``voice``) decides whether a start gets that far.
SAMPLE_VOICE_ADAPTER = """
// Sample stand-in for the OpenAI WebRTC adapter (tour capture): no network, no microphone.
export class OpenAIWebRTCAdapter {
  constructor() { this._listeners = {}; }
  on(name, fn) { (this._listeners[name] ||= []).push(fn); }
  _emit(name, payload) { for (const fn of this._listeners[name] || []) fn(payload); }
  prepareSession(config) { this._config = config; }
  async connect(credentials) {}
  start() {}
  mute() {}
  unmute() {}
  sendContinuationInstruction() {}
  sendFunctionResult() {}
  end(reason) { this._emit('closed', { reason }); }
}
"""


class SampleServerError(RuntimeError):
    """Raised when the sample server cannot be built or bound safely."""


def _nd(obj) -> bytes:
    return (json.dumps(obj) + "\n").encode()


class _Resp:
    def __init__(self, status: int, text: str, headers: dict | None = None):
        self.status_code, self.text, self.headers = status, text, headers or {}

    def json(self):
        return json.loads(self.text)


class _StreamResp:
    """The relay's NDJSON stream: deltas, pauses, and a hold until released."""

    def __init__(self, items: list, relay: "ScriptedRelay"):
        self.items, self.relay = items, relay
        self.status_code, self.text = 200, ""
        self.headers = {"Content-Type": "application/x-ndjson; charset=utf-8"}

    def iter_content(self, chunk_size=None):
        for item in self.items:
            if item == "WAIT":
                end = time.monotonic() + self.relay.hold_s
                while not (self.relay.gate.is_set() or self.relay.stopped.is_set()) and time.monotonic() < end:
                    self.relay.gate.wait(0.02)
                if self.relay.stopped.is_set():
                    yield _nd({"t": "error", "class": "stopped"})
                    return
                continue
            if isinstance(item, (int, float)):
                time.sleep(min(float(item), 3.0))
                continue
            yield _nd(item)

    def close(self):
        pass


class ScriptedRelay:
    """Answers the OpenClaw adapter the way MiniMoi's mc-relay would, from a script.

    A script item is text (a delta), a number (seconds to pause), ``"WAIT"``
    (hold until ``mc_release`` or Stop, at most ``hold_s``) or an event object
    such as ``{"t": "error", "class": "idle"}``. A finish and a usage line
    (``tokens`` output tokens) close the script unless it ends in an error.
    """

    def __init__(self):
        self.gate, self.stopped = threading.Event(), threading.Event()
        self.ready = True
        self.fail_status: int | None = None     # answer every turn with this HTTP status (a failing runtime)
        self.hold_s = 20.0
        self.items: list = []
        self.posts: list[str] = []
        self.set_script(["ok"], tokens=1)

    def set_script(self, script: list, *, tokens: int = 7) -> None:
        items: list = []
        for part in script:
            if isinstance(part, str) and part != "WAIT":
                items.append({"t": "delta", "text": part})
            elif isinstance(part, bool) or not isinstance(part, (str, int, float, dict)):
                raise SampleServerError(f"unsupported script item: {part!r}")
            else:
                items.append(part)
        if not any(isinstance(i, dict) and i.get("t") == "error" for i in items):
            items += [{"t": "finish", "reason": "stop"},
                      {"t": "usage", "prompt_tokens": 900, "completion_tokens": int(tokens)}]
        self.items = items
        self.gate.clear()
        self.stopped.clear()

    def text(self) -> str:
        return "".join(i["text"] for i in self.items if isinstance(i, dict) and i.get("t") == "delta")

    def get(self, url, **_kw):
        return _Resp(200 if self.ready else 503, '{"ready":true}' if self.ready else '{"ready":false}')

    def post(self, url, data=None, headers=None, stream=False, **_kw):
        self.posts.append(url)
        if url.endswith("/turns/stop"):
            self.stopped.set()
            return _Resp(200, '{"stopped":true}')
        if self.fail_status:
            return _Resp(self.fail_status, '{"error": "sample failure"}', {"Content-Type": "application/json"})
        if not stream:
            return _Resp(200, json.dumps({
                "id": "chatcmpl_sample", "object": "chat.completion", "model": "openclaw/mc-agent",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": self.text() or "ok"},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}),
                {"X-MC-Correlation-Id": (headers or {}).get("X-MC-Correlation-Id")})
        return _StreamResp(list(self.items), self)


def _stand_in_cos(state: dict):
    """cos-scheduler's pages with the real template and voice files, and sample answers."""
    from flask import Flask, Response, jsonify, render_template, request, send_from_directory

    app = Flask("cos_sample", template_folder=str(REPO / "domains/cos/templates"),
                static_folder=str(REPO / "domains/cos/static"))

    @app.route("/ui/<tab>")
    def ui(tab):
        return render_template("cos_ui.html", initial_tab=tab)

    @app.route("/static/realtime-voice/<path:name>")
    def voice_static(name):
        if name.endswith("openai-webrtc-adapter.js"):
            return Response(SAMPLE_VOICE_ADAPTER, mimetype="text/javascript")
        return send_from_directory(REPO / "core/realtime_voice/static", name)

    @app.route("/status")
    def status():
        return jsonify({"backend_label": "sample", "model_label": "none"})

    @app.route("/ui/api/<what>")
    def lists(what):
        return jsonify([])

    @app.route("/api/realtime-voice/confer/capabilities")
    def capabilities():
        return jsonify({"ok": True, "default_provider": "openai",
                        "providers": [{"provider": "openai", "label": "OpenAI Voice"}]})

    @app.route("/api/realtime-voice/confer/bootstrap", methods=["POST"])
    def bootstrap():
        if state.get("voice") != "ok":
            return jsonify({"ok": False, "error": "voice is unavailable right now"}), 503
        return jsonify({"ok": True, "provider": "openai", "model": "sample", "client_secret": "sample-not-a-secret",
                        "warning_minutes": 20, "max_minutes": 30, "session_config": {}})

    @app.route("/ui/send", methods=["POST"])
    def send():
        return jsonify({"reply": f"Noted: {(request.get_json(silent=True) or {}).get('text', '')}"})

    return app


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@dataclass
class GuildSample:
    """The sample portal and its state; ``app`` is the Flask app to serve."""

    workdir: Path
    app: object = None
    relay: ScriptedRelay = field(default_factory=ScriptedRelay)
    cos_state: dict = field(default_factory=lambda: {"voice": "fail"})
    features: dict = field(default_factory=dict)

    # ── building ──────────────────────────────────────────────────────────
    def build(self) -> "GuildSample":
        for extra in (REPO / "tests" / "guild" / "shop_floor", REPO):
            if str(extra) not in sys.path:
                sys.path.insert(0, str(extra))
        import core.get_secret as secrets_module
        import minimoi_portal.config as portal_config
        from domains.guild import queue_store as qs
        from floor_helpers import OWNER, write_queue

        def no_secrets(*_a, **_k):
            raise RuntimeError("the sample server reads no secrets")

        env_keys = ("DATABASE_URL", "GUILD_RECORDS_DB", "SENTRY_DSN", "MC_RUNTIME_URL", "MC_RUNTIME_TOKEN",
                    "MINIMOI_GUILD_NEXT", "MINIMOI_USAGE_DIR", "MINIMOI_WORKSHOPS_DIR", "MINIMOI_WORKSHOP_ID")
        self._restore = {"env": {k: os.environ.get(k) for k in env_keys},
                         "get_secret": secrets_module.get_secret,
                         "in_container": qs._running_in_container,
                         "queue_path": portal_config.GUILD_QUEUE_PATH, "base_url": portal_config.BASE_URL,
                         "cos_backend": getattr(portal_config, "COS_BACKEND", None)}
        secrets_module.get_secret = no_secrets
        qs._running_in_container = lambda: False
        for var in env_keys[:5]:
            os.environ.pop(var, None)
        os.environ.update({"MINIMOI_GUILD_NEXT": "1", "MINIMOI_USAGE_DIR": str(self.workdir / "usage"),
                           "MINIMOI_WORKSHOPS_DIR": str(self.workdir / "workshops"), "MINIMOI_WORKSHOP_ID": "mac"})
        (self.workdir / "usage").mkdir(parents=True, exist_ok=True)
        self.queue = write_queue(self.workdir / "guild" / "build_queue.json")
        self._write_queue = write_queue
        portal_config.GUILD_QUEUE_PATH = str(self.queue)
        portal_config.BASE_URL = "https://dev.minimoi.ai"          # as the harness: links read like dev's

        spec = importlib.util.spec_from_file_location("portal_app_tour_sample", REPO / "minimoi_portal" / "app.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        app = module.app
        app.config["SESSION_COOKIE_SECURE"] = False                 # plain http on loopback
        owner = dict(OWNER)

        from flask import abort, jsonify, request, session

        def sign_in_loopback():
            if request.remote_addr not in LOOPBACK_ADDRS:
                abort(403)
            if session.get("user") != owner:
                session["user"] = dict(owner)
        app.before_request_funcs.setdefault(None, []).insert(0, sign_in_loopback)

        def control(action):
            if request.remote_addr not in LOOPBACK_ADDRS:
                abort(403)
            try:
                result = self.control(action, request.get_json(silent=True) or {})
            except SampleServerError as exc:
                return jsonify({"ok": False, "error": str(exc)}), 400
            return jsonify({"ok": True, **(result or {})})
        app.add_url_rule(f"{SAMPLE_PREFIX}<action>", "tour_sample_control", control, methods=["POST"])

        self.app = app
        self.services = app.extensions["guild_ui_next"]["services"]
        self._saved_mc = (self.services.mc, self.services.mc_health)
        self.features = {"stream": hasattr(self.services, "mc_stream"),
                         "workshop": importlib.util.find_spec("minimoi_portal.workshop") is not None}
        self.reset({})
        return self

    def restore(self) -> None:
        """Put back what build() changed in this process (secrets hook, env, portal config)."""
        import core.get_secret as secrets_module
        import minimoi_portal.config as portal_config
        from domains.guild import queue_store as qs

        saved = getattr(self, "_restore", None)
        if not saved:
            return
        for key, value in saved["env"].items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        secrets_module.get_secret = saved["get_secret"]
        qs._running_in_container = saved["in_container"]
        portal_config.GUILD_QUEUE_PATH, portal_config.BASE_URL = saved["queue_path"], saved["base_url"]
        portal_config.COS_BACKEND = saved["cos_backend"]
        self._restore = None

    # ── scene set-up ──────────────────────────────────────────────────────
    def control(self, action: str, args: dict) -> dict | None:
        handler = {"reset": self.reset, "queue": self.set_queue, "mc": self.set_mc, "mc_release": self.mc_release,
                   "postit": self.add_postit, "continue": self.set_continue, "voice": self.set_voice}.get(action)
        if handler is None:
            raise SampleServerError(f"unknown sample action {action!r}")
        return handler(args)

    def reset(self, args: dict) -> dict:
        """A fresh sample: the harness queue, an empty floor, no conversations,
        Master Craftsman off, a seeded Workshop, voice failing to start."""
        from floor_db_helpers import SqliteFloor
        from domains.guild import queue_store as qs

        for child in self.queue.parent.iterdir():
            if child != self.queue:
                shutil.rmtree(child) if child.is_dir() else child.unlink()
        self._write_queue(self.queue)
        floor_dir = self.workdir / "floor"
        shutil.rmtree(floor_dir, ignore_errors=True)
        self.floor = SqliteFloor(floor_dir)
        self.services.floor = self.floor.store()
        assert not (self.queue.parent / qs.JOURNAL_NAME).exists()
        self.set_mc({"mode": "off"})
        self.cos_state["voice"] = "fail"
        self._seed_workshop()
        return {"features": self.features}

    def set_queue(self, args: dict) -> None:
        from floor_helpers import QUEUE_ITEMS
        variant = args.get("variant", "default")
        if variant == "default":
            self._write_queue(self.queue, QUEUE_ITEMS)
        elif variant == "quiet":            # nothing blocked: nothing needs Robert
            self._write_queue(self.queue, [i for i in QUEUE_ITEMS if i.get("status") != "blocked"])
        else:
            raise SampleServerError(f"unknown queue variant {variant!r}")

    def set_mc(self, args: dict) -> None:
        """mode off (no runtime), turns (one answer), stream (streamed), down (runtime not
        answering); ``fail`` (an HTTP status) makes a reachable runtime fail every turn."""
        mode = args.get("mode", "off")
        services = self.services
        if mode == "off":
            services.mc, services.mc_health = self._saved_mc
            services.mc_turns = False
            if self.features["stream"]:
                services.mc_stream = False
            return
        if mode not in ("turns", "stream", "down"):
            raise SampleServerError(f"unknown mc mode {mode!r}")
        if mode == "stream" and not self.features["stream"]:
            raise SampleServerError("this checkout has no streaming Master Craftsman")
        from minimoi_portal.guild_ui.mc import CachedHealth
        from minimoi_portal.guild_ui.mc.openclaw import OpenClawMasterCraftsman
        self.relay = ScriptedRelay()
        self.relay.ready = mode != "down"
        if args.get("fail") is not None:
            if not isinstance(args["fail"], int) or not 400 <= args["fail"] <= 599:
                raise SampleServerError("fail must be an HTTP error status")
            self.relay.fail_status = args["fail"]
        if "script" in args:
            if not isinstance(args["script"], list):
                raise SampleServerError("script must be a list")
            self.relay.set_script(args["script"], tokens=int(args.get("tokens", 7)))
        backend = OpenClawMasterCraftsman("http://mc-relay.sample:8790/v1", "sample-relay-token-" + "0" * 24,
                                          http_get=self.relay.get, http_post=self.relay.post)
        services.mc, services.mc_health, services.mc_turns = backend, CachedHealth(backend), True
        if self.features["stream"]:
            services.mc_stream = mode == "stream"
            self._clear_inflight()

    def mc_release(self, args: dict) -> None:
        self.relay.gate.set()

    def add_postit(self, args: dict) -> None:
        from minimoi_portal.guild_ui.stores import MASTER_CRAFTSMAN, Author
        author = MASTER_CRAFTSMAN if args.get("author") == "mc" else Author("robert", "owner", "Robert")
        text = str(args.get("text") or "").strip()
        if not text:
            raise SampleServerError("a post-it needs text")
        result = self.services.floor.add_postit(text, author, idempotency_key=f"sample-{time.time_ns()}")
        if not getattr(result, "ok", True):
            raise SampleServerError(f"post-it not added: {result}")

    def set_continue(self, args: dict) -> None:
        item = str(args.get("item", "12"))
        label = str(args.get("label") or f"#{item}")
        self.services.floor.set_continue("robert", kind="item", ref=item, label=label,
                                         idempotency_key=f"sample-cont-{time.time_ns()}")

    def set_voice(self, args: dict) -> None:
        if args.get("boot") not in ("ok", "fail"):
            raise SampleServerError("voice boot must be 'ok' or 'fail'")
        self.cos_state["voice"] = args["boot"]

    def _clear_inflight(self) -> None:
        try:
            from minimoi_portal.guild_ui.api import _MC_INFLIGHT
            from minimoi_portal.guild_ui.mc.streaming import DISPATCHED
        except ImportError:
            return
        DISPATCHED._items.clear()
        _MC_INFLIGHT.clear()

    def _seed_workshop(self) -> None:
        folder = self.workdir / "workshops"
        shutil.rmtree(folder, ignore_errors=True)
        if not self.features["workshop"]:
            return
        from minimoi_portal.workshop.observer import Observation, health_event
        from minimoi_portal.workshop.record import Workshop, iso, now
        w = Workshop(str(folder), "mac")
        w.append(health_event(Observation(
            observed_at=iso(now()), memory_free_pct=46.0, swap_used_gb=1.2, disk_free_gb=80.0, load_1m=2.1,
            clients=[{"kind": "codex", "pid": 11, "elapsed": "05:00", "counted": True},
                     {"kind": "codex-desktop", "pid": 12, "elapsed": "09:00", "counted": False}],
            clients_known=True), "mac"))
        w.append({"workshop": "mac", "actor": "claude-code", "kind": "started", "item": "queue:12", "stage": "build",
                  "text": "Slice 4a: tests and screenshots", "next_actor": "codex"})
        w.append({"workshop": "mac", "actor": "claude-code", "kind": "needs_you", "item": "queue:12",
                  "text": "Choose the refresh cadence", "next_actor": "robert"})
        w.append({"workshop": "mac", "actor": "claude-code", "kind": "next", "item": "spec:streaming",
                  "text": "Streaming S1 after the reviews"})


def serve(port: int = 0, host: str = "127.0.0.1", workdir: Path | None = None):
    """Build and start the sample portal and its CoS stand-in; returns (sample, url, stop)."""
    if host not in ("127.0.0.1", "localhost"):
        raise SampleServerError("the sample server binds loopback only")
    from werkzeug.serving import make_server
    import minimoi_portal.config as portal_config

    own_dir = workdir is None
    workdir = Path(workdir or tempfile.mkdtemp(prefix="tour-sample-"))
    sample = GuildSample(workdir).build()
    cos_srv = make_server("127.0.0.1", _free_port(), _stand_in_cos(sample.cos_state), threaded=True)
    threading.Thread(target=cos_srv.serve_forever, daemon=True).start()
    portal_config.COS_BACKEND = f"http://127.0.0.1:{cos_srv.server_port}"
    srv = make_server("127.0.0.1", port or _free_port(), sample.app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def stop():
        srv.shutdown()
        cos_srv.shutdown()
        sample.restore()
        if own_dir:
            shutil.rmtree(workdir, ignore_errors=True)
    return sample, f"http://127.0.0.1:{srv.server_port}", stop


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve a local sample portal for review captures (loopback only)")
    parser.add_argument("--port", type=int, default=8791)
    args = parser.parse_args(argv)
    sample, url, stop = serve(args.port)
    print(f"sample portal: {url} (sample data; loopback only; features: {sample.features})", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
