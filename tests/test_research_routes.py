"""
Regression coverage for research_routes.py's import and Flask blueprint
registration. curator_server.py wraps the blueprint registration in a
try/except specifically so a failure there can't crash the whole server —
which means a broken import silently disables every /research/* route
without raising anywhere a normal test would notice. This test asserts the
blueprint actually registered, not just that curator_server.py imported
without an exception.
"""

from research_routes import research_bp, save_annotation, _get_recent_annotations


def test_research_routes_imports_annotations_module():
    assert callable(save_annotation)
    assert callable(_get_recent_annotations)


def test_research_blueprint_has_deferred_routes():
    # deferred_functions holds every @research_bp.route(...) registered at
    # import time; a nonzero count confirms the module's route decorators
    # ran. The real end-to-end check is registration on the live app, below.
    assert len(research_bp.deferred_functions) > 0


def test_research_blueprint_registers_on_curator_app(curator_client):
    app = curator_client.application
    research_routes = [
        str(rule) for rule in app.url_map.iter_rules()
        if str(rule).startswith("/api/research/") or str(rule).startswith("/research/")
    ]
    assert "/api/research/annotate" in research_routes, (
        "Research Intelligence blueprint did not register — curator_server.py "
        "silently swallows this failure at startup (see the try/except around "
        "`from research_routes import research_bp`), so this must be checked "
        "explicitly rather than relying on import-time exceptions."
    )
