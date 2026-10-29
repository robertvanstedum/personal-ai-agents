"""What Master Craftsman's own container loads: the persona release its Dockerfile pins, laid out by start.sh.

The projection is produced by start.sh's own persona step (steps 1-2, run unchanged), with only the image directory
redirected to a copy of the pinned release. Needs node on PATH, like tests/test_mc_persona_loader.py."""
import os
import re
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RELEASES = REPO / "services/mc_operations/personas/mc-chat/releases"
DOCKERFILE = REPO / "docker/mc-agent-container/Dockerfile"
START = REPO / "docker/mc-agent-container/start.sh"
WORKFLOW = REPO / ".github/workflows/deploy-mc-agent.yml"
HANDBOOK_REF = REPO / "services/mc_operations/handbook/v0.1.1/skills/minimoi-engineering/references/build-and-deploy.md"
PROVIDER_KEYS = {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY", "MC_ANTHROPIC_API_KEY", "MC_MODEL_GATEWAY_KEY"}
HEADING = "## Outside reports and GitHub issues\n"


def pinned_release() -> str:
    found = re.findall(r"^COPY --chown=root:root services/mc_operations/personas/mc-chat/releases/([0-9.]+)/ "
                       r"/opt/minimoi/mc-agent/persona/$", DOCKERFILE.read_text(), re.M)
    assert len(found) == 1, found
    return found[0]


def outside_reports_section(text: str) -> str:
    start = text.index(HEADING)
    end = text.index("\n## ", start + len(HEADING))
    return text[start:end + 1]


def lay_out(tmp_path: Path, release: str):
    sh = START.read_text()
    cut = sh.index("\n# 3. Config")
    image = tmp_path / "image"
    shutil.copytree(RELEASES / release, image / "persona")
    assert sh.count("IMAGE_DIR=/opt/minimoi/mc-agent\n") == 1
    script = tmp_path / "persona-step.sh"
    script.write_text(sh[:cut].replace("IMAGE_DIR=/opt/minimoi/mc-agent\n", f"IMAGE_DIR={image}\n") + "\n")
    env = {k: v for k, v in os.environ.items() if k not in PROVIDER_KEYS}
    env["OPENCLAW_STATE_DIR"] = str(tmp_path / "state")
    r = subprocess.run(["/bin/sh", str(script)], env=env, capture_output=True, text=True, timeout=10)
    return r, tmp_path / "state/workspace-mc"


def test_the_container_loads_the_outside_reports_rule(tmp_path):
    release = pinned_release()
    r, ws = lay_out(tmp_path, release)
    assert r.returncode == 0, r.stderr
    persona = RELEASES / release
    loaded = (ws / "AGENTS.md").read_text()
    assert loaded == (persona / "AGENTS.md").read_text() + "\n" + (persona / "RUNTIME.openclaw.md").read_text()
    assert (ws / "SOUL.md").read_bytes() == (persona / "SOUL.md").read_bytes()
    # The rule MC's container loads is the handbook's rule, word for word, once.
    assert loaded.count(HEADING) == 1
    assert outside_reports_section(loaded) == outside_reports_section(HANDBOOK_REF.read_text())
    assert f"persona {release} laid out ({len(loaded)} chars)" in r.stderr
    assert len(loaded) <= 16000


def test_every_container_pin_names_the_same_release():
    release = pinned_release()
    assert f"minimoi.persona={release}\n" in DOCKERFILE.read_text()
    assert f"personas/mc-chat/releases/{release}/manifest.json" in WORKFLOW.read_text()
