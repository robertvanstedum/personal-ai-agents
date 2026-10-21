"""Alert rules and configs (spec 161 §9, §12.2): hygiene always; promtool/amtool when the tools
are available locally (PROMTOOL / AMTOOL env vars or on PATH). CI runs them with pinned images."""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CONF = REPO / "config" / "observability"


def tool(name, env):
    path = os.environ.get(env) or shutil.which(name)
    if not path:
        pytest.skip(f"{name} not available locally (CI runs it)")
    return path


def test_rule_hygiene():
    out = subprocess.run([sys.executable, str(REPO / "scripts/observability/check_rules_meta.py")],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stdout


def test_promtool_rule_tests_pass():
    promtool = tool("promtool", "PROMTOOL")
    for args in (["check", "rules", "rules/minimoi_o1.yaml"], ["test", "rules", "rules/tests/minimoi_o1_test.yaml"]):
        out = subprocess.run([promtool, *args], cwd=CONF, capture_output=True, text=True)
        assert out.returncode == 0, out.stdout + out.stderr


def test_alertmanager_config_is_valid(tmp_path):
    amtool = tool("amtool", "AMTOOL")
    (tmp_path / "templates").mkdir()
    shutil.copy(CONF / "templates" / "telegram.tmpl", tmp_path / "templates")
    for secret in ("am_telegram_bot_token", "deadman_ping_url"):
        (tmp_path / secret).write_text("placeholder")
    text = (CONF / "alertmanager.yml").read_text().replace("${TELEGRAM_CHAT_ID}", "123456789")
    text = text.replace("/run/secrets/", f"{tmp_path}/").replace("/etc/alertmanager/templates/", f"{tmp_path}/templates/")
    (tmp_path / "am.yml").write_text(text)
    out = subprocess.run([amtool, "check-config", str(tmp_path / "am.yml")], capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr


def test_prometheus_config_is_valid(tmp_path):
    promtool = tool("promtool", "PROMTOOL")
    (tmp_path / "rules").mkdir()
    shutil.copy(CONF / "rules" / "minimoi_o1.yaml", tmp_path / "rules")
    text = (CONF / "prometheus.yml").read_text().replace("/etc/prometheus/rules/", f"{tmp_path}/rules/")
    (tmp_path / "prometheus.yml").write_text(text)
    out = subprocess.run([promtool, "check", "config", str(tmp_path / "prometheus.yml")], capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr


def test_no_secret_or_chat_id_is_committed_in_configs():
    text = "\n".join(p.read_text() for p in CONF.rglob("*") if p.is_file())
    assert not re.search(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b", text)          # telegram bot token shape
    assert "chat_id: ${TELEGRAM_CHAT_ID}" in (CONF / "alertmanager.yml").read_text()


def test_collector_allowlist_matches_the_vocabulary_labels():
    import yaml
    vocab = yaml.safe_load((CONF / "vocabulary.yaml").read_text())
    collector = (CONF / "collector.yaml").read_text()
    keep = re.search(r'metric_statements:.*?keep_keys\(attributes, \[(.*?)\]\)', collector, re.S).group(1)
    allowed = {k.strip().strip('"') for k in keep.split(",")}
    assert allowed == set(vocab["labels"])
