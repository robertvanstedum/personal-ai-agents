"""Fixes from the build reviews (independent: REVIEW_independent_ACTION_WAITING_build.md; Codex:
REVIEW_codex_ACTION_WAITING_build.md). Each test reproduces a finding; it failed before its fix."""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from minimoi_portal import action_waiting as aw, auth
from minimoi_portal.action_waiting import sources as src, store as st, words
from aw_fakes import FAKE_TOKEN, FakeTelegram, build_service
from aw_fakes import auth_dir, clock, make_settings, telegram, token_file

PYTEST_FIXTURES = (auth_dir, clock, make_settings, telegram, token_file,)                  # used by name below
ROOT = Path(__file__).resolve().parents[2]
SECRET = "ghp_" + "A" * 36


@pytest.fixture
def svc(make_settings, clock, telegram, auth_dir):
    s = build_service(make_settings, clock, telegram)
    yield s
    s.sender.release()


def _items(s):
    return st.open_items(s.store.read())


# ---- independent B1 / Codex B3: a sweep's older snapshot never closes a newer item ------------------------------

def test_an_item_recorded_between_the_sources_read_and_the_commit_stays_open_and_is_told(svc, telegram, monkeypatch):
    svc.sweep()                                                    # an empty pending.json is the baseline
    read = svc._collect

    def read_then_register():
        snap = read()                                              # the sweep has read the empty file ...
        entry = auth.create_pending("Ann Guest", "ann@x.test", "pw-12345678")   # ... then /register writes ...
        assert svc.record_guest("ann@x.test", pending=entry) == "W-1"           # ... and ingress records W-1
        return snap
    monkeypatch.setattr(svc, "_collect", read_then_register)
    svc.sweep()
    [item] = _items(svc)
    assert item["code"] == "W-1" and item["state"] == "open"
    monkeypatch.setattr(svc, "_collect", read)
    svc.sweep()
    assert len(telegram.sent) == 1 and "W-1" in telegram.texts[0]


def test_a_reset_recorded_between_the_read_and_the_commit_stays_open_and_is_told(svc, telegram, monkeypatch, auth_dir):
    """The same race on /forgot-password (the independent reviewer's register and reset cases)."""
    auth._write_json("reset_requests.json", {"requests": []})
    svc.sweep()
    read = svc._collect

    def read_then_reset():
        snap = read()
        request = auth.create_reset_request("guest_ab12cd34", "Ann Guest", "ann@x.test")
        assert svc.record_reset(request) == "W-1"
        return snap
    monkeypatch.setattr(svc, "_collect", read_then_reset)
    svc.sweep()
    [item] = _items(svc)
    assert item["kind"] == "password_reset" and item["state"] == "open"
    monkeypatch.setattr(svc, "_collect", read)
    svc.sweep()
    assert len(telegram.sent) == 1 and "Password reset request" in telegram.texts[0]


def test_items_are_marked_told_only_after_telegram_returns(svc, telegram):
    """Independent S7 (mutation M2): at the moment Telegram is called the item is still not told, so a crash during
    the send can never leave a 'told' item that Robert never saw."""
    states = []
    original = telegram.__call__

    class Watching(type(telegram)):
        def __call__(self, req, timeout=None):
            item = svc.store.read()["items"]["W-1"]
            states.append((item["delivery"]["state"], item["delivery"]["told_at"]))
            return original(req, timeout)
    telegram.__class__ = Watching
    svc.record_guest("ann@x.test", pending={"token": "t" * 32, "requested_at": "2026-10-29T19:00:00+00:00"})
    auth._write_json("pending.json", {"pending": [{"token": "t" * 32, "email": "ann@x.test",
                                                   "requested_at": "2026-10-29T19:00:00+00:00"}]})
    svc.sweep()
    assert states == [("pending", None)]
    assert svc.store.read()["items"]["W-1"]["delivery"]["state"] == "told"


