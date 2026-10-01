"""The Operations agent on this Mac's Docker-staging topology (MINIMOI_ROLE=
standby): its database URL from the Keychain (never printed), the staging
services it watches (focus-aware), no restarts and no Telegram in standby.
Nothing here touches a real database, launchd, Telegram or the network."""
from __future__ import annotations

import importlib
import sys
from datetime import datetime, timedelta

import pytest

MOD = "domains.guild.agents.operations"
SECRET_DSN = "postgresql://kc-user:kc-secret-pass-123@127.0.0.1:1/personal_agents"


def _unload(before: set):
    for name in set(sys.modules) - before:
        sys.modules.pop(name, None)
        parent, _, child = name.rpartition(".")
        if parent in sys.modules and getattr(sys.modules[parent], child, None) is not None:
            try:
                delattr(sys.modules[parent], child)
            except AttributeError:
                pass


@pytest.fixture
def load(monkeypatch, tmp_path):
    """Import the agent fresh under a given role and Keychain, then unload it."""
    before = set(sys.modules)
    monkeypatch.setenv("STAGING_ROOT", str(tmp_path / "staging"))

    def _load(*, role="standby", keychain=SECRET_DSN, env_dsn=None):
        monkeypatch.setenv("MINIMOI_ROLE", role)
        if env_dsn is None:
            monkeypatch.delenv("DATABASE_URL", raising=False)
        else:
            monkeypatch.setenv("DATABASE_URL", env_dsn)
        import core.get_secret as gs
        seen = []

        def from_keyring(service, account):
            seen.append((service, account))
            return keychain if (service, account) == ("minimoi-dev-db", "database_url") else None
        monkeypatch.setattr(gs, "_from_keyring", from_keyring)
        monkeypatch.setattr(gs, "_from_ssm", lambda key: (_ for _ in ()).throw(RuntimeError("no SSM in tests")))
        sys.modules.pop(MOD, None)
        mod = importlib.import_module(MOD)
        return mod, seen
    yield _load
    _unload(before)


# ── the database URL ──────────────────────────────────────────────────────────

def test_standby_starts_with_the_keychain_dsn_and_never_prints_it(load, monkeypatch, capsys, caplog):
    mod, seen = load()
    assert mod.DSN == SECRET_DSN and seen == [("minimoi-dev-db", "database_url")]
    calls = []
    monkeypatch.setattr(mod, "_ensure_agent_state_constraint", lambda: calls.append("constraint"))
    monkeypatch.setattr(mod, "_db_update_state", lambda state, last=None: calls.append(state))
    mod.main(serve=False, loops=False)
    out = capsys.readouterr()
    assert calls == ["constraint", "starting", "running"] and mod._state["state"] == "running"
    for leak in (SECRET_DSN, "kc-secret-pass-123", "kc-user"):
        assert leak not in out.out + out.err + caplog.text
    assert "Starting as standby" in out.out and "Watching: portal, curator, german, portuguese" in out.out


def test_an_environment_dsn_still_wins(load):
    mod, seen = load(env_dsn="postgresql://env@127.0.0.1:1/x")
    assert mod.DSN == "postgresql://env@127.0.0.1:1/x" and seen == []


@pytest.mark.parametrize("role,keychain", [("standby", None), ("production", SECRET_DSN)],
                         ids=["standby-no-keychain-item", "production-no-ssm"])
def test_a_missing_dsn_fails_clearly_and_says_nothing_secret(load, role, keychain):
    with pytest.raises(RuntimeError) as err:
        load(role=role, keychain=keychain)
    text = str(err.value)
    assert "DATABASE_URL is not available" in text and "minimoi-dev-db/database_url" in text
    assert "kc-secret-pass-123" not in text


# ── what it watches ───────────────────────────────────────────────────────────

def _focus(tmp_path, text):
    path = tmp_path / "staging" / "state" / "focus.stopped"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_it_watches_the_staging_services_and_skips_the_ones_focus_keeps_stopped(load, tmp_path):
    mod, _ = load()
    assert [n for n, _ in mod._watched_services()] == ["portal", "curator", "german", "portuguese"]
    _focus(tmp_path, "curator german cos-agent-a portal\n")      # the portal is watched even if listed
    assert [n for n, _ in mod._watched_services()] == ["portal", "portuguese"]
    assert dict(mod.WATCHED_SERVICES)["portuguese"] == "http://localhost:8770/"
    assert not any(label in dict(mod.WATCHED_SERVICES) for label in mod.RETIRED_LABELS)


