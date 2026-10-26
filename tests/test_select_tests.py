"""CI's focused test set must never be smaller than what a change can break; any doubt runs the whole suite."""

from pathlib import Path

from scripts.ci.classify_release import classify
from scripts.ci.select_tests import CORE_TESTS, Repo, select

ROOT = Path(__file__).resolve().parent.parent


def _repo(tmp_path, files):
    for rel, text in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return Repo(root=tmp_path, files=sorted(files))


def _targets(tmp_path, files, changed, release_class="domain"):
    result = select(changed, release_class, repo=_repo(tmp_path, files))
    assert result["test_scope"] == "focused", result
    return set(result["test_targets"])


# Enough unrelated tests that a small selection stays under the whole-suite share.
FILLER = {f"tests/test_unrelated_{i}.py": "def test_x():\n    pass\n" for i in range(12)}


# --- the whole suite --------------------------------------------------------------------------------------------------
def test_full_releases_manual_and_nightly_runs_take_the_whole_suite():
    assert select(["minimoi_portal/app.py"], "full")["test_targets"] == ["tests/"]
    for event in ("workflow_dispatch", "schedule"):
        assert select(["docs/x.md"], "documents", event=event)["test_scope"] == "full"


def test_an_unknown_path_still_runs_the_whole_suite():
    """The classifier's unknown-path fallback is a full release, and a full release runs everything."""
    release_class, _ = classify(["unexpected/runtime_file.py"])
    assert select(["unexpected/runtime_file.py"], release_class)["test_scope"] == "full"


def test_a_change_no_test_can_be_tied_to_runs_the_whole_suite(tmp_path):
    repo = _repo(tmp_path, {"minimoi_portal/static/site.css": "body {}\n", **FILLER})
    assert select(["minimoi_portal/static/site.css"], "domain", repo=repo)["test_scope"] == "full"


def test_the_test_harness_runs_the_whole_suite(tmp_path):
    repo = _repo(tmp_path, {"tests/conftest.py": "", **FILLER})
    for path in ("tests/conftest.py", "requirements.test.txt"):
        assert select([path], "domain", repo=repo)["test_scope"] == "full"


def test_a_module_the_root_conftest_loads_runs_the_whole_suite(tmp_path):
    repo = _repo(tmp_path, {"tests/conftest.py": "import core.shared\n", "core/shared.py": "", **FILLER})
    assert select(["core/shared.py"], "domain", repo=repo)["test_scope"] == "full"


def test_a_selection_past_the_share_runs_the_whole_suite(tmp_path):
    files = {f"tests/test_{i}.py": "import core.base\n" for i in range(5)}
    files.update({"core/base.py": "", "tests/test_other.py": ""})
    assert select(["core/base.py"], "domain", repo=_repo(tmp_path, files))["test_scope"] == "full"


# --- what a focused set holds ------------------------------------------------------------------------------------------
def test_documents_run_only_the_core_and_tests_that_name_them(tmp_path):
    files = {"tests/test_runbook.py": "RUNBOOK = 'docs/runbooks/mc.md'\n", "docs/runbooks/mc.md": "", **FILLER}
    assert _targets(tmp_path, files, ["docs/runbooks/mc.md"], "documents") == {"tests/test_runbook.py"}
    assert _targets(tmp_path, files, ["docs/specs/other.md"], "documents") == set()


def test_a_changed_test_runs_itself_and_a_changed_conftest_its_folder(tmp_path):
    files = {"tests/test_a.py": "", "tests/cos/conftest.py": "", "tests/cos/test_c.py": "", **FILLER}
    assert _targets(tmp_path, files, ["tests/test_a.py"]) == {"tests/test_a.py"}
    assert _targets(tmp_path, files, ["tests/cos/conftest.py"]) == {"tests/cos/"}
    assert _targets(tmp_path, files, ["tests/test_deleted.py", "tests/test_a.py"]) == {"tests/test_a.py"}


