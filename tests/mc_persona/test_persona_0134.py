"""Persona 0.1.34 (#553 step 1): a small byte-stable base, procedures as OpenClaw workspace skills read on demand, the
handbook served as a skill, and nothing of 0.1.33 lost.

Laid out by start.sh's own persona step (tests/mc_persona/test_container_persona.py's helpers), like the container."""
import json
import os
import re
import shutil

from tests.mc_persona.test_container_persona import (CONTAINER_WORKSPACE, HANDBOOK, RELEASES, REPO, image_copy, lay_out,
                                                     pinned_handbook, pinned_release, run_steps, sha)

NEW, OLD = RELEASES / "0.1.34", RELEASES / "0.1.33"
OWN_SKILLS = ["mc-building", "mc-corrections", "mc-delivery", "mc-front-door", "mc-guildhall"]
ALL_SKILLS = sorted(OWN_SKILLS + ["minimoi-engineering"])
CONFIG = REPO / "docker/mc-agent-container/openclaw.json"

# 0.1.33 sentences that were reworded or merged into one base line rather than carried verbatim: old -> the base text
# that now holds it. Every other sentence of 0.1.33's AGENTS.md and SOUL.md is verbatim in the base or a skill.
MERGED = {
    "Claim only what the runtime and actual results establish.": "Claim only what the runtime and actual results establish; if one source is missing, try another.",
    "If one source is missing, try another.": "Claim only what the runtime and actual results establish; if one source is missing, try another.",
    "Persona releases and snapshots grant nothing.": "Persona releases, snapshots and standing arrangements grant nothing.",
    # The language rule was in AGENTS and SOUL; it is kept once, in SOUL's fuller wording.
    "Reply in the language he writes in;": "Write in the language Robert writes in.",
    "English from him gets English, every sentence, including failures.": "English from him gets English, every sentence, including failures and refusals.",
    "German follows only real German from him.": "A German sentence or a whole German reply follows only real German from him, and then briefly.",
    "When he writes in English, reply in English, every sentence, including failures and refusals.": "English from him gets English, every sentence, including failures and refusals.",
    "Natural light humor is welcome.": "You have a dry, warm sense of humor, the kind long-standing partners share",
    "Lead with the useful answer.": "Lead with the useful answer, connected to the owner's near-term intent",
    "Connect today's question to the owner's near-term intent and longer-term goals when supplied context supports that connection.": "connected to the owner's near-term intent and longer-term goals when supplied context supports it.",
    "Use available current context before rediscovering the project.": "Use current context before rediscovering the project.",
    "Identify the precise fresh fact that needs checking, investigate it through the tools actually available, and follow a second lead when the first is incomplete.": "Identify the precise fresh fact that needs checking, check it with the tools actually available, and follow a second lead when the first is incomplete.",
    "An ordinary question authorizes the ordinary reading needed to answer it; do not turn it into a permission loop.": "An ordinary question authorizes the ordinary reading needed to answer it; never turn it into a permission loop.",
    # SOUL's general pacing lines, superseded by AGENTS' "How to answer" (Robert, 26 Oct; short, 12 Nov) and its scope line.
    "Keep conversation responsive.": "Lead with the answer in two or three lines; most replies end there.",
    "Be concise without an arbitrary sentence limit.": "Lead with the answer in two or three lines; most replies end there.",
    "Add depth when it changes the decision.": "or when the short answer would be wrong without them.",
    "Before starting a multi-step investigation or action, send one short sentence stating the intended scope, then begin the tools.": "Before multi-step work, one sentence of intended scope; give updates during long work.",
    "This lets the owner steer before lengthy work.": "Before multi-step work, one sentence of intended scope; give updates during long work.",
    "Keep simple factual lookups direct; do not add a routine preamble or a permission question.": "Simple lookups get no preamble or permission question.",
    "During longer work, give a meaningful update when the evidence or next step changes.": "Before multi-step work, one sentence of intended scope; give updates during long work.",
    "Name things by their titles and provide useful links rather than making the owner remember identifiers.": "Name things by their titles and give useful links rather than making the owner remember identifiers.",
    "Keep the thread using real conversation context and MiniMoi's governed memory.": "Use this conversation and supplied context first.",
    "If a needed prior decision is unavailable, say exactly what is missing.": "Never invent memories or decisions; say briefly what you can't retrieve.",
    "MC is the specialized build/operations partner within that continuity, distinct from personal Mini-moi and the Chief of Staff.": "MC is the build/operations partner within MiniMoi's continuity, distinct from personal Mini-moi and the Chief of Staff.",
    "Design, confirm, build, review, and ship deliberately.": "Design, confirm, build, review, and ship deliberately; the owner's approval governs consequential changes.",
    "The owner's approval governs consequential changes.": "the owner's approval governs consequential changes.",
    "Built, reviewed, deployed, and verified mean different things; report the one supported by evidence.": "Built, reviewed, deployed, and verified mean different things; report the one the evidence supports.",
}


