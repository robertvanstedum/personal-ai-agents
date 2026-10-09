"""The release classifier must minimize restarts without guessing ownership."""

from pathlib import Path
from scripts.ci.classify_release import ALL_SERVICES, classify


def test_document_only_release_has_no_services():
    assert classify([
        "README.md",
        "docs/specs/spec_example_2026-08-16.md",
        "scripts/docs/render_key_doc.mjs",
    ]) == (
        "documents", ()
    )


def test_prototype_lab_and_planning_studio_are_document_releases():
    """A project home is briefs, specs and evidence; it restarts no service."""
    assert classify([
        "prototype-lab/projects/project-mini-moi-work-poc/START_HERE.md",
        "prototype-lab/projects/project-mini-moi-work-poc/docs/CASE_STUDY_AGENT_TEAM.md",
        "prototype-lab/projects/project-mini-moi-work-poc/project.yaml",
        "planning-studio/initiatives/INIT-2026-0001/README.md",
    ]) == ("documents", ())


def test_a_promoted_prototype_still_restarts_no_mini_moi_service():
    """A promoted prototype ships on its own tag workflow, not this pipeline."""
    assert classify([
        "prototype-lab/projects/project-iot-connect/Dockerfile",
        "prototype-lab/projects/project-iot-connect/app/main.py",
    ]) == ("documents", ())


def test_project_home_documents_do_not_mask_a_real_service_change():
    """A mixed push is classified by the service file, not by the documents."""
    release_class, services = classify([
        "prototype-lab/projects/project-mini-moi-work-poc/DECISIONS.md",
        "domains/cos/confer_service.py",
    ])
    assert release_class == "domain"
    assert services == ("cos-bot", "cos-scheduler")


def test_release_pipeline_changes_bootstrap_with_full_deployment():
    # The workflow and the deploy script change how every service is deployed,
    # so they still bootstrap with a full deployment. The classifier itself no
    # longer does (PR #248 review F2): it only chooses the service list, never
    # ships in an image, and CI runs this test file before any deploy, so a
    # classifier edit alone redeploys nothing
    # (test_the_classifier_itself_redeploys_nothing).
    for path in (
        ".github/workflows/deploy.yml",
        "scripts/operations/deploy_scoped_release.sh",
    ):
        assert classify([path]) == ("full", ALL_SERVICES)


def test_german_change_restarts_german_and_its_bot_only():
    assert classify(["domains/german/html_server.py"]) == (
        "domain", ("german", "system-bot")
    )


def test_cos_change_does_not_restart_language_domains():
    release_class, services = classify(["domains/cos/confer_service.py"])
    assert release_class == "domain"
    assert services == ("cos-bot", "cos-scheduler")
    assert "german" not in services
    assert "portuguese" not in services


def test_shared_voice_change_reaches_all_voice_consumers():
    assert classify(["core/realtime_voice/confer.py"]) == (
        "domain", ("german", "portuguese", "cos-scheduler")
    )


def test_curator_change_includes_system_bot_commands():
    assert classify(["domains/curator/curator_feedback.py"]) == (
        "domain", ("curator", "system-bot")
    )


def test_guild_context_change_includes_portal_and_cos_consumers():
    assert classify(["domains/guild/config/cos_context.json"]) == (
        "domain", ("portal", "cos-bot", "cos-scheduler")
    )


def test_unknown_path_falls_back_to_full_release():
    assert classify(["unexpected/runtime_file.py"]) == ("full", ALL_SERVICES)


def test_staging_only_changes_do_not_mask_a_real_service_change():
    """Mac staging files (issue #234) are release-only; a portal change beside
    them still deploys the portal."""
    assert classify([
        "docker-compose.staging.yml",
        "scripts/staging/build.sh",
        "services/model_gateway/litellm.staging.yaml",
    ]) == ("documents", ())
    assert classify([
        "scripts/staging/verify.sh",
        "minimoi_portal/app.py",
    ]) == ("domain", ("portal",))


def test_the_classifier_itself_redeploys_nothing():
    """Its own edits used to fall through to a full nine-service deploy (PR #248 review F2)."""
    assert classify(["scripts/ci/classify_release.py"]) == ("documents", ())
    assert classify(["scripts/ci/classify_release.py", "minimoi_portal/app.py"]) == ("domain", ("portal",))
    assert classify(["scripts/ci/other_script.py"]) == ("full", ALL_SERVICES)   # only the exact path


