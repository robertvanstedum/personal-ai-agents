"""What Master Craftsman's own container loads: the persona release its Dockerfile pins, with the handbook that release
names, laid out by start.sh.

The projection is produced by start.sh's own persona step (steps 1-2, run unchanged), with only the image directory
redirected to a copy of what the Dockerfile copies (the persona release, the handbook, handbook.mjs and
model-access.cjs). Needs node on PATH, like tests/test_mc_persona_loader.py."""
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RELEASES = REPO / "services/mc_operations/personas/mc-chat/releases"
HANDBOOK = REPO / "services/mc_operations/handbook"
DOCKERFILE = REPO / "docker/mc-agent-container/Dockerfile"
START = REPO / "docker/mc-agent-container/start.sh"
LOADER = REPO / "docker/mc-agent-container/handbook.mjs"
MODEL_ACCESS = REPO / "docker/mc-agent-container/model-access.cjs"
WORKFLOW = REPO / ".github/workflows/deploy-mc-agent.yml"
HANDBOOK_REF = HANDBOOK / "v0.1.2/skills/minimoi-engineering/references/build-and-deploy.md"
PROVIDER_KEYS = {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY", "MC_ANTHROPIC_API_KEY", "MC_MODEL_GATEWAY_KEY"}
HEADING = "## Outside reports and GitHub issues\n"
# Where the image's state lives (start.sh's default OPENCLAW_STATE_DIR): the block names paths under it.
CONTAINER_WORKSPACE = "/home/node/.openclaw/workspace-mc"
VOICE_LINE = ["0.1.18", "0.1.19", "0.1.20", "0.1.21", "0.1.22"]


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def pinned_release() -> str:
    found = re.findall(r"^COPY --chown=root:root services/mc_operations/personas/mc-chat/releases/([0-9.]+)/ "
                       r"/opt/minimoi/mc-agent/persona/$", DOCKERFILE.read_text(), re.M)
    assert len(found) == 1, found
    return found[0]


def pinned_handbook() -> str:
    found = re.findall(r"^COPY --chown=root:root services/mc_operations/handbook/v([0-9.]+)/ "
                       r"/opt/minimoi/mc-agent/handbook/v\1/$", DOCKERFILE.read_text(), re.M)
    assert len(found) == 1, found
    assert ("COPY --chown=root:root services/mc_operations/handbook/manifest.json "
            "/opt/minimoi/mc-agent/handbook/manifest.json\n") in DOCKERFILE.read_text()
    return found[0]


def outside_reports_section(text: str) -> str:
    """The section, up to the next heading or the end of the handbook block (it ends SKILL.md)."""
    start = text.index(HEADING)
    ends = [i for i in (text.find("\n## ", start + len(HEADING)), text.find("\nEND_MINIMOI_MC_HANDBOOK", start)) if i >= 0]
    return text[start:min(ends, default=len(text))].rstrip("\n")


def image_copy(tmp_path: Path, release: str | None = None) -> Path:
    """The image's /opt/minimoi/mc-agent as the Dockerfile lays it out, for the parts start.sh steps 1-2 read."""
    image = tmp_path / "image"
    shutil.copytree(RELEASES / (release or pinned_release()), image / "persona")
    version = pinned_handbook()
    (image / "handbook").mkdir()
    shutil.copy(HANDBOOK / "manifest.json", image / "handbook/manifest.json")
    shutil.copytree(HANDBOOK / f"v{version}", image / f"handbook/v{version}")
    shutil.copy(LOADER, image / "handbook.mjs")
    shutil.copy(MODEL_ACCESS, image / "model-access.cjs")
    return image


def run_steps(tmp_path: Path, image: Path, start: Path = START):
    sh = start.read_text()
    cut = sh.index("\n# 3. Config")
    assert sh.count("IMAGE_DIR=/opt/minimoi/mc-agent\n") == 1
    script = tmp_path / "persona-step.sh"
    script.write_text(sh[:cut].replace("IMAGE_DIR=/opt/minimoi/mc-agent\n", f"IMAGE_DIR={image}\n") + "\n")
    env = {k: v for k, v in os.environ.items() if k not in PROVIDER_KEYS}
    env["OPENCLAW_STATE_DIR"] = str(tmp_path / "state")
    r = subprocess.run(["/bin/sh", str(script)], env=env, capture_output=True, text=True, timeout=20)
    return r, tmp_path / "state/workspace-mc"


def lay_out(tmp_path: Path, release: str | None = None):
    return run_steps(tmp_path, image_copy(tmp_path, release))


def container_chars(loaded: str, ws: Path) -> int:
    """The projection's size in the image, where the block names /home/node/.openclaw/workspace-mc, not tmp_path."""
    return len(loaded.replace(str(ws), CONTAINER_WORKSPACE))


def test_the_container_loads_the_handbook_the_persona_pins(tmp_path):
    release = pinned_release()
    persona = RELEASES / release
    manifest = json.loads((persona / "manifest.json").read_text())
    r, ws = lay_out(tmp_path)
    assert r.returncode == 0, r.stderr
    loaded = (ws / "AGENTS.md").read_text()
    assert (ws / "SOUL.md").read_bytes() == (persona / "SOUL.md").read_bytes()
    head = (persona / "AGENTS.md").read_text() + "\n" + (persona / "RUNTIME.openclaw.md").read_text() + "\n"
    assert loaded.startswith(head)
    block = loaded[len(head):]

    # The block: the handbook the release pins, by its manifest's hash, with the manifest's path named (finding 6).
    hb = manifest["handbook"]
    version = hb["version"]
    assert version == pinned_handbook() and hb["manifest"] == "services/mc_operations/handbook/manifest.json"
    assert hb["manifest_sha256"] == sha((HANDBOOK / "manifest.json").read_bytes())
    root = ws / "handbook"
    lines = block.split("\n")
    assert lines[0] == f"BEGIN_MINIMOI_MC_HANDBOOK {version}" and block.endswith(f"\nEND_MINIMOI_MC_HANDBOOK {version}\n")
    assert lines[1] == f"Pinned handbook: {root}/v{version}/"
    assert lines[2] == f"Manifest: {root}/manifest.json"
    assert lines[3] == f"Manifest SHA-256: {hb['manifest_sha256']}"
    skill = (HANDBOOK / f"v{version}/skills/minimoi-engineering/SKILL.md").read_text()
    assert block.count(skill) == 1 and loaded.count("BEGIN_MINIMOI_MC_HANDBOOK") == 1
    for name in ("PROJECT.md", "skills/minimoi-engineering/references/build-and-deploy.md"):
        assert f"\n{root}/v{version}/{name}\n" in block

    # Every path the block names holds the manifest's bytes; the manifest itself is the pinned one.
    hm = json.loads((HANDBOOK / "manifest.json").read_text())
    assert (root / "manifest.json").read_bytes() == (HANDBOOK / "manifest.json").read_bytes()
    for name, want in hm["files"].items():
        assert sha((root / name).read_bytes()) == want, name
    assert sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()) == sorted(["manifest.json", *hm["files"]])

    # The outside-reports rule is loaded once, the handbook's words; the whole projection is within the budget.
    assert loaded.count(HEADING) == 1
    assert outside_reports_section(loaded) == outside_reports_section(HANDBOOK_REF.read_text())
    assert f"handbook {version} loaded (manifest {hb['manifest_sha256']})" in r.stderr
    assert f"persona {release} laid out ({len(loaded)} chars)" in r.stderr
    assert len(loaded) <= manifest["projection_budget_chars"] == 16000
    assert container_chars(loaded, ws) <= 16000