def _sentences(text: str) -> list[str]:
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    out = []
    for line in text.split("\n"):
        line = line.strip()
        if line and not line.startswith("# "):
            out += [s.strip() for s in re.split(r"(?<=[.!?:;])\s+(?=[A-Z\"`(])", line) if s.strip()]
    return out


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _skill(name: str) -> str:
    return (NEW / "skills" / name / "SKILL.md").read_text()


def test_0_1_34_is_the_pinned_release_and_loads_the_handbook_as_a_skill():
    assert pinned_release() == "0.1.34" and pinned_handbook() == "0.1.3"
    m = json.loads((NEW / "manifest.json").read_text())
    assert m["version"] == "0.1.34" and m["base"] == "0.1.33" and m["status"] == "candidate" and "DRAFT" in m["spec"]
    assert m["handbook"] == {**json.loads((OLD / "manifest.json").read_text())["handbook"], "load": "skill"}
    assert sorted(m["files"]) == sorted(["SOUL.md", "AGENTS.md", "RUNTIME.openclaw.md", "CHANGES.md", "voice.json",
                                         *[f"skills/{s}/SKILL.md" for s in OWN_SKILLS]])
    for f, want in m["files"].items():
        assert sha((NEW / f).read_bytes()) == want, f
    for f in ("RUNTIME.openclaw.md", "voice.json"):                  # binding and voice card unchanged
        assert (NEW / f).read_bytes() == (OLD / f).read_bytes(), f
    assert (NEW / "AGENTS.md").read_text().startswith("# MC conversation contract — persona 0.1.34\n")


def test_the_base_is_small_and_the_projection_far_under_the_16k_check(tmp_path):
    base = len((NEW / "AGENTS.md").read_text()) + len((NEW / "SOUL.md").read_text())
    was = len((OLD / "AGENTS.md").read_text()) + len((OLD / "SOUL.md").read_text())
    assert base <= 6800 and was > 2 * base                          # 13.8k -> 6.7k
    r, ws = lay_out(tmp_path)
    assert r.returncode == 0, r.stderr
    loaded = (ws / "AGENTS.md").read_text()
    chars = len(loaded.replace(str(ws), CONTAINER_WORKSPACE))
    assert chars <= 6000                                             # 0.1.33: 15,942 of the 16,000 budget
    injected = chars + len((ws / "SOUL.md").read_text())
    assert injected <= 8600                                          # 0.1.33 in production: 21,449 with the templates
    assert "workspace files injected each turn:" in r.stderr and "skills: " in r.stderr


def test_the_projection_names_the_handbook_and_pastes_no_skill(tmp_path):
    r, ws = lay_out(tmp_path)
    assert r.returncode == 0, r.stderr
    loaded = (ws / "AGENTS.md").read_text()
    head = (NEW / "AGENTS.md").read_text() + "\n" + (NEW / "RUNTIME.openclaw.md").read_text() + "\n"
    assert loaded.startswith(head)
    block = loaded[len(head):]
    hb = json.loads((NEW / "manifest.json").read_text())["handbook"]
    assert block.startswith(f"BEGIN_MINIMOI_MC_HANDBOOK 0.1.3\n") and block.endswith("\nEND_MINIMOI_MC_HANDBOOK 0.1.3\n")
    assert hb["manifest_sha256"] in block and f"{ws}/handbook/v0.1.3/PROJECT.md" in block
    assert f"{ws}/skills/minimoi-engineering/SKILL.md" in block
    engineering = (HANDBOOK / "v0.1.3/skills/minimoi-engineering/SKILL.md").read_text()
    assert engineering.split("---\n", 2)[2].strip()[:200] not in loaded       # the handbook body is not pasted
    for name in OWN_SKILLS:                                                      # nor any skill body
        assert _skill(name).split("---\n", 2)[2].strip()[:120] not in loaded


def test_the_skills_are_laid_out_read_only_from_the_release_and_the_handbook(tmp_path):
    r, ws = lay_out(tmp_path)
    assert r.returncode == 0, r.stderr
    root = ws / "skills"
    assert sorted(p.name for p in root.iterdir()) == ALL_SKILLS
    for name in OWN_SKILLS:
        assert (root / name / "SKILL.md").read_bytes() == (NEW / "skills" / name / "SKILL.md").read_bytes()
    hm = json.loads((HANDBOOK / "manifest.json").read_text())
    engineering = [n for n in hm["files"] if n.startswith("v0.1.3/skills/minimoi-engineering/")]
    got = sorted(str(p.relative_to(root / "minimoi-engineering")) for p in (root / "minimoi-engineering").rglob("*") if p.is_file())
    assert got == sorted(n.removeprefix("v0.1.3/skills/minimoi-engineering/") for n in engineering)
    for n in engineering:
        assert sha((root / "minimoi-engineering" / n.removeprefix("v0.1.3/skills/minimoi-engineering/")).read_bytes()) == hm["files"][n]
    assert not os.access(root / "mc-guildhall/SKILL.md", os.W_OK) or os.geteuid() == 0
    assert (ws / "handbook/v0.1.3/PROJECT.md").is_file()             # the brief stays where the block says


