"""Document-only main pushes must not rebuild or restart application containers."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_workflow_classifies_document_only_changes():
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")

    assert "scripts/ci/classify_release.py" in workflow
    assert "release_class" in workflow
    assert "services" in workflow


def test_image_build_requires_application_changes():
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    build_job = workflow[workflow.index("  build-push:") : workflow.index("  deploy:")]

    assert "needs.classify.outputs.release_class != 'documents'" in build_job
    assert "for service in ${{ needs.classify.outputs.services }}" in build_job


def test_document_release_syncs_without_container_commands():
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    docs_job = workflow[workflow.index("  deploy-docs:") : workflow.index("  notify:")]

    assert "needs.classify.outputs.release_class == 'documents'" in docs_job
    assert "./scripts/sync_docs.sh" in docs_job
    assert "docker build" not in docs_job
    assert "docker-compose" not in docs_job
    assert "docker image prune" not in docs_job