def test_the_merged_persona_carries_both_lines(tmp_path):
    """0.1.23 is 0.1.17 (outside reports) and 0.1.22 (the voice card), both from 0.1.16, in one release."""
    merged, outside, voice = RELEASES / "0.1.23", RELEASES / "0.1.17", RELEASES / "0.1.22"
    m = json.loads((merged / "manifest.json").read_text())
    for f in ("SOUL.md", "RUNTIME.openclaw.md"):
        assert (merged / f).read_bytes() == (outside / f).read_bytes() == (voice / f).read_bytes()
    assert (merged / "voice.json").read_bytes() == (voice / "voice.json").read_bytes()
    assert m["files"]["voice.json"] == json.loads((voice / "manifest.json").read_text())["files"]["voice.json"]
    # Every line of 0.1.17's contract is in what the container loads (its outside-reports section via the handbook).
    r, ws = lay_out(tmp_path)
    assert r.returncode == 0, r.stderr
    loaded = (ws / "AGENTS.md").read_text()
    contract = (outside / "AGENTS.md").read_text().split("\n")[1:]
    assert [line for line in contract if line not in loaded.split("\n")] == []
    # The voice line came in byte for byte: each release verifies against its own manifest.
    for v in VOICE_LINE:
        vm = json.loads((RELEASES / v / "manifest.json").read_text())
        assert vm["version"] == v and "voice.json" in vm["files"]
        for f, want in vm["files"].items():
            assert sha((RELEASES / v / f).read_bytes()) == want, (v, f)


