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
        "domain", ("portal", "german", "portuguese", "cos-bot", "cos-scheduler")
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
        "core/workshop_journal/journal.py", "core/vault_t1/reader.py",
        "minimoi_portal/workshop/record.py", "scripts/workshop/workshop.py", "scripts/vault/vault.py",
        "tests/workshop_journal/test_journal.py", "tests/test_credential_scrub.py",
    ]
    assert classify(backend) == ("domain", ("portal",))
    assert classify(["scripts/workshop/workshop.py", "scripts/vault/vault.py"]) == ("documents", ())
    # The Guild screens' own helpers (agent-turn writer and scheduled-job judge) are portal-only too, and the
    # laptop tools that drive them run in no service.
    assert classify(["core/agent_turns/writer.py", "core/jobs/judge.py"]) == ("domain", ("portal",))
    assert classify(["tools/workshop/topic_inbox.py", "scripts/release/exclude_reserve_pages.py"]) == ("documents", ())
    # Neighbours keep their old ownership: only these exact places moved.
    assert classify(["core/other_module.py"]) == ("domain", ("portal", "curator", "german", "portuguese", "system-bot", "cos-bot", "cos-scheduler"))
    assert classify(["scripts/other_tool.py"]) == ("full", ALL_SERVICES)


def test_only_the_exact_scrub_files_reach_portal_and_cos_consumers():
    for path in ("utils/credential_scrub.py", "utils/payment_scrub.py"):
        assert classify([path]) == ("domain", ("portal", "cos-bot", "cos-scheduler"))
    assert classify(["utils/credential_scrub.py.bak"]) == classify(["utils/other_helper.py"])     # neighbours keep the shared rule
    assert classify(["utils/credential_scrub.pyc"])[1] == classify(["utils/other_helper.py"])[1]


# --- the ownership guard -------------------------------------------------------------------------------------------------
# The backend ownership above is honest only while no unclassified service imports it. The guard reads each file's
# imports with the parser (so formatting, aliases, parentheses and nesting do not matter) and also looks for string-named dynamic
# imports. Importing the portal's own facade (minimoi_portal.workshop) counts as using the backend, because the facade wraps it.
import ast

WORKSHOP_BACKEND_MODULES = ("core.workshop_journal", "core.vault_t1", "core.agent_turns", "core.jobs", "utils.credential_scrub",
                            "utils.payment_scrub", "minimoi_portal.workshop")
# Where an import of the backend is expected: its own code, the portal, laptop tools, tests, and project homes.
BACKEND_IMPORT_ALLOWED = ("core/workshop_journal/", "core/vault_t1/", "core/agent_turns/", "core/jobs/", "utils/credential_scrub.py",
                          "utils/payment_scrub.py", "minimoi_portal/", "scripts/workshop/", "scripts/vault/", "scripts/release/",
                          "tools/workshop/", "scripts/dev/", "tests/", "prototype-lab/", "planning-studio/")
# Exact new homes and only their actual backend dependencies, not a package/domain waiver.
BACKEND_IMPORT_EXPECTED = {
    "core/memory_ask/context.py": ("utils.credential_scrub", "utils.payment_scrub"),
    "core/memory_ask/querylog.py": ("core.agent_turns.writer",),
    "core/memory_backup/backup.py": ("core.vault_t1.reader", "core.vault_t1.portable", "core.jobs.batch_window"),
    "core/memory_backup/production.py": ("core.vault_t1.reader", "core.vault_t1.portable"),
    "core/memory_index/embedder.py": ("core.jobs.batch_window",),
    "core/memory_index/indexer.py": ("core.jobs.batch_window",),
    "core/memory_sync/versions.py": ("core.vault_t1.reader",),
    "core/memory_sync/sync.py": ("core.jobs", "core.vault_t1.reader"),
    "core/memory_sync/apply.py": ("core.jobs", "core.vault_t1.portable", "core.vault_t1.reader"),
    "core/memory_sync/mc_pull.py": ("core.jobs",),
    "core/memory_shelf/approvals.py": ("core.vault_t1.reader",),
    "core/memory_shelf/cli.py": ("core.vault_t1.reader",),
    "core/memory_shelf/watchers.py": ("core.jobs.batch_window",),
    "scripts/mc/morning.py": ("core.jobs.registry", "core.jobs.schedule", "core.jobs.status"),
    "scripts/mc/github_awareness.py": ("core.jobs.registry", "core.jobs.status"),
    "scripts/rooms/archive.py": ("core.jobs.registry", "core.jobs.schedule", "core.jobs.status"),
    "core/memory_shelf/render.py": ("utils.credential_scrub", "utils.payment_scrub"),
    "core/memory_shelf/report.py": ("core.jobs",),
    "scripts/jobs/watchdog.py": ("core.jobs",),
    "scripts/memory/daily_watch.py": ("core.jobs",),
    "scripts/memory/index_job.py": ("core.jobs",),
    # MC learning (Codex's MC runtime work, 24 Oct): portal-owned, reached through mc_tools_wiring and the MC tools
    # listener inside the portal image. Exact dependencies only.
    "core/mc_learning/annotation_actions.py": ("core.workshop_journal.errors",),
    "core/mc_learning/annotation_shelf.py": ("core.vault_t1.reader", "core.workshop_journal.errors", "core.workshop_journal.fsutil"),
    "core/mc_learning/corrections.py": ("core.workshop_journal.errors",),
    "core/mc_learning/floor_source.py": ("utils.credential_scrub", "utils.payment_scrub"),
    "core/mc_learning/statement_journal.py": ("core.workshop_journal.errors", "core.workshop_journal.fsutil"),
    "core/mc_learning/statement_shelf.py": ("core.vault_t1.reader", "core.workshop_journal.errors", "core.workshop_journal.fsutil"),
}
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
            used = {name for name in _imported_names(path.read_text(errors="ignore"))
                    if any(name == module or name.startswith(module + ".") for module in WORKSHOP_BACKEND_MODULES)}
            # AST records both package and imported leaf; judge the most specific dependency.
            used = {name for name in used if not any(other.startswith(name + ".") for other in used)}
            expected = BACKEND_IMPORT_EXPECTED.get(rel, ())
            if any(not any(name == module or name.startswith(module + ".") for module in expected) for name in used):
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
    """New production consumers or unexpected dependencies in the exact laptop/context homes must update ownership."""
    assert _backend_importers(Path(__file__).resolve().parent.parent) == []


