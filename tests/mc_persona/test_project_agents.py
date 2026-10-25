import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "mc_persona"))
import project_agents as P  # noqa: E402

RUNTIME_AND_PRIVATE = "\n# Native binding\nruntime text\n\n# Private current context (operator-supplied data)\n{\"k\": \"v\"}\n"


def sha(b):
    return hashlib.sha256(b).hexdigest()


def release(tmp, name, agents, budget=16000):
    d = tmp / name
    d.mkdir()
    (d / "AGENTS.md").write_text(agents)
    m = {"version": name, "files": {"AGENTS.md": sha(agents.encode())}, "projection_budget_chars": budget}
    (d / "manifest.json").write_text(json.dumps(m))
    return d


@pytest.fixture
def env(tmp_path):
    base = release(tmp_path, "0.1.8", "# Contract\nrules\n")
    new = release(tmp_path, "0.1.10", "# Contract\nrules\n\n## Correction capture (standing)\ncapture\n")
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "AGENTS.md").write_text("# Contract\nrules\n" + RUNTIME_AND_PRIVATE)
    (ws / "SOUL.md").write_text("soul")
    cfg = tmp_path / "openclaw.json"
    cfg.write_text("{}")
    return base, new, ws, cfg, tmp_path / "backup"


def args(env, *extra):
    base, new, ws, cfg, bk = env
    return ["apply", "--release", str(new), "--base-release", str(base), "--workspace", str(ws), "--config", str(cfg),
            "--expect-agents", sha((ws / "AGENTS.md").read_bytes()), "--expect-config", sha(cfg.read_bytes()),
            "--backup-dir", str(bk), *extra]


def test_apply_replaces_only_the_base_prefix_and_keeps_private_context(env):
    base, new, ws, cfg, bk = env
    assert P.main(args(env)) == 0
    out = (ws / "AGENTS.md").read_text()
    assert out == (new / "AGENTS.md").read_text() + RUNTIME_AND_PRIVATE
    assert (ws / "SOUL.md").read_text() == "soul" and cfg.read_text() == "{}"
    assert (bk / "AGENTS.md.preimage").read_text() == "# Contract\nrules\n" + RUNTIME_AND_PRIVATE


def test_rollback_restores_the_preimage(env):
    base, new, ws, cfg, bk = env
    before = (ws / "AGENTS.md").read_bytes()
    P.main(args(env))
    assert P.main(["rollback", "--workspace", str(ws), "--backup-dir", str(bk)]) == 0
    assert (ws / "AGENTS.md").read_bytes() == before


def test_refuses_on_pinned_hash_drift(env):
    base, new, ws, cfg, bk = env
    a = args(env)
    (ws / "AGENTS.md").write_text("# Contract\nrules\n" + RUNTIME_AND_PRIVATE + "drift")
    with pytest.raises(SystemExit, match="refused: live AGENTS"):
        P.main(a)
    assert not bk.exists()


def test_refuses_on_config_drift(env):
    base, new, ws, cfg, bk = env
    a = args(env)
    cfg.write_text('{"changed": true}')
    with pytest.raises(SystemExit, match="refused: live config"):
        P.main(a)


def test_refuses_when_live_is_not_the_base_release(env):
    base, new, ws, cfg, bk = env
    (ws / "AGENTS.md").write_text("# Other\n" + RUNTIME_AND_PRIVATE)
    with pytest.raises(SystemExit, match="does not start with"):
        P.main(args(env))


def test_refuses_over_budget(tmp_path):
    base = release(tmp_path, "0.1.8", "a\n")
    new = release(tmp_path, "0.1.10", "a\n" + "x" * 50, budget=40)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "AGENTS.md").write_text("a\n")
    with pytest.raises(SystemExit, match="exceeds budget"):
        P.main(["plan", "--release", str(new), "--base-release", str(base), "--workspace", str(ws)])


def test_refuses_tampered_release(env):
    base, new, ws, cfg, bk = env
    (new / "AGENTS.md").write_text("tampered")
    with pytest.raises(SystemExit, match="manifest"):
        P.main(args(env))


def test_rollback_refuses_if_live_moved_on(env):
    base, new, ws, cfg, bk = env
    P.main(args(env))
    (ws / "AGENTS.md").write_text("successor")
    with pytest.raises(SystemExit, match="is not the projection"):
        P.main(["rollback", "--workspace", str(ws), "--backup-dir", str(bk)])
