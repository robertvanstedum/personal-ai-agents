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
                     "docker/mc-agent/mc-key-check.sh"]) == ("documents", ())
    # Guard: the day a Dockerfile, compose file or deploy script uses docker/mc-agent/,
    # this test fails and that change must classify the path as a real service.
    root = Path(__file__).resolve().parent.parent
    users = []
    candidates = list((root / "docker").glob("Dockerfile*")) + list(root.glob("docker-compose*.yml")) + [
        root / ".github/workflows/deploy.yml", root / "scripts/operations/deploy_scoped_release.sh"]
    for path in candidates:
        if path.exists() and "docker/mc-agent" in path.read_text():
            users.append(path.name)
    assert users == []