# Guild request/limit helpers are used by the portal and the laptop agents.
# Keep their release ownership coupled to actual imports and startup wiring.
GUILD_MODULES = ("core.agent_requests", "core.limits", "services.agent_installer",
                 "scripts.agent_connection", "scripts.agent_requests")
GUILD_ALLOWED_PREFIXES = ("core/agent_requests/", "minimoi_portal/", "tests/",
                          "prototype-lab/projects/project-records-room-poc/tests/",
                          "services/rooms_connector/", "services/rooms_worker/",
                          "services/agent_installer/")
GUILD_ALLOWED_FILES = {"core/limits.py", "scripts/agent_connection.py", "scripts/agent_requests.py",
                       # MC tools served by the portal's MC tools listener (Codex's MC runtime work, 24 Oct).
                       "core/mc_tools/agent_contact.py", "core/mc_tools/approved_handoff.py", "core/mc_tools/task_watch.py",
                       # The supervision sync's relay of MC's front-door requests, served by the portal (27 Oct).
                       "core/supervision/relay.py",
                       # Host bridge (runs from the installed agent-bridge bundle on the Mac host; no production image).
                       "scripts/mc_host/agent_worker.py", "scripts/mc_host/claude_send_guard.py"}
SHARED_LIMIT_CONSUMERS = {"core/realtime_voice/budget.py"}
LOCAL_GUILD_ALLOWED_PREFIXES = ("tests/", "prototype-lab/projects/project-records-room-poc/tests/",
                                "services/rooms_connector/", "services/rooms_worker/", "services/agent_installer/")
LOCAL_GUILD_ALLOWED_FILES = {"scripts/agent_connection.py", "scripts/agent_requests.py"}


def _guild_importers(root):
    found = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if SKIPPED_DIRS.intersection(rel.split("/")):
            continue
        try:
            names = _imported_names(path.read_text(errors="ignore"))
        except SyntaxError:
            continue
        for module in GUILD_MODULES:
            if not any(name == module or name.startswith(module + ".") for name in names):
                continue
            if module == "core.limits" and rel in SHARED_LIMIT_CONSUMERS:
                continue
            # Core helpers may be consumed by the portal. Installer/CLI code is
            # documents-only, so even a new PORTAL consumer must trip the guard.
            prefixes, files = ((GUILD_ALLOWED_PREFIXES, GUILD_ALLOWED_FILES) if module.startswith("core.")
                               else (LOCAL_GUILD_ALLOWED_PREFIXES, LOCAL_GUILD_ALLOWED_FILES))
            if not rel.startswith(prefixes) and rel not in files:
                found.append(rel)
                break
    return found


def test_guild_backend_and_laptop_tools_keep_the_release_on_portal():
    assert classify(["core/agent_requests/ledger.py",
                     "scripts/agent_requests.py", "scripts/agent_connection.py",
                     "services/agent_installer/installer.py"]) == ("domain", ("portal",))
    assert classify(["scripts/agent_connection.py", "services/agent_installer/installer.py"]) == ("documents", ())
    assert classify(["core/limits.py"]) == ("domain", ("portal", "cos-bot", "cos-scheduler"))
    assert classify(["core/realtime_voice/budget.py"]) == ("domain", ("portal", "german", "portuguese", "cos-bot", "cos-scheduler"))
    # Similar names do not inherit this exception.
    assert classify(["core/limits_extra.py"])[1] == ALL_SERVICES[:7]
    assert classify(["scripts/agent_connection_extra.py"]) == ("full", ALL_SERVICES)
    assert classify(["services/agent_installer_extra/start.py"]) == ("full", ALL_SERVICES)


def test_guild_guard_detects_new_consumers_even_with_dynamic_or_aliased_imports(tmp_path):
    forms = ["from core import limits as caps", "from core.agent_requests import ledger",
             "import services.agent_installer.installer as installer",
             "import importlib; importlib.import_module('core.agent_requests.connection')",
             "from scripts import agent_connection"]
    for index, source in enumerate(forms):
        root = tmp_path / str(index)
        target = root / "domains" / "cos" / "consumer.py"
        target.parent.mkdir(parents=True)
        target.write_text(source)
        assert _guild_importers(root) == ["domains/cos/consumer.py"]