def test_a_ref_joined_after_the_read_keeps_its_item_open(svc, telegram, monkeypatch):
    """A re-registration joins an open item whose first entry was answered while the sweep was reading."""
    first = auth.create_pending("Ann Guest", "ann@x.test", "pw-12345678")
    svc.record_guest("ann@x.test", pending=first)
    svc.sweep()
    assert len(telegram.sent) == 1
    assert auth.pending_reject(first["token"]) == "rejected"      # answered: the next read shows nothing
    read = svc._collect

    def read_then_reregister():
        snap = read()
        again = auth.create_pending("Ann Guest", "ann@x.test", "pw-12345678")
        assert svc.record_guest("ann@x.test", pending=again) == "W-1"           # joins the still-open W-1
        return snap
    monkeypatch.setattr(svc, "_collect", read_then_reregister)
    svc.sweep()
    assert [i["code"] for i in _items(svc)] == ["W-1"]


def test_a_snapshot_taken_before_an_ingress_cannot_close_it(svc, clock):
    """Codex's probe: a snapshot built with no sequence mark falls back to its start time."""
    snap = src.Snapshot()
    snap.coverage["pending"] = "ok"
    snap.present["pending"] = set()
    clock.advance(1)
    code = svc.record_guest("synthetic@example.test", pending={"token": "test-ref", "requested_at": clock()})
    with svc.store.edit() as tx:
        src.reconcile(tx, snap, clock())
    assert svc.store.read()["items"][code]["state"] == "open"


# ---- independent S3: a missing source file flags its items, and Robert can dismiss them ----------------------------

def test_a_missing_source_file_flags_its_items_source_lost_and_they_can_be_dismissed(svc, auth_dir):
    entry = auth.create_pending("Ann Guest", "ann@x.test", "pw-12345678")
    svc.record_guest("ann@x.test", pending=entry)
    svc.sweep()
    os.unlink(auth_dir / "pending.json")
    svc.sweep()
    [item] = _items(svc)
    assert item["source_lost"] is True and item["state"] == "open"
    assert svc.dismiss("W-1")[0] == "dismissed"


# ---- Codex B4: identifiers never carry a secret into a store, a notice, MC or the API -------------------------------

def test_secret_bearing_branch_session_and_key_never_reach_a_notice_or_the_store(svc, telegram):
    answer = svc.raise_agent("codex", {"key": f"key-{SECRET}", "words": "Please review this change",
                                       "branch": SECRET, "session": f"s-{SECRET}"})
    svc.sweep()
    item = svc.store.read()["items"][answer["code"]]
    assert SECRET not in words.headline(item) and SECRET not in words.single(svc.settings, item)
    for name in (st.STORE, st.STATE, st.EVENTS):
        path = svc.store.root / name
        assert not path.exists() or SECRET not in path.read_text()
    assert SECRET not in json.dumps(svc.payload())
    assert all(SECRET not in t for t in telegram.texts)
    again = svc.raise_agent("codex", {"key": f"key-{SECRET}", "words": "Please review this change"})
    assert again["code"] == answer["code"] and again["new"] is False          # the key still dedups
    assert svc.resolve_agent("codex", {"key": f"key-{SECRET}"})["state"] == "resolved"


def test_decision_requester_metadata_is_screened_too(make_settings, clock, telegram, auth_dir):
    created = datetime.fromtimestamp(clock(), timezone.utc)
    rows = {"value": [{"id": 3, "requester": {"agent": "codex", "branch": SECRET, "session": "abc12345"},
                       "text": "Deploy please, it is ready", "state": "new", "created_at": created,
                       "expires_at": created + timedelta(hours=2)}]}
    s = build_service(make_settings, clock, telegram, decisions=rows, supervision=True)
    s.sweep()
    [item] = _items(s)
    assert "branch" not in item["facts"] and item["facts"]["session"] == "abc12345"
    s.sender.release()


# ---- D12 (Robert, 29 Oct: "No-name notice"; Codex B5): a fault never sends a name or an email ------------------------

@pytest.fixture
def minimal(monkeypatch):
    from core.supervision.telegram import Bot
    sent = FakeTelegram()
    monkeypatch.setattr(aw, "minimal_bot", lambda: Bot(FAKE_TOKEN, opener=sent))
    monkeypatch.setitem(aw._MINIMAL, "base_url", "https://dev.minimoi.test")
    monkeypatch.setitem(aw._MINIMAL, "label", "[dev] ")
    return sent


def _boom(*a, **kw):
    raise RuntimeError("synthetic failure ann@x.test Ann Guest")


