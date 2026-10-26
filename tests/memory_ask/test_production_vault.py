"""Production Vault wiring (owner decisions, 17 Oct 2026): MC asks the full Vault on on-record turns through an
internal-only container, with month windows for questions like "What were we doing in May?". No network, no real data."""
import json
from datetime import date
from pathlib import Path

import pytest
import yaml

from core.memory_ask import consumer, service

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "t" * 43


def cfg(url, **kw):
    return consumer.Config(url, TOKEN, **kw)


# ── where the hook may connect ────────────────────────────────────────────────

@pytest.mark.parametrize("url,ok", [
    ("http://vault-ask:8771", True),                       # the named container on the internal network
    ("https://vault-ask:8771", True),
    ("http://vault-ask.example.com:8771", False),          # dotted: not a container name
    ("http://10.0.0.5:8771", False),                       # an IP is never an internal name
    ("http://2130706433:8771", False),                     # all digits can resolve as an IP
    ("http://0x7f000001:8771", False),                     # a hex number resolves as an IP too
    ("http://other:8771", False),                          # not listed
    ("http://user:pw@vault-ask:8771", False),              # embedded credentials
])
def test_internal_host_allowance_is_exact_and_narrow(url, ok):
    assert (consumer.endpoint(cfg(url, internal_hosts=("vault-ask",))) is not None) is ok


def test_existing_rules_are_unchanged():
    assert consumer.endpoint(cfg("http://127.0.0.1:8771")) is not None                       # loopback
    assert consumer.endpoint(cfg("http://vault-ask:8771")) is None                           # nothing listed: refused
    assert consumer.endpoint(cfg("http://h.example:1", allow_hosts=("h.example",))) is None  # allow-listed needs https
    assert consumer.endpoint(cfg("https://h.example:1", allow_hosts=("h.example",))) is not None


def test_settings_read_from_the_environment():
    c = consumer.Config.from_env({"MINIMOI_VAULT_ASK_URL": "http://vault-ask:8771", "MINIMOI_VAULT_ASK_TOKEN": TOKEN,
                                  "MINIMOI_VAULT_ASK_INTERNAL_HOSTS": "vault-ask", "MINIMOI_VAULT_ASK_INCLUDE_RAW": "1"})
    assert c.internal_hosts == ("vault-ask",) and c.include_raw is True
    assert consumer.Config.from_env({"MINIMOI_VAULT_ASK_INCLUDE_RAW": "yes"}).include_raw is False   # only "1"


# ── what the hook sends ───────────────────────────────────────────────────────

def sent(config, query="what did we decide about the gateway"):
    seen = {}

    def post(url, headers, body, timeout):
        seen["url"], seen["body"] = url, json.loads(body)
        return 200, json.dumps({"keyword": [], "meaning": [], "status": {}}).encode()
    consumer.retrieve(query, config, post=post)
    return seen


def test_raw_tier_is_asked_only_when_the_owner_setting_is_on():
    assert "include_raw" not in sent(cfg("http://127.0.0.1:8771"))["body"]
    assert sent(cfg("http://vault-ask:8771", internal_hosts=("vault-ask",), include_raw=True))["body"]["include_raw"] is True


def test_a_named_month_becomes_a_date_window_with_an_exclusive_end():
    body = sent(cfg("http://127.0.0.1:8771"), "What were we doing in May?")["body"]
    year = date.today().year if date.today().month >= 5 else date.today().year - 1
    assert (body["created_after"], body["created_before"]) == (f"{year}-05-01", f"{year}-06-01")   # 31 May is inside
    assert "created_after" not in sent(cfg("http://127.0.0.1:8771"), "I may need the gateway notes")["body"]
    since = sent(cfg("http://127.0.0.1:8771"), "what changed since March")["body"]
    assert since["created_after"].endswith("-03-01") and "created_before" not in since


@pytest.mark.parametrize("text,window", [
    ("What were we doing in May?", ("2026-05-01", "2026-06-01")),
    ("im Mai 2026", ("2026-05-01", "2026-06-01")),
    ("in Oktober", ("2026-10-01", "2026-11-01")),
    ("during December", ("2025-12-01", "2026-01-01")),         # a month after today: last year's, across the year end
    ("September 2025 notes", ("2025-09-01", "2025-10-01")),
    ("what about 2026-03", ("2026-03-01", "2026-04-01")),
    ("in Sep or Oct", ("2026-09-01", "2026-11-01")),             # both months, not just the first
    ("from March to May", ("2026-03-01", "2026-06-01")),
    ("since March", ("2026-03-01", None)), ("seit März", ("2026-03-01", None)),   # open-ended to now
    ("Any word from Jan?", None),                                # "from <word>" alone is as often a person
    ("I may need help", None), ("nothing here", None), ("in November 2026", None),   # future month: no window
])
def test_date_window(text, window):
    assert consumer.date_window(text, date(2026, 10, 17)) == window