def test_dormant_master_craftsman_files_redeploy_nothing_until_a_service_uses_them():
    assert classify(["docker/mc-agent/openclaw.json", "docker/mc-agent/workspace/AGENTS.md",
                     "docker/mc-agent/mc-key-check.sh", "docker/mc-agent/start-mc.sh",
                     "docker/Dockerfile.mc-agent", "docker-compose.mc.yml"]) == ("documents", ())
    # Guard: the day production's deploy path uses MC's image or files, this
    # test fails and that change must classify them as a real service.
    root = Path(__file__).resolve().parent.parent
    users = []
    for path in (root / ".github/workflows/deploy.yml", root / "scripts/operations/deploy_scoped_release.sh",
                 root / "docker-compose.prod.yml", root / "docker-compose.yml", root / "scripts/staging/build.sh"):
        text = path.read_text()
        if "docker/mc-agent" in text or "Dockerfile.mc-agent" in text or "mc-agent" in text:
            users.append(path.name)
    assert users == []
    # Only MC's own Dockerfile builds from docker/mc-agent/.
    builders = sorted(p.name for p in (root / "docker").glob("Dockerfile*") if "docker/mc-agent" in p.read_text())
    assert builders == ["Dockerfile.mc-agent"]


def test_production_builds_exactly_the_classifier_services():
    """A new build entry in deploy.yml (for example MC under another name) must
    be a classified service first (PR #260 review F2)."""
    import re
    root = Path(__file__).resolve().parent.parent
    text = (root / ".github/workflows/deploy.yml").read_text()
    built = tuple(re.findall(r"^\s+([a-z][a-z-]*)\) dockerfile=", text, re.M))
    assert built == ALL_SERVICES
    listed = re.search(r'ALL_SERVICES="([^"]+)"', text).group(1).split()
    assert tuple(listed) == ALL_SERVICES


def test_the_workshop_backend_deploys_the_portal_only():
    """The Workshop backend's one consumer is the portal; its laptop tools run in no service."""
    backend = [
        "core/workshop_journal/journal.py", "core/vault_t1/reader.py", "utils/credential_scrub.py",
        "minimoi_portal/workshop/record.py", "scripts/workshop/workshop.py", "scripts/vault/vault.py",
        "tests/workshop_journal/test_journal.py", "tests/test_credential_scrub.py",
    ]
    assert classify(backend) == ("domain", ("portal",))
    assert classify(["scripts/workshop/workshop.py", "scripts/vault/vault.py"]) == ("documents", ())
    # The Guild screens' own helpers (agent-turn writer, scheduled-job judge, payment scrub) are portal-only too, and the
    # laptop tools that drive them run in no service.
    assert classify(["core/agent_turns/writer.py", "core/jobs/judge.py", "utils/payment_scrub.py"]) == ("domain", ("portal",))
    assert classify(["tools/workshop/topic_inbox.py", "scripts/release/exclude_reserve_pages.py"]) == ("documents", ())
    # Neighbours keep their old ownership: only these exact places moved.
    assert classify(["core/other_module.py"]) == ("domain", ("portal", "curator", "german", "portuguese", "system-bot", "cos-bot", "cos-scheduler"))
    assert classify(["scripts/other_tool.py"]) == ("full", ALL_SERVICES)


def test_only_the_exact_credential_scrub_file_moves_to_the_portal():
    assert classify(["utils/credential_scrub.py"]) == ("domain", ("portal",))
    assert classify(["utils/credential_scrub.py.bak"]) == classify(["utils/other_helper.py"])     # neighbours keep the shared rule
    assert classify(["utils/credential_scrub.pyc"])[1] == classify(["utils/other_helper.py"])[1]


# --- the ownership guard -------------------------------------------------------------------------------------------------
# The portal-only rule above is honest only while no other service imports the Workshop backend. The guard reads each file's
# imports with the parser (so formatting, aliases, parentheses and nesting do not matter) and also looks for string-named dynamic
# imports. Importing the portal's own facade (minimoi_portal.workshop) counts as using the backend, because the facade wraps it.
import ast

WORKSHOP_BACKEND_MODULES = ("core.workshop_journal", "core.vault_t1", "core.agent_turns", "core.jobs", "utils.credential_scrub",
                            "utils.payment_scrub", "minimoi_portal.workshop")