@pytest.mark.parametrize("where", ["wake", "suppresses", "health", "direct_notice"])
def test_a_fault_anywhere_in_the_service_sends_the_no_name_notice_and_never_the_legacy_one(svc, minimal, monkeypatch,
                                                                                          where, caplog):
    if where == "health":
        monkeypatch.setattr(svc, "health", _boom)              # suppresses() asks health()
    else:
        monkeypatch.setattr(svc, where, _boom)
    monkeypatch.setattr(aw, "_SERVICE", svc)
    legacy = []
    outcome = aw.after_create("guest_access", "W-1" if where != "direct_notice" else None,
                              lambda: legacy.append(("Ann Guest", "ann@x.test")),
                              facts={"requested_at": 1.0}, refs=("pending:",))
    assert outcome == "minimal" and legacy == []
    [body] = minimal.sent
    assert body["text"] == ("[dev] MiniMoi · action waiting (NOT RECORDED in MiniMoi)\n"
                            "Open MiniMoi: https://dev.minimoi.test/guild-next/guild/build")
    assert "parse_mode" not in body
    for personal in ("ann", "Ann", "@", "Guest"):
        assert personal not in json.dumps(body)
    assert "ann@x.test" not in caplog.text                   # the fault is logged by its type only


def test_a_fault_in_the_minimal_sender_itself_never_breaks_the_request(svc, monkeypatch, caplog):
    monkeypatch.setattr(svc, "wake", _boom)
    monkeypatch.setattr(aw, "_SERVICE", svc)
    monkeypatch.setattr(aw, "minimal_bot", _boom)
    legacy = []
    assert aw.after_create("password_reset", None, lambda: legacy.append("name")) == "minimal"
    assert legacy == [] and "minimal notice failed" in caplog.text and "ann@x.test" not in caplog.text


def test_with_the_switch_off_todays_send_is_unchanged(monkeypatch, minimal):
    monkeypatch.setattr(aw, "_SERVICE", None)
    legacy = []
    assert aw.after_create("guest_access", None, lambda: legacy.append(("Ann Guest", "ann@x.test"))) == "legacy"
    assert legacy == [("Ann Guest", "ann@x.test")] and minimal.sent == []


# ---- Codex S1: coverage that was never read, or is stale, is not "complete" ----------------------------------------

def test_coverage_is_unknown_until_read_and_stale_after_two_sweep_periods(svc, clock):
    first = svc.payload()["coverage"]
    assert first["complete"] is False and set(first["unknown"]) == {"guild", "pending", "resets", "decisions"}
    svc.sweep()
    fresh = svc.payload()["coverage"]
    assert fresh["complete"] is True and fresh["unknown"] == [] and fresh["stale"] is False
    clock.advance(3 * svc.settings.sweep_seconds)
    stale = svc.payload()["coverage"]
    assert stale["complete"] is False and stale["stale"] is True


# ---- Codex S2: a truncated decisions read never says "complete" or closes anything ----------------------------------

def test_more_decisions_than_one_read_holds_is_partial_coverage(make_settings, clock, telegram, auth_dir, monkeypatch):
    monkeypatch.setattr(src, "DECISIONS_MAX", 5)
    created = datetime.fromtimestamp(clock(), timezone.utc)

    def row(i):
        return {"id": i, "requester": {"agent": "codex"}, "text": f"Decision number {i} please", "state": "sent",
                "created_at": created, "expires_at": created + timedelta(hours=2)}
    rows = {"value": [row(i) for i in range(1, src.DECISIONS_MAX + 2)]}
    s = build_service(make_settings, clock, telegram, decisions=rows, supervision=True)
    s.sweep()
    assert s.coverage["decisions"] == "truncated" and s.payload()["coverage"]["complete"] is False
    rows["value"] = rows["value"][1:] + [row(src.DECISIONS_MAX + 2)]   # D-1 falls outside a read that is still full
    s.sweep()
    assert "decision:D-1" in {i["key"] for i in _items(s)}         # not closed by a partial read
    s.sender.release()


# ---- independent B3 and S2, Codex B1 and B2: the release's auth-file preflight and final copy ---------------------

SCOPED = ROOT / "scripts" / "operations" / "deploy_scoped_release.sh"
OPS = ROOT / "scripts" / "operations" / "deploy_mc_container_ec2.sh"


def _functions(path, start, end):
    text = path.read_text()
    return text[text.index(start):text.index(end)]


