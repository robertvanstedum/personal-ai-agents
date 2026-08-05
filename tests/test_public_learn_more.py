"""Public presentation links and project framing."""

from bs4 import BeautifulSoup
from pathlib import Path
from pypdf import PdfReader
import pytest


GITHUB_URL = "https://github.com/robertvanstedum/personal-ai-agents"
LINKEDIN_URL = "https://www.linkedin.com/in/robert-van-stedum/"
REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client():
    from minimoi_portal.app import app

    app.config["TESTING"] = True
    app.config["SESSION_COOKIE_SECURE"] = False
    with app.test_client() as test_client:
        yield test_client


def _links(nodes):
    return {
        node.get_text(" ", strip=True): node.get("href")
        for node in nodes
    }


def test_landing_moves_public_links_below_about(client):
    response = client.get("/")
    assert response.status_code == 200
    soup = BeautifulSoup(response.get_data(as_text=True), "html.parser")

    assert _links(soup.select(".front-learn-more a")) == {
        "About": "/about",
        "GitHub": GITHUB_URL,
        "LinkedIn": LINKEDIN_URL,
    }
    header_links = _links(soup.select(".front-door-account a"))
    assert "About" not in header_links
    assert "GitHub" not in header_links
    assert "LinkedIn" not in header_links
    assert _links(soup.select(".front-door-footer a")) == {
        "Request access": "/register"
    }


def test_about_page_uses_readme_introduction(client):
    response = client.get("/about")
    assert response.status_code == 200
    soup = BeautifulSoup(response.get_data(as_text=True), "html.parser")
    story = " ".join(
        soup.select_one(".about-document").get_text(" ", strip=True).split()
    )

    assert "mini-moi is a personal AI agent platform" in story
    assert "The same pattern applies beyond one person" in story
    assert "This repository is the personal version" in story
    assert "in daily use since February 2026" in story
    assert "on AWS since June 2026" in story
    assert "Current state (July 2026)" in story
    assert "Curator" in story
    assert "Mein Deutsch" in story
    assert "Meu Português" in story
    assert "Guild" in story
    assert "Chief of Staff" in story

    pdf_link = soup.select_one('.about-pdf-link[href="/about/readme.pdf"]')
    assert pdf_link is not None
    assert "PDF · 10 pages" in pdf_link.get_text(" ", strip=True)

    header_links = _links(soup.select(".front-door-account a"))
    assert "GitHub" not in header_links
    assert "LinkedIn" not in header_links


def test_about_pdf_downloads_maintained_readme(client):
    response = client.get("/about/readme.pdf")

    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.data.startswith(b"%PDF")
    assert response.headers["Content-Disposition"] == (
        "attachment; filename=mini-moi-README.pdf"
    )


def test_maintained_readme_does_not_link_to_its_own_pdf():
    readme_text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    pdf_text = " ".join(
        page.extract_text() or ""
        for page in PdfReader(REPO_ROOT / "README.pdf").pages
    )

    assert "Download the formatted PDF" not in readme_text
    assert '<a name="overview"></a>' in readme_text
    assert '<div id="overview">' not in readme_text
    assert "Download the formatted PDF" not in pdf_text


def test_tour_keeps_public_links_in_footer_only(client):
    response = client.get("/tour")
    assert response.status_code == 200
    soup = BeautifulSoup(response.get_data(as_text=True), "html.parser")

    header_links = _links(soup.select(".front-door-account a"))
    assert "About" not in header_links
    assert "GitHub" not in header_links
    assert "LinkedIn" not in header_links
    assert _links(soup.select(".tour-footer a")) == {
        "About": "/about",
        "GitHub": GITHUB_URL,
        "LinkedIn": LINKEDIN_URL,
    }