def test_a_module_selects_every_test_that_reaches_it_through_imports(tmp_path):
    files = {
        "core/__init__.py": "", "core/base.py": "",
        "core/middle.py": "from core import base\n",
        "domains/cos/service.py": "from ..cos_helpers import x\nimport core.middle as m\n",
        "tests/test_direct.py": "from core.base import thing\n",
        "tests/test_transitive.py": "def test_x():\n    from domains.cos.service import run\n",
        "tests/test_dynamic.py": "import importlib\nmod = importlib.import_module('core.middle')\n",
        "tests/test_mentions_only.py": "# core.base is documented here\n",
        **FILLER,
    }
    assert _targets(tmp_path, files, ["core/base.py"]) == {"tests/test_direct.py", "tests/test_transitive.py", "tests/test_dynamic.py"}
    # A package's __init__ runs for every import beneath it.
    assert _targets(tmp_path, files, ["core/__init__.py"]) == {"tests/test_direct.py", "tests/test_transitive.py", "tests/test_dynamic.py"}


def test_bare_imports_through_sys_path_resolve_to_every_module_of_that_name(tmp_path):
    files = {"domains/german/html_server.py": "", "domains/portuguese/html_server.py": "",
             "tests/test_german.py": "import sys\nsys.path.insert(0, 'domains/german')\nfrom html_server import app\n",
             "tests/test_json.py": "import json\n", "scripts/json.py": "", **FILLER}
    assert _targets(tmp_path, files, ["domains/portuguese/html_server.py"]) == {"tests/test_german.py"}    # over-select, never miss
    # A bare standard-library name never resolves to a nested repository file.
    assert select(["scripts/json.py"], "domain", repo=_repo(tmp_path, files))["test_scope"] == "full"


def test_a_conftest_fixture_selects_only_the_tests_that_use_it(tmp_path):
    files = {
        "minimoi_portal/app.py": "",
        "tests/guild/conftest.py": "import pytest\n\n@pytest.fixture\ndef portal_client():\n    from minimoi_portal.app import app\n    return app\n",
        "tests/guild/test_uses.py": "def test_x(portal_client):\n    pass\n",
        "tests/guild/test_ignores.py": "def test_y():\n    pass\n",
        "tests/test_outside.py": "def test_z(portal_client):\n    pass\n",
        **FILLER,
    }
    assert _targets(tmp_path, files, ["minimoi_portal/app.py"]) == {"tests/guild/test_uses.py"}


def test_a_conftest_module_import_selects_its_folder(tmp_path):
    files = {"core/store.py": "", "tests/cos/conftest.py": "from core import store\n", "tests/cos/test_a.py": "", **FILLER}
    assert _targets(tmp_path, files, ["core/store.py"]) == {"tests/cos/"}


def test_test_helpers_pass_their_importers_on(tmp_path):
    files = {"core/store.py": "", "tests/guild/helpers.py": "from core import store\n",
             "tests/guild/test_a.py": "from helpers import build\n", **FILLER}
    assert _targets(tmp_path, files, ["core/store.py"]) == {"tests/guild/test_a.py"}


def test_a_file_is_selected_through_the_tests_and_readers_that_name_it(tmp_path):
    files = {
        "docker-compose.prod.yml": "services: {}\n",
        "tests/test_compose.py": "COMPOSE = ROOT / 'docker-compose.prod.yml'\n",
        "scripts/deploy.sh": "docker-compose -f docker-compose.prod.yml up -d\n",
        "tests/test_deploy_script.py": "SCRIPT = 'scripts/deploy.sh'\n",
        "scripts/commented.sh": "# docker-compose.prod.yml is described here\n",
        "tests/test_commented.py": "S = 'commented.sh'\n",
        "domains/cos/hint.py": "'''Mentions docker-compose.prod.yml in a docstring.'''\n",
        "core/reader.py": "PATH = 'docker-compose.prod.yml'\n",
        "core/reader_user.py": "from core import reader\n",
        "tests/test_reader.py": "from core import reader\n",
        "tests/test_reader_user.py": "from core import reader_user\n",
        "tests/test_hint.py": "from domains.cos import hint\n",
        **FILLER,
    }
    assert _targets(tmp_path, files, ["docker-compose.prod.yml"]) == {
        "tests/test_compose.py", "tests/test_deploy_script.py", "tests/test_reader.py"}


