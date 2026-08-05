from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = REPO_ROOT / "scripts" / "docs"


def test_key_document_sources_and_publications_are_declared():
    publisher = (SCRIPT_DIR / "publish_key_docs.py").read_text(encoding="utf-8")
    for stem in ("README", "ARCHITECTURE", "OPERATIONS", "ROADMAP"):
        assert f'("{stem}.md", "{stem}.pdf"' in publisher


def test_publisher_dependencies_are_version_pinned():
    package = (SCRIPT_DIR / "package.json").read_text(encoding="utf-8")
    requirements = (SCRIPT_DIR / "requirements.txt").read_text(encoding="utf-8")
    assert '"marked": "18.0.7"' in package
    assert '"mermaid": "11.16.0"' in package
    assert '"playwright": "1.62.0"' in package
    assert "pypdf==6.10.0" in requirements


def test_reference_sources_link_to_their_matching_pdf():
    for stem in ("ARCHITECTURE", "OPERATIONS"):
        source = (REPO_ROOT / f"{stem}.md").read_text(encoding="utf-8")
        assert f"]({stem}.pdf)" in source


def test_public_overview_sources_do_not_link_to_their_own_pdf():
    for stem in ("README", "ROADMAP"):
        source = (REPO_ROOT / f"{stem}.md").read_text(encoding="utf-8")
        assert f"]({stem}.pdf)" not in source


def test_renderer_keeps_section_headings_with_their_first_visual():
    renderer = (SCRIPT_DIR / "render_key_doc.mjs").read_text(encoding="utf-8")
    stylesheet = (SCRIPT_DIR / "key-docs.css").read_text(encoding="utf-8")

    assert 'group.className = "keep-section-start"' in renderer
    assert 'element.matches("table, figure, .mermaid")' in renderer
    assert 'onlyNode.tagName !== "STRONG"' in renderer
    assert ".keep-section-start {" in stylesheet
    assert "break-inside: avoid-page;" in stylesheet
