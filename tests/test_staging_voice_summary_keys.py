"""The voice summary's two capped gateway keys on dev (Spec 165 §5.4, decision D22 (a)): `voice.sh summary-keys`,
which Robert runs, and the read-only `voice.sh summary-status`. No real credential, no model call: the commands run
against a fake docker here (the route merge runs this interpreter's YAML, as the gateway's own Python would), and a
fake switch-over script stands in for operators/dev-voice-1-2-20261029/deploy.sh.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from test_staging_environment import GATEWAY_STAGING, SCRIPTS, _write_exe, shell  # noqa: F401  (shell is a fixture)

REPO = Path(__file__).resolve().parent.parent
VOICE = SCRIPTS / "voice.sh"
MC_KEYS = SCRIPTS / "mc_keys.sh"
ALIAS = "minimoi-voice-summary"
MC_VAR, COS_VAR = "MINIMOI_GATEWAY_KEY_VOICE_SUMMARY_MC", "MINIMOI_GATEWAY_KEY_VOICE_SUMMARY_COS"


def _keys(fn, *args):
    return subprocess.run(["bash", "-c", f'source "{MC_KEYS}"; {fn} "$@"', "keys", *args], capture_output=True, text=True)


# ── the key's scope and the names the client reads ───────────────────────────

@pytest.mark.parametrize("host", ["mc", "cos"])
def test_summary_key_is_the_alias_only_chat_only_monthly_and_rate_limited(host):
    py = _keys("voice_summary_keygen_py", host).stdout
    assert '"models": ["minimoi-voice-summary"]' in py
    assert '"allowed_routes": ["/v1/chat/completions", "/chat/completions"]' in py
    assert "/key/info" not in py and "/v1/responses" not in py
    assert '"budget_duration": "30d"' in py and '"rpm_limit": 10' in py and '"max_budget": cap' in py
    assert f'"voice-summary-{host}-" + os.urandom(3).hex()' in py and f'"role": "voice.summary.{host}"' in py
    assert 'os.environ["VOICE_CAP"]' in py and 'os.environ.get("VOICE_OLD_KEY", "")' in py
    assert py.index('call("/key/generate"') < py.index('call("/key/delete"')      # the old key goes after the new exists
    assert _keys("voice_summary_keygen_py", "portal").returncode == 64


def test_the_names_match_the_client_and_the_repo_route():
    from core.realtime_voice.summary_gateway import SUMMARY_MODEL, key_env
    assert SUMMARY_MODEL == ALIAS
    assert key_env("voice-summary-mc") == MC_VAR and key_env("voice-summary-cos") == COS_VAR
    route = {r["model_name"]: r for r in yaml.safe_load(GATEWAY_STAGING.read_text())["model_list"]}[ALIAS]
    assert route["litellm_params"]["api_key"] == "os.environ/GATEWAY_XAI_API_KEY"
    text = VOICE.read_text()
    assert f"echo {MC_VAR}" in text and f"echo {COS_VAR}" in text and f"VOICE_ALIAS={ALIAS}" in text


def test_voice_sh_sets_umask_first_and_writes_a_key_only_into_its_file():
    assert os.access(VOICE, os.X_OK)                          # Robert runs it by path, like cos.sh
    text = VOICE.read_text()
    body = text.split("\nsummary_keys() {\n", 1)[1]
    assert body.index("umask 077") < body.index("ensure_route") < body.index("make_key mc")
    make = text.split("\nmake_key() {\n", 1)[1].split("\n}\n", 1)[0]
    uses = [line.strip() for line in make.splitlines() if "$key" in line]
    assert uses == ['[[ "$key" == sk-* ]] || die "the gateway\'s answer for $who was not a key (nothing written for it)"',
                    '{ grep -v "^$var=" "$file" || true; printf \'%s=%s\\n\' "$var" "$key"; } > "$tmp"'], uses
    assert "set -x" not in text and "export VOICE" not in text


# ── voice.sh against a fake docker ────────────────────────────────────────────

FAKE_DOCKER = r"""#!/bin/sh
# Fake docker for voice.sh: every call's argv goes to $FAKE/argv.log. The gateway's
# /app/config.yaml is $FAKE/app-config.yaml: refreshed from the live file on restart.
printf '%s\n' "$*" >> "$FAKE/argv.log"
CFG="$STAGING_ROOT/config/litellm.staging.yaml"
case "$*" in
  "inspect -f {{.State.Running}} "*) echo true; exit 0 ;;
  "inspect -f {{if .State.Health}}"*) echo healthy; exit 0 ;;
  "inspect -f {{range .Config.Env}}"*) [ -f "$FAKE/deployed" ] && echo set; exit 0 ;;
  "exec -i -e VOICE_LIVE_CFG -e VOICE_ROUTE_SRC minimoi-model-gateway python -")
    echo route >> "$FAKE/route.calls"; exec "$FAKE_PY" - ;;
  "exec -i -e VOICE_OLD_KEY -e VOICE_CAP minimoi-model-gateway python -")
    cat >> "$FAKE/keygen.stdin"
    n=$(( $(cat "$FAKE/keygen.count" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$FAKE/keygen.count"
    printf 'cap=%s old=%s\n' "$VOICE_CAP" "$VOICE_OLD_KEY" >> "$FAKE/keygen.env"
    printf 'sk-fake-voice-key-%s-0123456789\nab%s%s\n' "$n" "$n" "$n"; exit 0 ;;
  "restart --time 20 minimoi-model-gateway") cp "$CFG" "$FAKE/app-config.yaml"; echo minimoi-model-gateway; exit 0 ;;
  "exec minimoi-model-gateway sha256sum /app/config.yaml")
    if [ -n "$FAKE_SHA_BAD" ]; then echo "0000  /app/config.yaml"; exit 0; fi
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$FAKE/app-config.yaml"; else shasum -a 256 "$FAKE/app-config.yaml"; fi
    exit 0 ;;
  "exec minimoi-model-gateway grep -Eq "*) grep -Eq "$5" "$FAKE/app-config.yaml"; exit $? ;;