def test_guild_runtime_has_no_unclassified_production_consumer():
    root = Path(__file__).resolve().parent.parent
    assert _guild_importers(root) == []
    markers = ("agent_installer", "agent_connection.py", "agent_requests.py",
               "core.agent_requests", "core/agent_requests", "core.limits", "core/limits.py",
               "rooms_worker", "rooms_connector", "rooms-worker", "rooms-connector")
    # A new production entrypoint/mount must update service ownership too.
    paths = [root / ".github/workflows/deploy.yml", root / "docker-compose.prod.yml",
             root / "docker-compose.yml", root / "scripts/operations/deploy_scoped_release.sh"]
    paths.extend(p for p in (root / "docker").rglob("*") if p.is_file()
                 and p.name not in {"Dockerfile.rooms-worker", "Dockerfile.rooms-codex", "requirements.rooms-worker.txt"}
                 and "mc-agent" not in str(p))
    violations = [(str(p.relative_to(root)), marker) for p in paths for marker in markers if marker in p.read_text(errors="ignore")]
    assert violations == []


def test_portal_cannot_silently_start_using_documents_only_agent_tools(tmp_path):
    for module in ("services.agent_installer.installer", "scripts.agent_connection", "scripts.agent_requests"):
        path = tmp_path / "minimoi_portal" / "consumer.py"
        path.parent.mkdir(exist_ok=True)
        path.write_text(f"import {module}\n")
        assert _guild_importers(tmp_path) == ["minimoi_portal/consumer.py"]
    path.write_text("from core.agent_requests import ledger\nfrom core import limits\n")
    assert _guild_importers(tmp_path) == []


# The MC delegation runner: the portal reads four exact files (through its dispatcher and core/agent_requests); the rest
# runs only in the Mac runner's own bundle. Keep that ownership coupled to actual imports, as for the Guild helpers.
MC_RUNNER = "core.mc_delegation_runner"
MC_DISPATCHER = "core.mc_delegation_dispatcher"
# Portal-owned homes that may read the runner's portal files (and nothing else of the runner).
MC_RUNNER_PORTAL_HOMES = ("minimoi_portal/", "core/mc_delegation_dispatcher/", "core/agent_requests/")
# Who may import the portal's dispatcher: the portal, the dispatcher itself, and the Mac runner's client of its door.
MC_DISPATCHER_HOMES = ("minimoi_portal/", "core/mc_delegation_dispatcher/")
MC_DISPATCHER_FILES = {"core/mc_delegation_runner/cli.py",
                       "core/mc_delegation_runner/host.py"}     # the door's one URL allowlist (S1-D review N4)


def _runner_names(rel, source):
    """``_imported_names`` plus the package's own relative imports, resolved, so a portal file's ``from . import x`` counts."""
    names = _imported_names(source)
    if rel.startswith("core/mc_delegation_runner/"):
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                base = MC_RUNNER + (f".{node.module}" if node.module else "")
                names.add(base)
                names.update(f"{base}.{alias.name}" for alias in node.names)
    return names


def _mc_runner_importers(root):
    from scripts.ci.classify_release import MC_RUNNER_PORTAL_FILES
    portal_modules = tuple(MC_RUNNER + "." + Path(f).stem for f in MC_RUNNER_PORTAL_FILES if not f.endswith("__init__.py"))
    found = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if SKIPPED_DIRS.intersection(rel.split("/")) or rel.startswith(("tests/", "prototype-lab/", "planning-studio/")):
            continue
        try:
            names = _runner_names(rel, path.read_text(errors="ignore"))
        except SyntaxError:
            continue
        used = {n for n in names if n == MC_RUNNER or n.startswith(MC_RUNNER + ".")}
        used = {n for n in used if not any(other.startswith(n + ".") for other in used)}      # judge the most specific module
        if rel.startswith("core/mc_delegation_runner/") and rel not in MC_RUNNER_PORTAL_FILES:
            allowed = (MC_RUNNER,)                                   # a runner-only file may use the whole package
        elif rel in MC_RUNNER_PORTAL_FILES or rel.startswith(MC_RUNNER_PORTAL_HOMES):
            allowed = portal_modules                                 # the portal side reaches only the portal files
        else:
            allowed = ()                                             # no other production code imports the runner
        if any(not any(n == m or n.startswith(m + ".") for m in allowed) for n in used):
            found.append(rel)
            continue
        if (any(n == MC_DISPATCHER or n.startswith(MC_DISPATCHER + ".") for n in names)
                and not rel.startswith(MC_DISPATCHER_HOMES) and rel not in MC_DISPATCHER_FILES):
            found.append(rel)                                        # the dispatcher carries the runner's portal files
    return found


def test_mc_runner_files_redeploy_only_the_portal_or_nothing():
    from scripts.ci.classify_release import MC_RUNNER_LOCAL_FILES, MC_RUNNER_PORTAL_FILES
    on_disk = {p.relative_to(Path(__file__).resolve().parent.parent).as_posix()
               for p in (Path(__file__).resolve().parent.parent / "core" / "mc_delegation_runner").glob("*.py")}
    assert MC_RUNNER_PORTAL_FILES | MC_RUNNER_LOCAL_FILES == on_disk                  # every runner file has a decided owner
    assert not MC_RUNNER_PORTAL_FILES & MC_RUNNER_LOCAL_FILES
    for path in MC_RUNNER_PORTAL_FILES:
        assert classify([path]) == ("domain", ("portal",)), path
    for path in MC_RUNNER_LOCAL_FILES:
        assert classify([path]) == ("documents", ()), path
    # Similar names, and new neighbours, keep the conservative core fallback.
    assert classify(["core/mc_delegation_runner/new_module.py"])[1] == ALL_SERVICES[:7]
    assert classify(["core/mc_delegation_runner/launch_env.py.bak"])[1] == ALL_SERVICES[:7]
    assert classify(["core/mc_delegation_runner_extra/workspace.py"])[1] == ALL_SERVICES[:7]