def _bash(script, *args):
    return subprocess.run(["bash", "-c", script, "probe", *map(str, args)], capture_output=True, text=True)


AUTH_FUNCS = ("# ---- portal auth files: begin", "# ---- portal auth files: end")


def _compose(tmp_path, bound=True):
    c = tmp_path / "docker-compose.prod.yml"
    c.write_text("target: /app/minimoi_portal/auth/pending.json\n" if bound else "services: {}\n")
    return c


def test_preflight_refuses_a_portal_release_without_the_host_auth_files(tmp_path):
    funcs = _functions(SCOPED, *AUTH_FUNCS)
    auth_host = tmp_path / "auth"
    auth_host.mkdir()
    (auth_host / "pending.json").write_text('{"pending": []}')
    out = _bash("set -euo pipefail\n" + funcs + '\nportal_auth_preflight "$1" "$2"\n', auth_host, _compose(tmp_path))
    assert out.returncode != 0 and "reset_requests.json" in out.stderr and "notices prepare" in out.stderr
    (auth_host / "reset_requests.json").write_text('{"requests": []}')
    assert _bash("set -euo pipefail\n" + funcs + '\nportal_auth_preflight "$1" "$2"\n', auth_host,
                 _compose(tmp_path)).returncode == 0
    (auth_host / "pending.json").unlink()
    (auth_host / "pending.json").mkdir()                           # a directory a bind once made: refused
    assert _bash("set -euo pipefail\n" + funcs + '\nportal_auth_preflight "$1" "$2"\n', auth_host,
                 _compose(tmp_path)).returncode != 0
    # A compose without the binds (an older release) is not held to it.
    assert _bash("set -euo pipefail\n" + funcs + '\nportal_auth_preflight "$1" "$2"\n', tmp_path / "none",
                 _compose(tmp_path, bound=False)).returncode == 0


def test_the_preflight_runs_before_anything_changes():
    text = SCOPED.read_text()
    assert text.index("portal_auth_preflight \"$AUTH_HOST_DIR\"") < text.index("release_status.py begin")
    assert text.index("portal_auth_final_copy \"$AUTH_HOST_DIR\"") < text.index(
        '"${COMPOSE[@]}" up -d --no-deps --remove-orphans "${SERVICES[@]}"')


FAKE_DOCKER = r'''
docker() {
  case "$1 $2" in
    "inspect --format") case "$3" in *Mounts*) printf '%s\n' "${FAKE_MOUNTS:-}";; *Running*) echo "${FAKE_RUNNING:-true}";; esac; return "${FAKE_INSPECT_RC:-0}";;
    "container inspect") return "${FAKE_INSPECT_RC:-0}";;
    "exec minimoi-portal") return "${FAKE_EXISTS_RC:-0}";;
    "cp minimoi-portal:"*) [ "${FAKE_CP_RC:-0}" = 0 ] || return "$FAKE_CP_RC"; printf '%s' "$FAKE_CONTENT" > "$3"; return 0;;
  esac
  return 1
}
chown() { :; }
chmod() { :; }
'''


def _final_copy(tmp_path, **env):
    funcs = _functions(SCOPED, *AUTH_FUNCS)
    script = "set -euo pipefail\n" + FAKE_DOCKER + funcs + '\nportal_auth_final_copy "$1" "$2"\n'
    return subprocess.run(["bash", "-c", script, "probe", str(tmp_path / "auth"), str(_compose(tmp_path))],
                          capture_output=True, text=True, env={**os.environ, **env})


@pytest.fixture
def host_auth(tmp_path):
    d = tmp_path / "auth"
    d.mkdir()
    (d / "pending.json").write_text('{"pending": [{"token": "from-prepare"}]}')
    (d / "reset_requests.json").write_text('{"requests": []}')
    return d


def test_the_final_copy_takes_the_live_files_just_before_the_recreate(tmp_path, host_auth):
    out = _final_copy(tmp_path, FAKE_CONTENT='{"pending": [{"token": "live"}], "requests": []}')
    assert out.returncode == 0, out.stderr
    assert json.loads((host_auth / "pending.json").read_text())["pending"] == [{"token": "live"}]
    assert any(p.name.startswith("pending.json.bak-release-") for p in host_auth.iterdir())