esac
exit 0
"""

# The live deploy.sh writes these (bash-escaped in its unquoted heredoc).
FAKE_DEPLOY = r"""#!/bin/sh
# {"MINIMOI_GATEWAY_KEY_VOICE_SUMMARY_MC": "\${MINIMOI_GATEWAY_KEY_VOICE_SUMMARY_MC:-}"}
# {"MINIMOI_GATEWAY_KEY_VOICE_SUMMARY_COS": "\${MINIMOI_GATEWAY_KEY_VOICE_SUMMARY_COS:-}"}
echo deployed >> "$FAKE/deploy.log"
env > "$FAKE/deploy.env"
touch "$FAKE/deployed"
"""


def _live_style(text):
    """The Mac's live config as the d25 route operator left it: yaml.safe_dump, no comments, routes at column 0."""
    data = yaml.safe_load(text)
    data["model_list"] = [r for r in data["model_list"] if r["model_name"] != ALIAS]
    return yaml.safe_dump(data, sort_keys=False)


def _repo_without_block(text):
    """The repo file's own style, with the route's block (its comment lines and the blank line after) taken out."""
    lines = text.splitlines(True)
    at = next(i for i, l in enumerate(lines) if l.strip() == f"- model_name: {ALIAS}")
    start = at
    while lines[start - 1].lstrip().startswith("#"):
        start -= 1
    end = at + 1
    while lines[end].strip():
        end += 1
    return "".join(lines[:start] + lines[end + 1:])


def _world(tmp_path, *, config=None, mc_env="MC_MODEL_GATEWAY_KEY=sk-mc-agent-key\n", cos_env="COS_MODEL_GATEWAY_KEY=sk-cos-key\n",
           keys_on=True, deploy=FAKE_DEPLOY):
    root, fake, bin_dir = tmp_path / "root", tmp_path / "fake", tmp_path / "bin"
    (root / "state").mkdir(parents=True)
    (root / "config").mkdir()
    fake.mkdir()
    _write_exe(bin_dir / "docker", FAKE_DOCKER)
    deploy_path = _write_exe(tmp_path / "ops" / "deploy.sh", deploy)
    repo_text = GATEWAY_STAGING.read_text()
    cfg = root / "config" / "litellm.staging.yaml"
    cfg.write_text(_live_style(repo_text) if config is None else config)
    cfg.chmod(0o644)
    (fake / "app-config.yaml").write_text(cfg.read_text())
    (root / ".env").write_text("MINIMOI_MODEL_GATEWAY_KEY='sk-master-key'\n")
    (root / ".env").chmod(0o600)
    for name, text in (("mc.env", mc_env), ("cos.env", cos_env)):
        (root / name).write_text(text)
        (root / name).chmod(0o600)
    if keys_on:
        (root / "state" / "gateway.keys").write_text("on\n")
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE": str(fake), "FAKE_PY": sys.executable,
           "STAGING_ROOT": str(root), "STAGING_RELEASE_DIR": str(tmp_path / "release"),
           "STAGING_VOICE_DEPLOY": str(deploy_path)}
    for name in (MC_VAR, COS_VAR, "VOICE_CAP", "VOICE_OLD_KEY", "FAKE_SHA_BAD", "STAGING_PORTAL_RELEASE_DIR",
                 "STAGING_VOICE_ROUTE_SOURCE"):
        env.pop(name, None)
    return env, fake, root


def _voice(shell, env, *args, stdin=""):
    return subprocess.run([shell, str(VOICE), *args], env=env, input=stdin, capture_output=True, text=True)