def test_the_agent_child_environment_change_redeploys_only_the_portal():
    assert classify(["core/agent_requests/claude_stream.py", "core/agent_requests/codex_app_server.py",
                     "core/mc_delegation_runner/launch_env.py", "core/mc_delegation_runner/workspace.py",
                     "scripts/ci/classify_release.py", "tests/mc_delegation_runner/test_launch_env.py"]) == ("domain", ("portal",))


def test_mc_runner_has_no_unclassified_production_consumer():
    root = Path(__file__).resolve().parent.parent
    assert _mc_runner_importers(root) == []
    # No production entrypoint starts the runner: it runs only from the Mac's own bundle.
    paths = [root / ".github/workflows/deploy.yml", root / "docker-compose.prod.yml",
             root / "docker-compose.yml", root / "scripts/operations/deploy_scoped_release.sh"]
    paths.extend(p for p in (root / "docker").rglob("*") if p.is_file())
    assert [str(p.relative_to(root)) for p in paths if "mc_delegation_runner" in p.read_text(errors="ignore")] == []


def test_mc_runner_guard_detects_new_consumers_and_runner_only_files_reaching_the_portal(tmp_path):
    allowed = {"core/mc_delegation_dispatcher/store.py": "from core.mc_delegation_runner import envelope as env_mod\n",
               "core/mc_delegation_dispatcher/door.py": "def late():\n    from core.mc_delegation_runner.dispatcher_adapter import output_of\n",
               "core/agent_requests/claude_stream.py": "from core.mc_delegation_runner import launch_env\n",
               "minimoi_portal/guild_ui/jobs_wiring.py": "from core.mc_delegation_dispatcher.store import DispatcherStore\n",
               "core/mc_delegation_runner/workspace.py": "from core.mc_delegation_runner import launch_env\n",
               "core/mc_delegation_runner/runner.py": "from . import commands, envelope as env_mod, workspace\n",
               "core/mc_delegation_runner/cli.py": "from . import runner\nfrom core.mc_delegation_dispatcher.door import HttpDispatcher\n"}
    for rel, source in allowed.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)
    assert _mc_runner_importers(tmp_path) == []
    violations = {"domains/cos/consumer.py": "from core.mc_delegation_runner import launch_env\n",
                  "domains/german/aliased.py": "import core.mc_delegation_runner.envelope as e\n",
                  "services/usage/dynamic.py": "import importlib\nm = importlib.import_module('core.mc_delegation_runner.cli')\n",
                  "domains/curator/dispatcher.py": "from core.mc_delegation_dispatcher import store\n",
                  "minimoi_portal/runner_only.py": "from core.mc_delegation_runner import workspace\n",
                  "core/agent_requests/wider.py": "from core.mc_delegation_runner.runner import run_once\n",
                  "core/mc_delegation_runner/launch_env.py": "from . import workspace\n"}
    for rel, source in violations.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        original = target.read_text() if target.exists() else None
        target.write_text(source)
        assert _mc_runner_importers(tmp_path) == [rel], rel
        if original is None:
            target.unlink()
        else:
            target.write_text(original)


def test_vault_release_ownership_separates_shared_context_and_portal_reports_from_local_ask():
    from scripts.ci.classify_release import VAULT_COS_FILES, VAULT_LOCAL_FILES, VAULT_SERVER_FILES
    for path in VAULT_COS_FILES:
        assert classify([path]) == ("domain", ("portal", "cos-bot", "cos-scheduler"))
    for path in (VAULT_LOCAL_FILES - VAULT_SERVER_FILES) | {"core/memory_shelf/inbox.py", "core/memory_shelf/watchers.py",
                                                            "core/memory_backup/backup.py", "core/memory_backup/production.py"}:
        assert classify([path]) == ("documents", ())
    # The production Vault Ask service runs the portal image: its modules redeploy the portal (17 Oct 2026).
    for path in VAULT_SERVER_FILES - VAULT_COS_FILES:
        assert classify([path]) == ("domain", ("portal",)), path
    assert classify(["core/memory_ask/new_consumer.py"])[1] == ALL_SERVICES[:7]
    assert classify(["core/memory_ask/context.py.bak"])[1] == ALL_SERVICES[:7]
    assert classify(["scripts/unclassified_vault_tool.py"]) == ("full", ALL_SERVICES)


def test_expected_vault_backend_homes_do_not_waive_unrelated_dependencies(tmp_path):
    for rel, modules in BACKEND_IMPORT_EXPECTED.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join("import " + module for module in modules))
    assert _backend_importers(tmp_path) == []
    for rel, modules in BACKEND_IMPORT_EXPECTED.items():
        target = tmp_path / rel
        original = target.read_text()
        # A bare package is pruned beside a more specific import, so inject a leaf this home does not expect.
        unrelated = next(m for m in ("core.workshop_journal.journal", "utils.payment_scrub")
                         if not any(m == e or m.startswith(e + ".") for e in modules))
        target.write_text(original + f"\nimport {unrelated}\n")
        assert _backend_importers(tmp_path) == [rel]
        target.write_text(original)