# Where an import of the backend is expected: its own code, the portal, laptop tools, tests, and project homes.
BACKEND_IMPORT_ALLOWED = ("core/workshop_journal/", "core/vault_t1/", "core/agent_turns/", "core/jobs/", "utils/credential_scrub.py",
                          "utils/payment_scrub.py", "minimoi_portal/", "scripts/workshop/", "scripts/vault/", "scripts/release/",
                          "tools/workshop/", "scripts/dev/", "tests/", "prototype-lab/", "planning-studio/")
SKIPPED_DIRS = {".git", "node_modules", "venv", "ai-env", ".venv", "_working", ".claude", "__pycache__"}


def _imported_names(source):
    """Every module name a file imports: ``import a.b``, ``from a import b`` (as a.b), ``from a.b import c``, and
    ``importlib.import_module("a.b")`` / ``__import__("a.b")`` with a literal name. Relative imports stay inside their package."""
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level:
            base = node.module or ""
            names.add(base)
            names.update(f"{base}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Call):
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if called in ("import_module", "__import__") and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    names.add(arg.value)
    return names


def _uses_workshop_backend(source):
    return any(name == module or name.startswith(module + ".")
               for name in _imported_names(source) for module in WORKSHOP_BACKEND_MODULES)


def _backend_importers(root):
    found = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if SKIPPED_DIRS.intersection(rel.split("/")) or rel.startswith(BACKEND_IMPORT_ALLOWED):
            continue
        try:
            if _uses_workshop_backend(path.read_text(errors="ignore")):
                found.append(rel)
        except SyntaxError:
            continue
    return found


def test_the_ownership_guard_sees_every_ordinary_way_to_import_the_backend(tmp_path):
    forms = {
        "direct": "import core.workshop_journal.journal\n",
        "aliased": "import core.vault_t1.reader as reader\n",
        "from_module": "from core.workshop_journal.brief import build\n",
        "from_package": "from core import workshop_journal\n",
        "from_package_many": "from core import (\n    get_secret,\n    workshop_journal as wj,\n)\n",
        "from_utils": "from utils import credential_scrub\n",
        "from_utils_module": "from utils.credential_scrub import scrub\n",
        "facade_class": "from minimoi_portal.workshop.record import Workshop\n",
        "facade_package": "from minimoi_portal import workshop\n",
        "facade_import": "import minimoi_portal.workshop.observer\n",
        "nested_in_function": "def late():\n    from core import workshop_journal\n    return workshop_journal\n",
        "importlib": "import importlib\nmod = importlib.import_module('core.workshop_journal.journal')\n",
        "dunder": "mod = __import__('core.vault_t1.reader')\n",
    }
    for name, source in forms.items():
        folder = tmp_path / name / "domains" / "cos"
        folder.mkdir(parents=True)
        (folder / "consumer.py").write_text(source)
        assert _backend_importers(tmp_path / name) == ["domains/cos/consumer.py"], name


def test_the_ownership_guard_ignores_what_is_not_an_import_or_is_allowed(tmp_path):
    quiet = {
        "domains/cos/mention.py": "# core.workshop_journal is documented elsewhere\nNAME = 'from core import workshop_journal'\n",
        "domains/cos/neighbours.py": "from core import get_secret\nfrom utils import other_helper\nfrom minimoi_portal import app\n",
        "domains/cos/relative.py": "from . import workshop_journal\n",
        "minimoi_portal/guild_ui/screen.py": "from core.workshop_journal import brief\n",
        "scripts/workshop/workshop.py": "from minimoi_portal.workshop.record import Workshop\n",
        "tests/workshop_journal/test_x.py": "import core.workshop_journal\n",
        "venv/lib/site.py": "import core.workshop_journal\n",
    }
    for rel, source in quiet.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)
    (tmp_path / "domains" / "cos" / "broken.py").write_text("def (:\n")              # unparseable files are skipped, not crashed on
    assert _backend_importers(tmp_path) == []


def test_no_other_service_imports_the_workshop_backend():
    """The guard for the portal-only rule, on the real repository: the day a CoS, language or bot file imports the Workshop
    backend (or its facade), this fails and that change must classify the new consumer."""
    assert _backend_importers(Path(__file__).resolve().parent.parent) == []