def _read(path):
    return path.read_text() if path.exists() else ""


def _backups(root):
    return sorted((root / "config").glob("litellm.staging.yaml.before-voice-summary.*"))


def test_summary_keys_adds_the_route_writes_both_keys_unprinted_and_redeploys(tmp_path, shell):
    env, fake, root = _world(tmp_path)
    cfg = root / "config" / "litellm.staging.yaml"
    before = cfg.read_text()
    result = _voice(shell, env, "summary-keys", stdin="7\n\n")
    assert result.returncode == 0, result.stderr
    out = result.stdout + result.stderr
    # The keys: written unprinted, 600, one variable per host's own file.
    assert f"{MC_VAR}=sk-fake-voice-key-1-0123456789" in (root / "mc.env").read_text()
    assert f"{COS_VAR}=sk-fake-voice-key-2-0123456789" in (root / "cos.env").read_text()
    assert COS_VAR not in (root / "mc.env").read_text() and MC_VAR not in (root / "cos.env").read_text()
    assert "MC_MODEL_GATEWAY_KEY=sk-mc-agent-key" in (root / "mc.env").read_text()       # nothing else lost
    for name in ("mc.env", "cos.env"):
        assert oct((root / name).stat().st_mode & 0o777) == "0o600", name
    assert "sk-fake-voice-key" not in out and "sk-master-key" not in out and "sk-mc-agent-key" not in out
    assert "Master Craftsman's voice summary key (voice-summary-mc): monthly cap $7 (30d)" in out and "…ab11" in out
    assert "Chief of Staff's voice summary key (voice-summary-cos): monthly cap $5 (30d)" in out and "…ab22" in out
    assert '["minimoi-voice-summary"]' in out and "rpm 10" in out
    assert _read(fake / "keygen.env").splitlines() == ["cap=7 old=", "cap=5 old="]
    # The route: exactly the repo's, the rest of the live config unchanged; a backup of the file before.
    assert yaml.safe_load(cfg.read_text()) == yaml.safe_load(GATEWAY_STAGING.read_text())
    assert oct(cfg.stat().st_mode & 0o777) == "0o644"
    backups = _backups(root)
    assert len(backups) == 1 and backups[0].read_text() == before
    # Only the gateway restarts; nothing goes through compose; the switch-over runs once.
    argv = _read(fake / "argv.log").splitlines()
    assert [l for l in argv if l.startswith("restart")] == ["restart --time 20 minimoi-model-gateway"]
    assert not [l for l in argv if l.startswith(("compose", "stop", "rm", "kill"))]
    assert _read(fake / "deploy.log") == "deployed\n"
    deploy_env = _read(fake / "deploy.env")                     # the keys reach deploy.sh only through the env files
    assert "sk-fake-voice-key" not in deploy_env and MC_VAR not in deploy_env and COS_VAR not in deploy_env
    assert "sk-fake-voice-key" not in "\n".join(argv)


def test_summary_keys_keeps_the_repo_style_text_exactly(tmp_path, shell):
    repo_text = GATEWAY_STAGING.read_text()
    env, fake, root = _world(tmp_path, config=_repo_without_block(repo_text))
    result = _voice(shell, env, "summary-keys", stdin="\n\n")
    assert result.returncode == 0, result.stderr
    assert (root / "config" / "litellm.staging.yaml").read_text() == repo_text


def test_summary_keys_leaves_a_config_that_already_routes_the_alias(tmp_path, shell):
    env, fake, root = _world(tmp_path, config=GATEWAY_STAGING.read_text())
    result = _voice(shell, env, "summary-keys", stdin="\n\n")
    assert result.returncode == 0, result.stderr
    assert "already routes minimoi-voice-summary (unchanged" in result.stdout
    assert not (fake / "route.calls").exists() and not _backups(root)
    assert "restart" not in _read(fake / "argv.log")
    assert _read(fake / "keygen.env").splitlines() == ["cap=5 old=", "cap=5 old="]


def test_summary_keys_asks_before_rotating_and_hands_the_old_key_over_by_env(tmp_path, shell):
    env, fake, root = _world(tmp_path, config=GATEWAY_STAGING.read_text(),
                             mc_env=f"MC_MODEL_GATEWAY_KEY=sk-mc-agent-key\n{MC_VAR}=sk-old-mc-999\n",
                             cos_env=f"COS_MODEL_GATEWAY_KEY=sk-cos-key\n{COS_VAR}=sk-old-cos-888\n")
    (fake / "deployed").touch()                                 # the containers already have their keys
    kept = _voice(shell, env, "summary-keys", stdin="n\n\n")
    assert kept.returncode == 0, kept.stderr
    assert kept.stdout.count("keeping") == 2 and not (fake / "keygen.env").exists()
    assert not (fake / "deploy.log").exists() and "not recreated" in kept.stdout
    rotated = _voice(shell, env, "summary-keys", stdin="y\n9\nn\n")
    assert rotated.returncode == 0, rotated.stderr
    assert _read(fake / "keygen.env").splitlines() == ["cap=9 old=sk-old-mc-999"]         # handed over by env...
    assert "sk-old-mc-999" not in _read(fake / "argv.log")                                  # ...never in argv
    mc = (root / "mc.env").read_text()
    assert "sk-old-mc-999" not in mc and mc.count(f"{MC_VAR}=") == 1
    assert f"{COS_VAR}=sk-old-cos-888" in (root / "cos.env").read_text()
    assert _read(fake / "deploy.log") == "deployed\n"
    assert "sk-old" not in rotated.stdout + rotated.stderr