def _vault_runtime_importers(root):
    """Exact report readers may serve portal metadata; local Ask/custody stays laptop-only."""
    from scripts.ci.classify_release import VAULT_COS_FILES, VAULT_LOCAL_FILES, VAULT_LOCAL_PREFIXES, LOCAL_TOOL_FILES, REGRESSION_REPORT_FILES
    packages = ("core.memory_ask", "core.memory_shelf", "core.memory_index", "core.memory_backup", "core.workshop_journal.vault_export") + tuple(
        path[:-3].replace("/", ".") for path in LOCAL_TOOL_FILES)
    expected_host = {"domains/cos/vault_context.py": ("core.memory_ask.consumer",),
                     "minimoi_portal/guild_ui/mc_regression.py": ("core.memory_ask.regression", "core.memory_ask.runner"),
                     # MC learning, portal-owned; only shelf modules that ship in the portal image (VAULT_SERVER_FILES).
                     "core/mc_learning/annotation_shelf.py": ("core.memory_shelf.bundle", "core.memory_shelf.codes", "core.memory_shelf.sessions",
                                                              "core.memory_shelf.shelf", "core.memory_shelf.ulid"),
                     "core/mc_learning/corrections.py": ("core.memory_shelf.render",),
                     "core/mc_learning/floor_source.py": ("core.memory_shelf.render",),
                     "core/mc_learning/statement_journal.py": ("core.memory_shelf.fsio", "core.memory_shelf.record", "core.memory_shelf.render",
                                                               "core.memory_shelf.ulid"),
                     "core/mc_learning/statement_shelf.py": ("core.memory_shelf.bundle", "core.memory_shelf.codes", "core.memory_shelf.render",
                                                             "core.memory_shelf.sessions", "core.memory_shelf.shelf", "core.memory_shelf.ulid"),
                     "core/mc_learning/statements.py": ("core.memory_shelf.render",)}
    report_dependencies = {
        "core/memory_ask/regression.py": ("core.memory_ask.runner",),
        "core/memory_ask/runner.py": ("core.memory_ask.consumer", "core.memory_ask.context", "core.memory_ask.citation"),
        "core/memory_ask/assessment.py": ("core.memory_ask.citation", "core.memory_ask.regression", "core.memory_ask.runner"),
    }
    assert set(report_dependencies) == set(REGRESSION_REPORT_FILES)
    found = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if SKIPPED_DIRS.intersection(rel.split("/")) or rel.startswith(("tests/", "scripts/memory/", "scripts/vault/", "prototype-lab/", "planning-studio/")):
            continue
        if rel in VAULT_LOCAL_FILES or rel in LOCAL_TOOL_FILES or rel.startswith(VAULT_LOCAL_PREFIXES):
            continue
        try:
            names = _imported_names(path.read_text(errors="ignore"))
        except SyntaxError:
            continue
        # from package import module reports both names; check the specific module
        # when available, but a bare package dependency is conservatively rejected.
        used = {name for name in names if any(name == module or name.startswith(module + ".") for module in packages)}
        used = {name for name in used if not any(other.startswith(name + ".") for other in used)}
        allowed = (tuple("core.memory_ask." + Path(file).stem for file in VAULT_COS_FILES) if rel in VAULT_COS_FILES
                   else report_dependencies.get(rel, expected_host.get(rel, ())))
        if any(not any(name == module or name.startswith(module + ".") for module in allowed) for name in used):
            found.append(rel)
    return found


def test_vault_runtime_has_no_unclassified_production_consumer():
    assert _vault_runtime_importers(Path(__file__).resolve().parent.parent) == []


def test_vault_guard_rejects_production_laptop_retrieval_and_scrub_dependency_expansion(tmp_path):
    paths = {"domains/cos/vault_context.py": "from core.memory_ask.consumer import retrieve",
             "core/memory_ask/context.py": "from core.memory_ask import citation"}
    for rel, source in paths.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)
    assert _vault_runtime_importers(tmp_path) == []
    for rel in ("domains/cos/vault_context.py", "core/memory_ask/context.py", "minimoi_portal/new_reader.py", "domains/german/new_reader.py"):
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        original = target.read_text() if target.exists() else ""
        target.write_text(original + "\nfrom core.memory_ask import querylog\n")
        assert _vault_runtime_importers(tmp_path) == [rel]
        target.write_text(original)


def test_laptop_workshop_export_seam_cannot_silently_become_a_portal_consumer(tmp_path):
    target = tmp_path / "minimoi_portal" / "exporter.py"
    target.parent.mkdir(parents=True)
    target.write_text("from core.workshop_journal import vault_export\n")
    assert _vault_runtime_importers(tmp_path) == ["minimoi_portal/exporter.py"]


def test_only_reviewed_laptop_entrypoints_are_release_only():
    from scripts.ci.classify_release import LOCAL_TOOL_FILES
    assert LOCAL_TOOL_FILES == {"scripts/mc/morning.py", "scripts/mc/regression.py", "scripts/rooms/archive.py"}
    for path in LOCAL_TOOL_FILES:
        assert classify([path]) == ("documents", ())
        assert classify([path.removesuffix(".py") + "_extra.py"]) == ("full", ALL_SERVICES)
    for path in ("core/memory_ask/assessment.py", "core/memory_ask/regression.py", "core/memory_ask/runner.py"):
        assert classify([path]) == ("domain", ("portal",))
        assert classify([path.removesuffix(".py") + "_extra.py"])[1] == ALL_SERVICES[:7]