def test_the_window_end_matches_the_services_exclusive_bound():
    from core.memory_index import search
    assert "exclusive" in search._window.__doc__ and search._window("2026-05-01", "2026-06-01")[0].count("<") == 1


# ── the service and the Compose wiring ────────────────────────────────────────

def test_service_writes_its_query_log_where_told(monkeypatch, tmp_path):
    seen = {}

    class App:
        def run(self, **kw):
            seen["run"] = kw
    monkeypatch.setattr(service, "create_app", lambda **kw: seen.setdefault("app", kw) and App())
    service.main(["--shelf-root", str(tmp_path / "s"), "--index", str(tmp_path / "i.sqlite"),
                  "--registry", str(tmp_path / "a.json"), "--log", str(tmp_path / "log" / "ask-log.jsonl")])
    assert seen["app"]["log_path"] == tmp_path / "log" / "ask-log.jsonl"


def test_compose_vault_service_is_profile_gated_internal_and_read_only():
    spec = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text())
    vault = spec["services"]["vault-ask"]
    assert vault["profiles"] == ["vault"] and "ports" not in vault and vault["networks"] == ["vault-internal"]
    assert vault["read_only"] is True and vault["cap_drop"] == ["ALL"]
    data = next(v for v in vault["volumes"] if v["target"] == "/vault")
    assert data["read_only"] is True and data["bind"] == {"create_host_path": False}
    log = next(v for v in vault["volumes"] if v["target"] != "/vault")
    assert not log["target"].startswith("/vault/") and log.get("read_only") is not True     # writable, outside the RO tree
    assert "MOI_OLLAMA_URL=http://127.0.0.1:9" in vault["environment"]
    assert spec["networks"]["vault-internal"] == {"internal": True}
    on_network = [n for n, s in spec["services"].items() if "vault-internal" in (s.get("networks") or [])]
    assert sorted(on_network) == ["cos-scheduler", "vault-ask"]
    env = spec["services"]["cos-scheduler"]["environment"]
    assert "MINIMOI_VAULT_ASK_URL=${MINIMOI_VAULT_ASK_URL:-}" in env
    assert not any(e.startswith("MINIMOI_VAULT_ASK_TOKEN=") for e in env)
    assert spec["services"]["cos-scheduler"]["env_file"] == [
        "${MINIMOI_ROOT:-/opt/minimoi}/.env",
        "${MINIMOI_ROOT:-/opt/minimoi}/${MINIMOI_VAULT_READER_ENV_FILE:-.env}",
    ]
    assert "MINIMOI_VAULT_ASK_INCLUDE_RAW=${MINIMOI_VAULT_ASK_INCLUDE_RAW:-0}" in env
    # Guild only by default; Robert's Confer conversation joins only by the host switch (cos_prod.sh vault on).
    assert "MINIMOI_VAULT_ASK_CONVERSATIONS=${MINIMOI_COS_VAULT_CONVERSATIONS:-guild-}" in env


def test_scoped_portal_release_recreates_vault_ask_only_once_it_exists():
    text = (ROOT / "scripts/operations/deploy_scoped_release.sh").read_text()
    guard = 'docker container inspect minimoi-vault-ask >/dev/null 2>&1'
    assert guard in text and '--profile vault up -d --no-deps vault-ask' in text
    assert text.index('up -d --no-deps --remove-orphans "${SERVICES[@]}"') < text.index(guard)
    from scripts.ci.classify_release import ALL_SERVICES
    assert "vault-ask" not in ALL_SERVICES            # never named in a release before activation
    # Once recreated it is verified before the prune: image, running, health (Codex finding 3, 17 Oct).
    check = text.index('if [[ "$VAULT_RECREATED" == "1" ]]')
    assert text.index('expected_image portal', check) < text.index('docker image prune -af')
    assert text.index('wait_for_container_health minimoi-vault-ask', check) < text.index('docker image prune -af')
