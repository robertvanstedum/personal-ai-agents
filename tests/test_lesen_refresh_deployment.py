"""Deployment contract for the production Lesen refresh loop."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts/operations/install_lesen_refresh_cron.sh"
WORKFLOW = ROOT / ".github/workflows/deploy.yml"
SOURCES = ROOT / "domains/german/data/config/lesen_sources.json"
TEMPLATE = ROOT / "domains/german/templates/german_lesen.html"


def test_installer_uses_packaged_cli_preserves_crontab_and_rotates_logs():
    script = INSTALLER.read_text(encoding="utf-8")

    assert "# minimoi:lesen-refresh" in script
    assert "docker exec minimoi-german python" in script
    assert "/app/domains/german/lesen_refresh_cli.py" in script
    assert "/opt/minimoi/backups/crontab" in script
    assert "grep -vF" in script
    assert "/etc/logrotate.d/minimoi-lesen-refresh" in script


def test_main_deployment_installs_the_refresh_contract():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    pushed = (
        "/opt/minimoi/scripts/install_lesen_refresh_cron.sh"
        '\\",\\"chmod +x'
    )
    executed = '"/opt/minimoi/scripts/install_lesen_refresh_cron.sh"'
    assert pushed in workflow
    assert executed in workflow


def test_permanently_broken_sources_are_inactive():
    data = json.loads(SOURCES.read_text(encoding="utf-8"))
    sources = {source["name"]: source for source in data["sources"]}

    assert sources["ORF Kultur"]["active"] is False
    assert "404" in sources["ORF Kultur"]["inactive_reason"]
    assert sources["Heute"]["active"] is False
    assert "404" in sources["Heute"]["inactive_reason"]


def test_stale_pool_exposes_a_real_refresh_button():
    template = TEMPLATE.read_text(encoding="utf-8")

    stale_start = template.index('id="lesen-stale-notice"')
    stale_end = template.index("</div>", stale_start)
    stale_block = template[stale_start:stale_end]
    assert 'id="lesen-stale-text"' in stale_block
    assert 'id="btn-artikel-laden"' in stale_block
    assert "Aktualisieren ↻" in stale_block