def test_one_persona_the_portals_pin_and_the_container_name_the_same_release():
    """The portal reads the voice card through pin.json, the container copies the release its Dockerfile names: one release."""
    pin = json.loads((RELEASES.parent / "pin.json").read_text())
    assert pin == {"release": pinned_release()}
    assert f"minimoi.persona={pin['release']} " in DOCKERFILE.read_text()
    assert f'personas/mc-chat/releases/{pin["release"]}/"' in (REPO / "scripts/staging/beta_dev_deploy.py").read_text()


def test_0_1_24_changes_only_the_voice_cards_text_from_0_1_23():
    new, old = RELEASES / "0.1.24", RELEASES / "0.1.23"
    for f in ("SOUL.md", "RUNTIME.openclaw.md"):
        assert (new / f).read_bytes() == (old / f).read_bytes()
    assert (new / "AGENTS.md").read_text().split("\n")[1:] == (old / "AGENTS.md").read_text().split("\n")[1:]
    assert (new / "AGENTS.md").read_text().split("\n")[0] == "# MC conversation contract — persona 0.1.24"
    card, was = json.loads((new / "voice.json").read_text()), json.loads((old / "voice.json").read_text())
    assert card.pop("card") != was.pop("card") and card == was
    m, base = json.loads((new / "manifest.json").read_text()), json.loads((old / "manifest.json").read_text())
    assert m["version"] == "0.1.24" and m["status"] == "candidate" and m["handbook"] == base["handbook"]
    assert "Robert's direction of 6 Nov 2026, restoring VOICE_MANNER's clauses to the direct path" in m["spec"]
    assert set(m["files"]) == set(base["files"])
    for f, want in m["files"].items():
        assert sha((new / f).read_bytes()) == want, f


def test_0_1_25_changes_only_the_voice_cards_text_from_0_1_24():
    new, old = RELEASES / "0.1.25", RELEASES / "0.1.24"
    for f in ("SOUL.md", "RUNTIME.openclaw.md"):
        assert (new / f).read_bytes() == (old / f).read_bytes()
    assert (new / "AGENTS.md").read_text().split("\n")[1:] == (old / "AGENTS.md").read_text().split("\n")[1:]
    assert (new / "AGENTS.md").read_text().split("\n")[0] == "# MC conversation contract — persona 0.1.25"
    card, was = json.loads((new / "voice.json").read_text()), json.loads((old / "voice.json").read_text())
    assert card.pop("card") != was.pop("card") and card == was
    m, base = json.loads((new / "manifest.json").read_text()), json.loads((old / "manifest.json").read_text())
    assert m["version"] == "0.1.25" and m["status"] == "candidate" and m["handbook"] == base["handbook"]
    assert "Robert's direction of 6 Nov 2026: voice persona as his longtime partner, not a task taker, on the model of German's persona." in m["spec"]
    assert set(m["files"]) == set(base["files"])
    for f, want in m["files"].items():
        assert sha((new / f).read_bytes()) == want, f