def test_exact_laptop_files_cannot_become_production_dependencies(tmp_path):
    for module in ("core.memory_ask.assessment", "core.memory_ask.regression", "core.memory_ask.runner",
                   "scripts.mc.morning", "scripts.mc.regression", "scripts.rooms.archive"):
        target = tmp_path / "domains/cos/new_consumer.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("import " + module + "\n")
        assert _vault_runtime_importers(tmp_path) == ["domains/cos/new_consumer.py"]
        target.unlink()


def test_precise_laptop_backend_imports_refuse_new_dependencies(tmp_path):
    sources = {
        "core/memory_backup/backup.py": "from core.jobs import batch_window\nfrom core.vault_t1 import portable\nfrom core.vault_t1.reader import Vault\n",
        "core/memory_index/embedder.py": "from core.jobs import batch_window\n",
        "core/memory_shelf/approvals.py": "from core.vault_t1 import reader\n",
        "scripts/mc/morning.py": "from core.jobs import registry, schedule, status\n",
        "scripts/rooms/archive.py": "from core.jobs import registry, schedule, status\n",
    }
    for rel, source in sources.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)
    assert _backend_importers(tmp_path) == []
    for rel, source in sources.items():
        target = tmp_path / rel
        target.write_text(source + "from core.jobs import unrelated_new_module\n")
        assert _backend_importers(tmp_path) == [rel]
        target.write_text(source)
    nested = tmp_path / "scripts/mc/nested/morning.py"
    nested.parent.mkdir(parents=True)
    nested.write_text(sources["scripts/mc/morning.py"])
    assert _backend_importers(tmp_path) == ["scripts/mc/nested/morning.py"]


def test_exact_regression_report_family_and_host_imports_keep_local_ask_denied(tmp_path):
    from scripts.ci.classify_release import REGRESSION_REPORT_FILES, VAULT_LOCAL_FILES
    assert REGRESSION_REPORT_FILES == {"core/memory_ask/regression.py", "core/memory_ask/runner.py", "core/memory_ask/assessment.py"}
    assert not(REGRESSION_REPORT_FILES & VAULT_LOCAL_FILES)
    for rel in REGRESSION_REPORT_FILES:
        assert classify([rel]) == ("domain", ("portal",))
        assert classify([rel.removesuffix(".py")+"_extra.py"])[1] == ALL_SERVICES[:7]
    host=tmp_path/"minimoi_portal/guild_ui/mc_regression.py"
    host.parent.mkdir(parents=True)
    original="from core.memory_ask import regression, runner\n"
    host.write_text(original)
    assert _vault_runtime_importers(tmp_path) == []
    for module in ("service", "querylog"):
        host.write_text(original+"from core.memory_ask import "+module+"\n")
        assert _vault_runtime_importers(tmp_path) == ["minimoi_portal/guild_ui/mc_regression.py"]
    host.write_text(original+"from core.memory_index import search\n")
    assert _vault_runtime_importers(tmp_path) == ["minimoi_portal/guild_ui/mc_regression.py"]
    host.write_text(original)
    sibling=host.with_name("other_report.py");sibling.write_text(original)
    assert _vault_runtime_importers(tmp_path) == ["minimoi_portal/guild_ui/other_report.py"]


def test_report_reader_dependencies_are_exact_without_ask_querylog_or_index(tmp_path):
    sources={
        "core/memory_ask/regression.py":"from core.memory_ask import runner\n",
        "core/memory_ask/runner.py":"from core.memory_ask import consumer, context, citation\n",
        "core/memory_ask/assessment.py":"from core.memory_ask import citation, regression, runner\n",
    }
    for rel,source in sources.items():
        target=tmp_path/rel;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(source)
    assert _vault_runtime_importers(tmp_path) == []
    for rel,source in sources.items():
        target=tmp_path/rel
        for forbidden in ("from core.memory_ask import service\n", "from core.memory_ask import querylog\n", "from core.memory_index import search\n"):
            target.write_text(source+forbidden)
            assert _vault_runtime_importers(tmp_path) == [rel]
            target.write_text(source)


def test_the_vault_sync_rebuilds_the_portal_image_its_index_step_runs_in():
    from scripts.ci.classify_release import classify
    for path in ("core/memory_sync/apply.py", "core/memory_sync/versions.py", "scripts/vault/vault_apply.py"):
        assert classify([path])[1] == ("portal",), path
    assert classify(["scripts/vault/vault_sync.py"])[1] == ()                    # the Mac job: no service image


def test_the_mc_exporter_rebuilds_the_portal_image_vault_mc_export_runs_it_in():
    from scripts.ci.classify_release import classify
    assert classify(["scripts/vault/export_mc_conversations.py"]) == ("domain", ("portal",))   # docker exec minimoi-portal ... --stdout-tar
    assert classify(["scripts/vault/vault_mc_export.py"])[1] == ()               # the EC2 host job, from the tools bundle: no image
    for path in ("core/memory_sync/mc_batch.py", "core/memory_sync/mc_export.py"):
        assert classify([path])[1] == ("portal",), path


# --- production Compose: only the services whose own block changed (26 Oct 2026) ----------------------------------------
from scripts.ci.classify_release import COMPOSE_SERVICE_OWNERS, compose_changed_services, git_compose_diff

