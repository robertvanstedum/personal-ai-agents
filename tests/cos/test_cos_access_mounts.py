"""CoS's wide test-phase access never reaches a credential (Robert, 26 Oct 2026; #215).

Read everything, write in its own folders and Planning Studio — but no .env, mc.env, MC's portal secrets, auth files,
private stores, backups, SSH keys or the Docker socket; no writable copy of scripts root runs; and with CoS's own key,
no master key in its environment.
"""
import re
import subprocess
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
COMPOSE = yaml.safe_load((REPO / "docker-compose.prod.yml").read_text())
FORBIDDEN = re.compile(r"(^|/)(\.env[^/]*|mc\.env|gateway-keys\.env|vault-reader\.env|auth|private|backups|operators|"
                       r"\.ssh|docker\.sock|portal|vault[^/]*)$")


def _mounts(service):
    out = []
    for v in COMPOSE["services"][service]["volumes"]:
        if isinstance(v, str):
            parts = v.replace("${MINIMOI_ROOT:-/opt/minimoi}", "/opt/minimoi").split(":")
            out.append((parts[0], parts[1],
                        len(parts) > 2 and parts[2] == "ro"))
    return out


def test_cos_agent_mounts_no_credential_source():
    sources = [s for s, _, _ in _mounts("cos-agent-a") if s.startswith("/")]
    assert "/opt/minimoi" not in sources
    for s in sources:
        assert not FORBIDDEN.search(s), s


def test_cos_agent_folders_and_modes():
    m = {target: (source, ro) for source, target, ro in _mounts("cos-agent-a")}
    assert m["/minimoi/scripts"][1] is True                 # root runs these: never writable by an agent
    assert m["/minimoi/cos/inbox"] == ("/opt/minimoi/cos/inbox", True)
    assert m["/minimoi/cos/outbox"] == ("/opt/minimoi/cos/outbox", False)
    assert m["/minimoi/cos/conversations"] == ("/opt/minimoi/cos/conversations", True)
    assert m["/minimoi/planning-studio"] == ("/opt/minimoi/cos/planning-studio", False)
    assert m["/minimoi/repo"] == ("/opt/minimoi/mc/repo", True)
    assert m["/run/cos-key"] == ("/opt/minimoi/cos/agent-key", True)
    for t in ("/minimoi/mc/work", "/minimoi/mc/workshops", "/minimoi/mc/artifacts"):
        assert m[t][1] is True


def test_scheduler_writes_inbox_and_only_reads_the_outbox():
    m = {target: (source, ro) for source, target, ro in _mounts("cos-scheduler")}
    assert m["/app/data/cos-files/inbox"] == ("/opt/minimoi/cos/inbox", False)
    assert m["/app/data/cos-files/outbox"] == ("/opt/minimoi/cos/outbox", True)
    env = COMPOSE["services"]["cos-scheduler"]["environment"]
    assert "COS_AGENT_INBOX=/minimoi/cos/inbox" in env and "COS_AGENT_OUTBOX=/minimoi/cos/outbox" in env
    assert "MINIMOI_VAULT_ASK_CONVERSATIONS=${MINIMOI_COS_VAULT_CONVERSATIONS:-guild-}" in env


def test_master_key_is_withheld_once_cos_has_its_own():
    env = COMPOSE["services"]["cos-agent-a"]["environment"]
    (line,) = [e for e in env if e.startswith("MINIMOI_MODEL_GATEWAY_KEY=")]
    assert line.startswith("MINIMOI_MODEL_GATEWAY_KEY=${COS_AGENT_MASTER_KEY-${MINIMOI_MODEL_GATEWAY_KEY:?")


def _apply(tmp_path, key_text, master):
    script = (REPO / "docker/cos-agent-a/apply-config.sh").read_text()
    for name in ("openclaw.json", "AGENTS.md"):
        (tmp_path / name).write_text("{}" if name.endswith("json") else "# policy")
    script = script.replace("PINNED_CONFIG=/opt/minimoi/cos-agent-a/openclaw.json", f"PINNED_CONFIG={tmp_path}/openclaw.json")
    script = script.replace("PINNED_POLICY=/opt/minimoi/cos-agent-a/AGENTS.md", f"PINNED_POLICY={tmp_path}/AGENTS.md")
    (tmp_path / "apply.sh").write_text(script)
    keyfile = tmp_path / "key"
    if key_text is not None:
        keyfile.write_text(key_text)
    env = {"PATH": "/usr/bin:/bin", "OPENCLAW_CONFIG_PATH": str(tmp_path / "state/openclaw.json"),
           "COS_GATEWAY_KEY_FILE": str(keyfile)}
    if master is not None:
        env["MINIMOI_MODEL_GATEWAY_KEY"] = master
    return subprocess.run(["sh", str(tmp_path / "apply.sh"), "sh", "-c", 'printf %s "$MINIMOI_MODEL_GATEWAY_KEY"'],
                          env=env, capture_output=True, text=True)


def test_apply_config_prefers_cos_own_key(tmp_path):
    got = _apply(tmp_path, "sk-cosownkey0123456789\n", "")
    assert got.returncode == 0 and got.stdout == "sk-cosownkey0123456789"
    assert "CoS's own capped key" in got.stderr and "sk-" not in got.stderr


def test_apply_config_falls_back_to_master_and_refuses_bad_or_no_key(tmp_path):
    got = _apply(tmp_path, None, "sk-mastermaster0123456")
    assert got.returncode == 0 and got.stdout == "sk-mastermaster0123456" and "master" in got.stderr
    assert _apply(tmp_path, "not-a-key", "sk-mastermaster0123456").returncode == 1
    assert _apply(tmp_path, None, "").returncode == 1


def test_cos_prod_script_parses_and_never_prints_the_key():
    path = REPO / "scripts/operations/cos_prod.sh"
    subprocess.run(["bash", "-n", str(path)], check=True)
    text = path.read_text()
    assert "echo \"$out\"" not in text and "cat \"$KEY_FILE\"\n" not in text
    assert '"$ROOT/scripts/deploy_scoped_release.sh" "$tag" cos-agent-a' in text