def test_0_1_26_says_english_gets_english_and_changes_nothing_else():
    new, old = RELEASES / "0.1.26", RELEASES / "0.1.25"
    for f in ("RUNTIME.openclaw.md", "voice.json"):
        assert (new / f).read_bytes() == (old / f).read_bytes()
    soul, was = (new / "SOUL.md").read_text().split("\n"), (old / "SOUL.md").read_text().split("\n")
    assert soul[:-2] == was[:-2]
    assert "When he writes in English, reply in English" in soul[-2] and "follows only real German from him" in soul[-2]
    assert "never a German answer" in soul[-2]
    assert "Preserve each language persona's distinct character" in soul[-2]
    agents, was = (new / "AGENTS.md").read_text().split("\n"), (old / "AGENTS.md").read_text().split("\n")
    assert agents[0] == "# MC conversation contract — persona 0.1.26"
    added = [line for line in agents[1:] if line not in was]
    assert len(added) == 1 and "English from him gets English" in added[0]
    assert "German follows only real German from him" in added[0]
    assert [line for line in agents[1:] if line != added[0]] == was[1:]
    m, base = json.loads((new / "manifest.json").read_text()), json.loads((old / "manifest.json").read_text())
    assert m["version"] == "0.1.26" and m["status"] == "candidate" and m["handbook"] == base["handbook"]
    assert set(m["files"]) == set(base["files"]) and m["config_change"] is False
    for f, want in m["files"].items():
        assert sha((new / f).read_bytes()) == want, f


def test_0_1_27_points_mc_at_the_delivery_record_and_changes_nothing_else():
    new, old = RELEASES / "0.1.27", RELEASES / "0.1.26"
    for f in ("SOUL.md", "RUNTIME.openclaw.md", "voice.json"):
        assert (new / f).read_bytes() == (old / f).read_bytes()
    agents, was = (new / "AGENTS.md").read_text().split("\n"), (old / "AGENTS.md").read_text().split("\n")
    assert agents[0] == "# MC conversation contract — persona 0.1.27"
    added = [line for line in agents[1:] if line not in was]
    assert added == ["## What was delivered (Robert, 7 Nov)", added[1]]
    assert "/minimoi/docs/delivery/DEV.md" in added[1] and "in production, on Dev only, or still open" in added[1]
    section = "\n" + "\n".join(added) + "\n"
    assert "\n".join(agents[1:]).replace(section, "", 1) == "\n".join(was[1:])
    m, base = json.loads((new / "manifest.json").read_text()), json.loads((old / "manifest.json").read_text())
    assert m["version"] == "0.1.27" and m["status"] == "candidate" and m["handbook"] == base["handbook"]
    assert set(m["files"]) == set(base["files"]) and m["config_change"] is False
    for f, want in m["files"].items():
        assert sha((new / f).read_bytes()) == want, f


def test_start_refuses_a_release_that_names_no_handbook(tmp_path):
    image = image_copy(tmp_path)
    m = json.loads((image / "persona/manifest.json").read_text())
    del m["handbook"]
    (image / "persona/manifest.json").write_text(json.dumps(m))
    r, ws = run_steps(tmp_path, image)
    assert r.returncode == 78 and "REFUSED: persona release names no handbook" in r.stderr
    assert not (ws / "AGENTS.md").exists()


@pytest.mark.parametrize("tamper", ["source", "manifest", "pin"])
def test_start_refuses_a_handbook_that_differs_from_its_pin(tmp_path, tamper):
    image = image_copy(tmp_path)
    version = pinned_handbook()
    if tamper == "source":
        with open(image / f"handbook/v{version}/PROJECT.md", "a") as f:
            f.write("tamper")
    elif tamper == "manifest":
        with open(image / "handbook/manifest.json", "a") as f:
            f.write(" ")
    else:
        m = json.loads((image / "persona/manifest.json").read_text())
        m["handbook"]["manifest_sha256"] = "0" * 64
        (image / "persona/manifest.json").write_text(json.dumps(m))
    r, ws = run_steps(tmp_path, image)
    assert r.returncode == 78 and f"REFUSED: handbook {version} failed verification" in r.stderr
    assert not (ws / "AGENTS.md").exists()


