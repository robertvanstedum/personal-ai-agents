"""Persona 0.1.37 (#693): one card edited on 0.1.36. mc-building gains "From an issue to a build": research, a saved
design, a build proposed for that exact revision, the owner's yes that names the code, and the result read back.
Nothing else changes; AGENTS.md differs only in its version heading."""
import json

from tests.mc_persona.test_container_persona import RELEASES, REPO, pinned_handbook, pinned_release, sha

NEW, OLD = RELEASES / "0.1.37", RELEASES / "0.1.36"
CARD = "skills/mc-building/SKILL.md"


def test_0_1_37_is_pinned_everywhere():
    assert pinned_release() == "0.1.37" and pinned_handbook() == "0.1.3"
    assert json.loads((REPO / "services/mc_operations/personas/mc-chat/pin.json").read_text()) == {"release": "0.1.37"}
    m = json.loads((NEW / "manifest.json").read_text())
    assert m["version"] == "0.1.37" and m["base"] == "0.1.36" and m["status"] == "candidate"
    for name, digest in m["files"].items():
        assert sha((NEW / name).read_bytes()) == digest, name
    dockerfile = (REPO / "docker/mc-agent-container/Dockerfile").read_text()
    assert "releases/0.1.37/ /opt/minimoi/mc-agent/persona/" in dockerfile and "minimoi.persona=0.1.37" in dockerfile


def test_only_the_building_card_and_the_version_heading_differ():
    files = sorted(p.relative_to(NEW).as_posix() for p in NEW.rglob("*") if p.is_file())
    assert files == sorted(p.relative_to(OLD).as_posix() for p in OLD.rglob("*") if p.is_file())
    for name in files:
        if name not in ("AGENTS.md", CARD, "CHANGES.md", "manifest.json"):
            assert (NEW / name).read_bytes() == (OLD / name).read_bytes(), name
    # AGENTS.md is injected every turn and 0.1.36 is 2 characters under its limit: only the version heading differs.
    assert (NEW / "AGENTS.md").read_text() == (OLD / "AGENTS.md").read_text().replace("persona 0.1.36", "persona 0.1.37", 1)


def test_the_building_card_gains_one_section_and_keeps_every_0_1_36_line():
    new, old = (NEW / CARD).read_text(), (OLD / CARD).read_text()
    head = new.split("\n")[2]
    assert head.startswith("description: ") and 60 <= len(head) - len("description: ") <= 300   # the loader's limit
    assert "an issue or work to build, a delegated job's result" in head
    section = new.split("## From an issue to a build", 1)[1].split("\n## ", 1)[0]
    # Without the new section and its own "Open when" line, the card is 0.1.36's, byte for byte.
    assert new.replace("\n## From an issue to a build" + section, "", 1).replace(head, old.split("\n")[2], 1) == old
    order = ["Read the issue", "minimoi_delegate_research", "minimoi_delegate_result", "minimoi_work_save",
             "minimoi_delegate_build", "`design_artifact_id`", "`design_revision`", "yes P-XXXX", "exactly as returned"]
    assert [section.index(step) for step in order] == sorted(section.index(step) for step in order)
    for said in ("You cannot search the code", "a plain yes starts nothing", "Never say tests passed",
                 "which the runner did not verify", "You are not told when a job ends",
                 "5 USD and a time limit of 60 minutes", "When the runner is set up to publish",
                 "Nothing is merged, released or deployed", "Private or professional material is never built this way"):
        assert said in section, said
