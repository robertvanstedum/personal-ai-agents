"""Persona 0.1.36 (#567 wave 2): one narrow edit on 0.1.35. A vague message ("hmm", "thoughts?") gets one question even
when platform state is attached, and a vague message matches no card. Nothing else changes."""
import json

from tests.mc_persona.test_container_persona import RELEASES, REPO, pinned_handbook, pinned_release, sha

NEW, OLD = RELEASES / "0.1.36", RELEASES / "0.1.35"


def test_0_1_36_is_pinned_everywhere():
    assert pinned_release() == "0.1.36" and pinned_handbook() == "0.1.3"
    assert json.loads((REPO / "services/mc_operations/personas/mc-chat/pin.json").read_text()) == {"release": "0.1.36"}
    m = json.loads((NEW / "manifest.json").read_text())
    assert m["version"] == "0.1.36" and m["base"] == "0.1.35" and m["status"] == "candidate"
    for name, digest in m["files"].items():
        assert sha((NEW / name).read_bytes()) == digest, name
    dockerfile = (REPO / "docker/mc-agent-container/Dockerfile").read_text()
    assert "releases/0.1.36/ /opt/minimoi/mc-agent/persona/" in dockerfile and "minimoi.persona=0.1.36" in dockerfile


def test_only_agents_and_changes_differ():
    files = sorted(p.relative_to(NEW).as_posix() for p in NEW.rglob("*") if p.is_file())
    assert files == sorted(p.relative_to(OLD).as_posix() for p in OLD.rglob("*") if p.is_file())
    for name in files:
        if name not in ("AGENTS.md", "CHANGES.md", "manifest.json"):
            assert (NEW / name).read_bytes() == (OLD / name).read_bytes(), name


def test_a_vague_message_gets_a_question_even_with_state_attached():
    a = (NEW / "AGENTS.md").read_text()
    assert a.startswith("# MC conversation contract — persona 0.1.36\n")
    assert 'Vague or two words ("hmm, thoughts?"): one question ("Thoughts on what?"), no tools; attached state isn\'t his topic.' in a
    assert "(a vague one matches none), open it with `minimoi_open_card`" in a
    # Every other 0.1.35 line is still there verbatim.
    new_lines = a.splitlines()
    changed = [l for l in (OLD / "AGENTS.md").read_text().splitlines()[1:] if l not in new_lines]
    assert len(changed) == 2 and changed[0].startswith("Vague or two words") and changed[1].startswith("Your procedures are cards")