COMPOSE = """\
x-hardening: &hardening
  security_opt: [no-new-privileges:true]
services:
  postgres:
    image: postgres:latest
  model-gateway:
    image: gateway:${MINIMOI_IMAGE_TAG:-latest}
    healthcheck: {start_period: 20s}
  portal:
    <<: *hardening
    image: portal:${MINIMOI_IMAGE_TAG:-latest}
    environment: [A=1]
  vault-ask:
    profiles: [vault]
    image: portal:${MINIMOI_IMAGE_TAG:-latest}
networks:
  default: {}
"""


def test_a_compose_change_restarts_only_the_services_whose_block_changed():
    gateway = COMPOSE.replace("start_period: 20s", "start_period: 120s")
    assert compose_changed_services(COMPOSE, gateway) == ("model-gateway",)
    both = gateway.replace("environment: [A=1]", "environment: [A=1, B=2]")
    assert compose_changed_services(COMPOSE, both) == ("portal", "model-gateway")
    # vault-ask runs the portal image; the scoped deploy recreates it beside the portal.
    assert compose_changed_services(COMPOSE, COMPOSE.replace("profiles: [vault]", "profiles: [vault, x]")) == ("portal",)
    # A shared anchor changes every service that merges it, and only those.
    assert compose_changed_services(COMPOSE, COMPOSE.replace("no-new-privileges:true", "no-new-privileges:false")) == ("portal",)


def test_named_volumes_and_networks_belong_to_the_services_that_use_them():
    """PR #330 added a named volume for the portal: that restarts the portal, not all nine."""
    volume = (COMPOSE.replace("    environment: [A=1]\n", "    environment: [A=1]\n    volumes: [mc-observations:/app/runtime/mc-observations]\n")
              .replace("networks:\n", "volumes:\n  mc-observations:\nnetworks:\n"))
    assert compose_changed_services(COMPOSE, volume) == ("portal",)
    # Bind mounts are not named volumes; an unused top-level definition restarts nothing.
    bind = COMPOSE.replace("    environment: [A=1]\n", "    environment: [A=1]\n    volumes: [/opt/minimoi/data:/app/data:ro]\n")
    assert compose_changed_services(COMPOSE, bind) == ("portal",)
    unused = COMPOSE.replace("networks:\n", "volumes:\n  spare:\nnetworks:\n")
    assert compose_changed_services(COMPOSE, unused) == ()
    # A network only the gateway joins restarts the gateway; changing it touches only its users.
    joined = (COMPOSE.replace("    healthcheck: {start_period: 20s}\n", "    healthcheck: {start_period: 20s}\n    networks: [default, mc]\n")
              .replace("  default: {}\n", "  default: {}\n  mc: {internal: true}\n"))
    assert compose_changed_services(COMPOSE, joined) == ("model-gateway",)
    assert compose_changed_services(joined, joined.replace("mc: {internal: true}", "mc: {internal: false}")) == ("model-gateway",)


def test_comments_and_layout_in_compose_restart_nothing():
    edited = COMPOSE.replace("  portal:\n", "  # the portal\n  portal:\n").replace("environment: [A=1]", "environment:\n      - A=1")
    assert compose_changed_services(COMPOSE, edited) == ()
    assert classify(["docker-compose.prod.yml"], compose_diff=lambda: ()) == ("documents", ())


def test_compose_changes_the_classifier_cannot_own_stay_full():
    cases = {
        "postgres": COMPOSE.replace("postgres:latest", "postgres:17"),                         # the scoped deploy never recreates it
        "new service": COMPOSE.replace("networks:\n", "  mc:\n    image: mc\nnetworks:\n"),
        "removed service": COMPOSE.replace("  vault-ask:\n    profiles: [vault]\n    image: portal:${MINIMOI_IMAGE_TAG:-latest}\n", ""),
        "top-level key": COMPOSE.replace("networks:\n", "name: other\nnetworks:\n"),
        "postgres's network": COMPOSE.replace("default: {}", "default: {driver: bridge}"),       # every service is on default
        "unparseable": "services: [",
        "not a mapping": "- a list\n",
    }
    for name, head in cases.items():
        assert compose_changed_services(COMPOSE, head) is None, name
    assert compose_changed_services(None, COMPOSE) is None
    assert compose_changed_services(COMPOSE, None) is None


def test_classify_uses_the_compose_diff_and_falls_back_to_full_without_one():
    assert classify(["docker-compose.prod.yml"]) == ("full", ALL_SERVICES)
    assert classify(["docker-compose.prod.yml"], compose_diff=lambda: None) == ("full", ALL_SERVICES)
    assert classify(["docker-compose.prod.yml"], compose_diff=lambda: ("model-gateway",)) == ("domain", ("model-gateway",))
    # An unknown path beside it still forces the full release.
    assert classify(["docker-compose.prod.yml", "unexpected/x.py"], compose_diff=lambda: ("portal",)) == ("full", ALL_SERVICES)