def test_a_shared_basename_needs_its_folder_and_a_whole_word(tmp_path):
    files = {"docker/cos-agent-a/AGENTS.md": "", "docker/mc-agent/AGENTS.md": "",
             "docker/cos-agent-a/lib.sh": "x\n",
             "tests/test_cos_agent.py": "P = ROOT / 'docker' / 'cos-agent-a' / 'AGENTS.md'\n",
             "tests/test_mc_agent.py": "P = ROOT / 'docker' / 'mc-agent' / 'AGENTS.md'\n",
             "tests/test_stdlib.py": "X = 'stdlib.sh'\n", "tests/test_lib.py": "X = 'lib.sh'\n", **FILLER}
    assert _targets(tmp_path, files, ["docker/cos-agent-a/AGENTS.md"], "documents") == {"tests/test_cos_agent.py"}
    assert _targets(tmp_path, files, ["docker/cos-agent-a/lib.sh"]) == {"tests/test_lib.py"}


# --- the real repository ------------------------------------------------------------------------------------------------
def test_the_core_tests_exist():
    for test in CORE_TESTS:
        assert (ROOT / test).is_file(), test


def test_pr_385_runs_a_focused_set_with_its_guards():
    """PR #385: compose, two ops scripts, a runbook, one test file, and a lock file."""
    paths = ["docker-compose.prod.yml", "docs/runbooks/mc_container_prod.md", "minimoi_portal/auth/.auth.lock",
             "scripts/credentials/sync_cos_runtime_secrets_from_ssm.sh", "scripts/operations/deploy_mc_container_ec2.sh",
             "tests/test_staging_environment.py"]
    result = select(paths, "domain")
    assert result["test_scope"] == "focused", result
    targets = set(result["test_targets"])
    assert set(CORE_TESTS) <= targets
    assert {"tests/test_staging_environment.py",                    # the changed test
            "tests/cos/test_cos_agent_a_production_config.py",      # names the secret sync
            "tests/test_auth_store_integrity.py",                   # imports auth.py, which owns .auth.lock
            "tests/test_library_intake_compose.py"} <= targets      # reads production Compose
    assert len(targets) < 0.2 * len(Repo().tests)


def test_the_workflow_runs_the_focused_set_only_when_told_and_the_whole_suite_otherwise():
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text()
    classify_job = workflow[workflow.index("  classify:"):workflow.index("  test:")]
    assert "scripts/ci/select_tests.py --release-class" in classify_job
    assert 'classify_release.py --base "$BEFORE"' in classify_job
    assert classify_job.count('echo "test_scope=full"') == 2                 # manual runs and rewritten history
    test_job = workflow[workflow.index("  test:"):workflow.index("  build-push:")]
    assert 'if [ "$TEST_SCOPE" = "focused" ] && [ -n "$TEST_TARGETS" ]; then' in test_job
    assert "pytest tests/ -v --junit-xml=data/reports/junit.xml --ignore=tests/smoke" in test_job.split("else", 1)[1]


def test_the_nightly_run_takes_the_whole_suite_on_main_and_deploys_nothing():
    nightly = (ROOT / ".github/workflows/nightly-tests.yml").read_text()
    assert "schedule:" in nightly and "workflow_dispatch:" in nightly and "ref: main" in nightly
    assert "pytest tests/ -v" in nightly and "file_defect.py" in nightly
    for deploying in ("aws", "ssm", "docker", "secrets.EC2"):
        assert deploying not in nightly


def test_a_conftest_outside_tests_is_never_selected(tmp_path):
    # prototype-lab/ keeps its own tests and dependencies; the suite CI runs is tests/ (26 Oct 2026, #393).
    files = {"core/shared.py": "", "prototype-lab/p/tests/conftest.py": "import core.shared\n",
             "tests/test_shared.py": "import core.shared\n", **FILLER}
    assert _targets(tmp_path, files, ["core/shared.py"]) == {"tests/test_shared.py"}