def test_each_start_lays_the_handbook_out_afresh(tmp_path):
    """MC can write in its workspace: a changed, added or symlinked handbook folder is replaced from the image."""
    image = image_copy(tmp_path)
    r, ws = run_steps(tmp_path, image)
    assert r.returncode == 0, r.stderr
    first = (ws / "AGENTS.md").read_bytes()
    root = ws / "handbook"
    project = root / f"v{pinned_handbook()}/PROJECT.md"
    project.chmod(0o644)
    project.write_text("edited in the workspace")
    (root / "extra.md").write_text("added")
    r, _ = run_steps(tmp_path, image)
    assert r.returncode == 0, r.stderr
    assert (ws / "AGENTS.md").read_bytes() == first and not (root / "extra.md").exists()
    assert project.read_bytes() == (HANDBOOK / f"v{pinned_handbook()}/PROJECT.md").read_bytes()
    assert not os.access(project, os.W_OK) or os.geteuid() == 0
    # A symlink in its place is removed, never followed.
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.md").write_text("not MC's")
    shutil.rmtree(root)
    root.symlink_to(outside)
    r, _ = run_steps(tmp_path, image)
    assert r.returncode == 0, r.stderr
    assert not root.is_symlink() and (outside / "keep.md").read_text() == "not MC's"
    assert sorted(p.name for p in outside.iterdir()) == ["keep.md"]
    # A folder MC made unwritable or unreadable is still replaced: start never wedges on it.
    for folder, mode in ((root / f"v{pinned_handbook()}", 0o555), (root, 0o000)):
        folder.chmod(mode)
        r, _ = run_steps(tmp_path, image)
        assert r.returncode == 0, r.stderr
        assert project.read_bytes() == (HANDBOOK / f"v{pinned_handbook()}/PROJECT.md").read_bytes()


def test_every_container_pin_names_the_same_release():
    release = pinned_release()
    handbook = json.loads((RELEASES / release / "manifest.json").read_text())["handbook"]["version"]
    assert f"minimoi.persona={release} minimoi.handbook={handbook}\n" in DOCKERFILE.read_text()
    assert handbook == pinned_handbook()
    assert "docker/mc-agent-container/handbook.mjs /opt/minimoi/mc-agent/" in DOCKERFILE.read_text()
    # Today's plan-route work stays in the image with the handbook.
    assert "docker/mc-agent-container/model-access.cjs " in DOCKERFILE.read_text()
    # The tag-only release workflow is left out of this change on purpose (1.2 Beta, B6): it still names 0.1.17, a release
    # that still exists, so its sanity step holds. Its move to this release and its image check come with the tag; see
    # docker/mc-agent-container/RELEASE_mc-agent-v0.1.23-1.md, "Before the tag".
    named = re.findall(r"personas/mc-chat/releases/([0-9.]+)/manifest.json", WORKFLOW.read_text())
    assert named and all((RELEASES / v / "manifest.json").is_file() for v in named)
    assert "refs/heads" not in WORKFLOW.read_text() and "tags: ['mc-agent-v*']" in WORKFLOW.read_text()


def test_the_loaded_handbook_points_to_the_operations_practice(tmp_path):
    r, ws = lay_out(tmp_path)
    assert r.returncode == 0, r.stderr
    loaded = (ws / "AGENTS.md").read_text()
    assert loaded.count("planning-studio/operations/OPS-YYYY-MM-DD-slug") == 1
    assert "`operations/WORKLIST.md` as\nthe index" in loaded
    # The earlier release stays for provenance, and only the pointer and version lines differ from it.
    old = (HANDBOOK / "v0.1.1/skills/minimoi-engineering/SKILL.md").read_text().splitlines()
    new = (HANDBOOK / "v0.1.2/skills/minimoi-engineering/SKILL.md").read_text().splitlines()
    added = [line for line in new if line not in old]
    assert len(added) == 4 and added[0].startswith("Version: 0.1.2") and added[1] == "## Official temporary plans"


def test_the_cos_image_and_this_container_pin_the_same_handbook():
    cos = (REPO / "docker/cos-agent-a/build-mc-handbook.mjs").read_text()
    assert f"MANIFEST_SHA256 = '{sha((HANDBOOK / 'manifest.json').read_bytes())}'" in cos
    assert f"candidate_version !== '{pinned_handbook()}'" in cos


def test_0_1_28_adds_only_the_answer_now_section():
    new, old = RELEASES / "0.1.28", RELEASES / "0.1.27"
    for name in ("SOUL.md", "voice.json", "RUNTIME.openclaw.md"):
        assert (new / name).read_bytes() == (old / name).read_bytes(), name
    agents = (new / "AGENTS.md").read_text()
    assert agents.startswith("# MC conversation contract — persona 0.1.28")
    assert "**Answer now or say when:** it waits till he asks; you can't return." in agents
    before = (old / "AGENTS.md").read_text().split("\n", 1)[1].rstrip("\n")
    assert before in agents
    assert json.loads((new / "manifest.json").read_text())["version"] == "0.1.28"