def test_pr_385_deploys_the_portal_and_the_gateway_not_all_nine():
    """PR #385: compose (gateway env_file + start_period, portal env), two ops scripts, a runbook, a test, and a lock file."""
    paths = ["docker-compose.prod.yml", "docs/runbooks/mc_container_prod.md", "minimoi_portal/auth/.auth.lock",
             "scripts/credentials/sync_cos_runtime_secrets_from_ssm.sh", "scripts/operations/deploy_mc_container_ec2.sh",
             "tests/test_staging_environment.py"]
    assert classify(paths) == ("full", ALL_SERVICES)                                               # before: no compose diff
    assert classify(paths, compose_diff=lambda: ("portal", "model-gateway")) == ("domain", ("portal", "model-gateway"))


def test_git_compose_diff_reads_both_commits(tmp_path):
    import subprocess
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "ci@example.invalid")
    git("config", "user.name", "ci")
    (tmp_path / "docker-compose.prod.yml").write_text(COMPOSE)
    git("add", ".")
    git("commit", "-qm", "base")
    (tmp_path / "docker-compose.prod.yml").write_text(COMPOSE.replace("start_period: 20s", "start_period: 120s"))
    git("commit", "-qam", "head")
    assert git_compose_diff("HEAD^", "HEAD", cwd=tmp_path)() == ("model-gateway",)
    assert git_compose_diff("no-such-commit", "HEAD", cwd=tmp_path)() is None


def test_every_production_compose_service_has_an_owner_except_postgres():
    import yaml
    root = Path(__file__).resolve().parent.parent
    text = (root / "docker-compose.prod.yml").read_text()
    services = set(yaml.safe_load(text)["services"])
    assert services - set(COMPOSE_SERVICE_OWNERS) == {"postgres"}
    assert compose_changed_services(text, text) == ()


# --- operator scripts no service image runs ----------------------------------------------------------------------------
def test_ops_only_scripts_are_release_only():
    for path in ("scripts/credentials/sync_cos_runtime_secrets_from_ssm.sh", "scripts/credentials/credential_manager.py",
                 "scripts/credentials/ensure_model_gateway_key.py", "scripts/operations/deploy_mc_container_ec2.sh",
                 "scripts/operations/mc_prod_db_migrate.sh"):
        assert classify([path]) == ("documents", ()), path
    # Neighbours keep the full fallback.
    for path in ("scripts/credentialsx/tool.sh", "scripts/operations/deploy_mc_container_ec2_extra.sh",
                 "scripts/operations/other.sh"):
        assert classify([path]) == ("full", ALL_SERVICES), path


def test_no_service_image_runs_the_ops_only_scripts():
    """The day a service imports, copies or starts one, it must be classified as that service."""
    root = Path(__file__).resolve().parent.parent
    importers = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if SKIPPED_DIRS.intersection(rel.split("/")) or rel.startswith(("tests/", "scripts/credentials/")):
            continue
        try:
            names = _imported_names(path.read_text(errors="ignore"))
        except SyntaxError:
            continue
        if any(name == "scripts.credentials" or name.startswith("scripts.credentials.") for name in names):
            importers.append(rel)
    assert importers == []
    markers = ("scripts/credentials", "credential_manager", "ensure_model_gateway_key", "ensure_cos_agent_a_token",
               "deploy_mc_container_ec2", "mc_prod_db_migrate")
    def code(path):
        return "\n".join(line for line in path.read_text(errors="ignore").splitlines() if not line.lstrip().startswith("#"))
    production = [root / "docker-compose.prod.yml", root / "scripts/operations/deploy_scoped_release.sh"]
    production.extend(p for p in (root / "docker").rglob("*") if p.is_file() and "mc-agent-container" not in p.parts)
    assert [(str(p.relative_to(root)), m) for p in production for m in markers if m in code(p)] == []
    # MC's host scripts ship only in MC's own image (deploy-mc-agent.yml).
    mc = (root / "docker/mc-agent-container/Dockerfile").read_text()
    assert "scripts/operations/deploy_mc_container_ec2.sh" in mc and "scripts/operations/mc_prod_db_migrate.sh" in mc
    # deploy.yml only pushes the host secret sync, on every deploy, before it runs it.
    workflow = (root / ".github/workflows/deploy.yml").read_text()
    assert [m for m in markers if m in workflow] == ["scripts/credentials"]
    assert workflow.count("scripts/credentials/") == 1 and "scripts/credentials/sync_cos_runtime_secrets_from_ssm.sh" in workflow
    deploy_job = workflow[workflow.index("  deploy:"):workflow.index("  deploy-docs:")]
    assert deploy_job.index("COS_SECRET_SYNC=") < deploy_job.index("/opt/minimoi/scripts/sync_cos_runtime_secrets_from_ssm.sh /opt/minimoi/.env")


def test_the_test_selector_and_nightly_workflow_redeploy_nothing():
    assert classify(["scripts/ci/select_tests.py", ".github/workflows/nightly-tests.yml"]) == ("documents", ())
    assert classify([".github/workflows/deploy-mc-agent.yml"]) == ("full", ALL_SERVICES)          # other workflows keep the fallback


def test_mc_container_files_redeploy_nothing_and_no_service_reads_them():
    assert classify(["docker/mc-agent-container/openclaw.json", "docker/mc-agent-container/start.sh"]) == ("documents", ())
    root = Path(__file__).resolve().parent.parent
    readers = [str(p.relative_to(root)) for top in ("minimoi_portal", "core", "domains", "services", "utils")
               for p in (root / top).rglob("*") if p.is_file() and p.suffix in (".py", ".sh", ".js", ".mjs", ".json", ".yml", ".yaml")
               and "mc-agent-container" in p.read_text(errors="ignore")]
    assert readers == []