@pytest.mark.parametrize("stdin", ["lots\n", "7\n0\n", "7\n501\n"])
def test_summary_keys_refuses_a_bad_cap_before_anything_changes(tmp_path, shell, stdin):
    env, fake, root = _world(tmp_path)
    cfg = root / "config" / "litellm.staging.yaml"
    before = cfg.read_text()
    result = _voice(shell, env, "summary-keys", stdin=stdin)
    assert result.returncode != 0 and "is not a dollar amount" in result.stderr and "(nothing changed)" in result.stderr
    assert cfg.read_text() == before and not _backups(root)
    assert not (fake / "keygen.env").exists() and not (fake / "deploy.log").exists()
    assert "restart" not in _read(fake / "argv.log")


def test_summary_keys_puts_the_backup_back_when_the_gateway_does_not_read_the_new_file(tmp_path, shell):
    env, fake, root = _world(tmp_path)
    cfg = root / "config" / "litellm.staging.yaml"
    before = cfg.read_text()
    result = _voice(shell, {**env, "FAKE_SHA_BAD": "1"}, "summary-keys", stdin="\n\n")
    assert result.returncode != 0 and "put the backup back" in result.stderr and "no key was made" in result.stderr
    assert cfg.read_text() == before
    assert [l for l in _read(fake / "argv.log").splitlines() if l.startswith("restart")] == ["restart --time 20 minimoi-model-gateway"] * 2
    assert not (fake / "keygen.env").exists() and not (fake / "deploy.log").exists()


def test_summary_keys_needs_the_key_database_a_deploy_that_passes_the_keys_and_no_key_in_dot_env(tmp_path, shell):
    env, fake, _ = _world(tmp_path / "a", keys_on=False)
    result = _voice(shell, env, "summary-keys", stdin="\n\n")
    assert result.returncode != 0 and "run mc.sh gateway-keys first" in result.stderr
    env, fake, _ = _world(tmp_path / "b", deploy="#!/bin/sh\ntouch \"$FAKE/deployed\"\n")
    result = _voice(shell, env, "summary-keys", stdin="\n\n")
    assert result.returncode != 0 and f"does not pass {MC_VAR} to minimoi-portal" in result.stderr
    assert not (fake / "keygen.env").exists()
    env, fake, root = _world(tmp_path / "c")
    (root / ".env").write_text(f"MINIMOI_MODEL_GATEWAY_KEY='sk-master-key'\n{COS_VAR}=x\n")
    result = _voice(shell, env, "summary-keys", stdin="\n\n")
    assert result.returncode != 0 and f"{COS_VAR} is in .env" in result.stderr and "(nothing changed)" in result.stderr
    assert not (fake / "keygen.env").exists()


def test_summary_status_reports_presence_only_and_changes_nothing(tmp_path, shell):
    env, fake, root = _world(tmp_path, mc_env=f"{MC_VAR}=sk-secret-mc-1234\n")
    files = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    result = _voice(shell, env, "summary-status")
    assert result.returncode == 0, result.stderr
    out = result.stdout + result.stderr
    assert "sk-" not in out
    assert f"{MC_VAR}: present in mc.env (mode 600)" in out and f"{COS_VAR}: absent from cos.env" in out
    assert "live gateway config: no minimoi-voice-summary route" in out
    assert "running gateway: healthy, no minimoi-voice-summary" in out
    assert f"minimoi-portal: {MC_VAR} not set" in out and f"minimoi-cos-scheduler: {COS_VAR} not set" in out
    argv = _read(fake / "argv.log")
    assert "exec -i" not in argv and "restart" not in argv and "compose" not in argv      # read-only
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == files
    (fake / "deployed").touch()
    (root / "config" / "litellm.staging.yaml").write_text(GATEWAY_STAGING.read_text())
    out = _voice(shell, env, "summary-status").stdout
    assert "live gateway config: routes minimoi-voice-summary" in out
    assert "running gateway: healthy, no minimoi-voice-summary (restart it" in out          # not restarted yet
    assert f"minimoi-portal: {MC_VAR} set" in out