@pytest.mark.parametrize("env", [{"FAKE_CP_RC": "1"}, {"FAKE_EXISTS_RC": "125"}, {"FAKE_CONTENT": "{broken"},
                                 {"FAKE_CONTENT": '{"pending": "not a list", "requests": 3}'}])
def test_the_final_copy_fails_closed_and_keeps_the_host_files(tmp_path, host_auth, env):
    before = (host_auth / "pending.json").read_text()
    out = _final_copy(tmp_path, FAKE_CONTENT=env.pop("FAKE_CONTENT", '{"pending": [], "requests": []}'), **env)
    assert out.returncode != 0 and "refusing" in out.stdout + out.stderr
    assert (host_auth / "pending.json").read_text() == before


def test_the_final_copy_skips_files_already_bound_or_absent_in_the_container(tmp_path, host_auth):
    before = (host_auth / "pending.json").read_text()
    bound = _final_copy(tmp_path, FAKE_MOUNTS="/app/minimoi_portal/auth/pending.json\n"
                                              "/app/minimoi_portal/auth/reset_requests.json", FAKE_CONTENT="x")
    assert bound.returncode == 0 and (host_auth / "pending.json").read_text() == before
    absent = _final_copy(tmp_path, FAKE_EXISTS_RC="1", FAKE_CONTENT="x")
    assert absent.returncode == 0 and (host_auth / "pending.json").read_text() == before


PREPARE = ("portal_binds() {", "\nnotices() {")


def _prepare(tmp_path, docker_body="return 1"):
    funcs = _functions(OPS, *PREPARE)
    script = (f'set -euo pipefail\nROOT="$1"\nNOTICES_DIR="$ROOT/notices"\nNOTICES_DATA="$ROOT/data"\n'
              f'install() {{ :; }}\nchown() {{ :; }}\nchmod() {{ :; }}\ndocker() {{ {docker_body}; }}\n'
              f'log() {{ echo "$*"; }}\ndie() {{ echo "REFUSED: $*" >&2; exit 2; }}\n' + funcs + "\nnotices_prepare\n")
    return subprocess.run(["bash", "-c", script, "probe", str(tmp_path)], capture_output=True, text=True)


def test_prepare_fails_closed_on_a_copy_error_and_keeps_the_host_file(tmp_path):
    """Codex's probe: an unavailable docker is not proof that the container has no file."""
    (tmp_path / "auth").mkdir()
    (tmp_path / "auth" / "users.json").write_text("{}")
    original = '{"pending": [{"token": "synthetic-request"}]}'
    (tmp_path / "auth" / "pending.json").write_text(original)
    out = _prepare(tmp_path)
    assert out.returncode != 0 and (tmp_path / "auth" / "pending.json").read_text() == original


def test_prepare_never_overwrites_an_existing_host_file_with_an_empty_one(tmp_path):
    (tmp_path / "auth").mkdir()
    (tmp_path / "auth" / "users.json").write_text("{}")
    original = '{"requests": [{"id": "kept"}]}'
    (tmp_path / "auth" / "reset_requests.json").write_text(original)
    docker = ('case "$1 $2" in "inspect --format") case "$3" in *Running*) echo true;; *) :;; esac;; '
              '"exec minimoi-portal") return 1;; "cp minimoi-portal:"*) printf %s "{\\"pending\\": []}" > "$3";; '
              '*) return 1;; esac')
    out = _prepare(tmp_path, docker)
    assert out.returncode == 0, out.stderr
    assert (tmp_path / "auth" / "reset_requests.json").read_text() == original
    assert "answered after this copy" in out.stdout                 # both directions are said (independent S2)


def test_cli_fallback_on_every_unrecorded_refusal_except_a_key_conflict():
    """Independent S1 is tested against the fake server in test_aw_cli.py; this checks the rule's table."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("waiting_cli_rules", ROOT / "scripts" / "agents" / "waiting.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    assert cli.fallback_on_refusal(404) and cli.fallback_on_refusal(401) and cli.fallback_on_refusal(429)
    assert cli.fallback_on_refusal(400) and not cli.fallback_on_refusal(409)


def test_door_reads_a_bounded_stream():
    text = (ROOT / "minimoi_portal" / "action_waiting" / "door.py").read_text()
    assert "request.stream.read(BODY_MAX + 1)" in text and "get_data(" not in text