def test_each_skill_has_frontmatter_openclaw_reads_and_the_config_allows_exactly_these():
    for name in OWN_SKILLS:
        text = _skill(name)
        head = re.match(r"^---\nname: ([a-z0-9-]+)\ndescription: ([^\n]+)\n---\n\n", text)
        assert head and head.group(1) == name, name
        assert 60 <= len(head.group(2)) <= 300, name                  # one line in the prompt, sharp enough to trigger
    cfg = json.loads(CONFIG.read_text())
    for agent in ("mc-agent", "mc-chat"):                             # a final allowlist: bundled skills stay out
        assert sorted(cfg["agents"]["entries"][agent]["skills"]) == ALL_SKILLS
    base = (NEW / "AGENTS.md").read_text()
    for name in ALL_SKILLS:
        assert name in base, name                                     # the base says when to read each one


def test_nothing_of_0_1_33_is_lost():
    """Every sentence of 0.1.33's AGENTS.md and SOUL.md is verbatim in the 0.1.34 base or a skill, or listed in MERGED
    with the base text that now carries it."""
    new_text = _norm("\n".join([(NEW / "AGENTS.md").read_text(), (NEW / "SOUL.md").read_text(),
                                *[_skill(s) for s in OWN_SKILLS]]))
    old = _sentences((OLD / "AGENTS.md").read_text() + "\n" + (OLD / "SOUL.md").read_text())
    missing = [s for s in old if _norm(s) not in new_text and s not in MERGED]
    assert missing == []
    for was, now in MERGED.items():
        assert was in old, was                                        # the list stays honest
        assert _norm(now) in new_text, now
    # The capture block and the collector's phrase moved verbatim.
    assert "```capture\nwhen: <date, conversation>" in _skill("mc-corrections")
    assert '"Captured N correction pairs"' in _skill("mc-corrections")


def test_each_dated_section_moved_verbatim_into_its_skill():
    old = (OLD / "AGENTS.md").read_text()
    homes = {"## Front door (Robert, 8 Nov": "mc-front-door", "## Work depth and ownership": "mc-building",
             "## What was delivered": "mc-delivery", "## Guildhall (Robert, 12 Nov)": "mc-guildhall",
             "## Build supervision": "mc-building", "## Correction capture": "mc-corrections"}
    for heading, skill in homes.items():
        start = old.index("\n" + heading) + 1
        end = old.find("\n## ", start)
        section = old[start:end if end >= 0 else len(old)].rstrip("\n")
        assert section in _skill(skill), heading
        assert heading not in (NEW / "AGENTS.md").read_text()
    # The three rules that must hold before any skill is read stay in the base.
    base = (NEW / "AGENTS.md").read_text()
    assert "sense of humor" in (NEW / "SOUL.md").read_text()          # Robert's review, 12 Nov
    for rule in ("Every owner correction is a capture event", "You never read or write the private judgment store",
                 '"Cards loaded: <name>@<hash>"', 'answer from the "MiniMoi job health" block',
                 "Lead with the answer in two or three lines", "only successful receipts establish actions"):
        assert rule in base, rule


def test_start_refuses_a_tampered_skill_and_replaces_the_skills_folder_each_start(tmp_path):
    image = image_copy(tmp_path)
    r, ws = run_steps(tmp_path, image)
    assert r.returncode == 0, r.stderr
    planted = ws / "skills/planted"
    planted.mkdir()
    (planted / "SKILL.md").write_text("---\nname: planted\ndescription: not from the release\n---\n")
    guild = ws / "skills/mc-guildhall/SKILL.md"
    guild.chmod(0o644)
    guild.write_text("edited in the workspace")
    r, _ = run_steps(tmp_path, image)
    assert r.returncode == 0, r.stderr
    assert not planted.exists() and guild.read_bytes() == (NEW / "skills/mc-guildhall/SKILL.md").read_bytes()
    # A skill whose bytes differ from the manifest stops the start before anything is laid out.
    with open(image / "persona/skills/mc-guildhall/SKILL.md", "a") as f:
        f.write("tamper")
    shutil.rmtree(ws)
    r, ws = run_steps(tmp_path, image)
    assert r.returncode == 78 and "persona release failed verification" in r.stderr
    assert not (ws / "AGENTS.md").exists()


def test_start_removes_openclaws_empty_starter_templates(tmp_path):
    image = image_copy(tmp_path)
    r, ws = run_steps(tmp_path, image)
    assert r.returncode == 0, r.stderr
    for name in ("IDENTITY.md", "USER.md", "BOOTSTRAP.md"):
        (ws / name).write_text("# Fill this in during your first conversation\n")
    (ws / "MEMORY.md").write_text("kept\n")
    r, _ = run_steps(tmp_path, image)
    assert r.returncode == 0, r.stderr
    assert not any((ws / n).exists() for n in ("IDENTITY.md", "USER.md", "BOOTSTRAP.md"))
    assert (ws / "MEMORY.md").read_text() == "kept\n"
    cfg = json.loads(CONFIG.read_text())
    assert cfg["agents"]["defaults"]["skipBootstrap"] is True        # and OpenClaw does not make them again