def test_a_stopped_service_stays_quiet_and_a_down_one_still_escalates(load, tmp_path, monkeypatch):
    mod, _ = load()
    _focus(tmp_path, "curator german\n")
    escalations = []
    monkeypatch.setattr(mod, "_db_escalate", lambda tier, text, *a: escalations.append((tier, text)))
    monkeypatch.setattr(mod, "_db_log", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_send_telegram", lambda text: None)
    monkeypatch.setattr(mod, "RULES", {"service_down_threshold_minutes": 15})
    up = {"http://localhost:5001/"}

    class Resp:
        status_code = 200

    def get(url, timeout=5):
        if url in up:
            return Resp()
        raise ConnectionError("refused")
    monkeypatch.setattr(mod.requests, "get", get)
    mod._outage_start.clear()
    mod._outage_start["curator"] = datetime.now() - timedelta(hours=1)      # stopped on purpose since then
    mod._outage_start["portuguese"] = datetime.now() - timedelta(minutes=20)
    results = mod._check_services()
    assert [(n, ok) for n, ok, _ in results] == [("portal", True), ("portuguese", False)]
    assert "curator" not in mod._outage_start                               # its outage clock stopped
    assert [t for t, _ in escalations] == [4] and "portuguese down" in escalations[0][1]


# ── restarts and Telegram in standby ──────────────────────────────────────────

def test_standby_restarts_nothing_and_never_runs_launchctl(load, monkeypatch):
    mod, _ = load()
    logged = []
    monkeypatch.setattr(mod, "_db_log", lambda action, tier, outcome, *a, **k: logged.append(action))
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: pytest.fail("launchctl must not run"))
    assert mod._action_restart_service("com.user.curator-server") is False
    assert logged == ["restart_skipped_standby"]


def test_production_never_restarts_a_retired_unknown_or_disabled_label(load, monkeypatch):
    mod, _ = load(role="production", env_dsn="postgresql://env@127.0.0.1:1/x")
    logged, ran = [], []
    monkeypatch.setattr(mod, "_db_log", lambda action, tier, outcome, *a, **k: logged.append(action))
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)

    class Out:
        stdout = '\t"com.example.live" => enabled\n\t"com.example.off" => disabled\n'
        returncode = 0

    def run(cmd, **kw):
        ran.append(cmd[:2])
        return Out()
    monkeypatch.setattr(mod.subprocess, "run", run)
    for label in (*mod.RETIRED_LABELS, "com.example.unknown"):
        assert mod._action_restart_service(label) is False
    assert ran == []                                                        # no launchctl at all for those
    monkeypatch.setattr(mod, "RESTARTABLE_LABELS", ("com.example.live", "com.example.off"))
    assert mod._action_restart_service("com.example.off") is False
    assert ["launchctl", "stop"] not in ran
    assert mod._action_restart_service("com.example.live") is True
    assert ["launchctl", "stop"] in ran and ["launchctl", "start"] in ran


def test_telegram_stays_suppressed_in_standby(load, monkeypatch):
    mod, _ = load()
    logged = []
    monkeypatch.setattr(mod, "_log_file", lambda event, detail: logged.append(event))
    monkeypatch.setattr(mod.requests, "post", lambda *a, **k: pytest.fail("nothing is sent in standby"))
    mod._send_telegram("TIER 4 test")
    assert logged == ["telegram_suppressed_standby"]


def test_the_staging_portal_reads_the_macs_operations_agent():
    from pathlib import Path
    compose = (Path(__file__).resolve().parents[2] / "docker-compose.staging.yml").read_text()
    assert "GUILD_OPERATIONS_STATUS_URL=${GUILD_OPERATIONS_STATUS_URL:-http://host.docker.internal:8768/status}" in compose


# ── the DSN never reaches a log (#280 review) ─────────────────────────────────

def test_a_malformed_dsn_fails_at_start_without_echoing_its_password(load):
    bad = "postgresql://kc-user:pa%zz-secret@localhost:5432/db"      # '%zz' is not a percent escape
    with pytest.raises(RuntimeError) as err:
        load(keychain=bad)
    text = str(err.value) + repr(err.value.__cause__) + repr(err.value.__context__)
    assert "pa%zz-secret" not in text and "zz-secret" not in text and "could not be parsed" in str(err.value)


def test_a_connect_error_that_quotes_the_dsn_is_scrubbed_from_the_log(load, monkeypatch, tmp_path):
    mod, _ = load()
    monkeypatch.setattr(mod, "LOGS_DIR", tmp_path)

    def connect(dsn, **kw):
        raise mod.psycopg2.OperationalError(f'connection failed for "{dsn}" password "kc-secret-pass-123"')
    monkeypatch.setattr(mod.psycopg2, "connect", connect)
    mod._db_log("probe", 1, "outcome")                     # logs its own failure through _log_file
    mod._log_file("direct", f"{SECRET_DSN} kc-secret-pass-123")
    log = (tmp_path / "operations.log").read_text()
    assert "kc-secret-pass-123" not in log and SECRET_DSN not in log
    assert "database connect failed: OperationalError" in log and "***" in log
