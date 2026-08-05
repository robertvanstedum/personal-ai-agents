def test_guild_queue_redirects_unauthenticated(portal_client):
    r = portal_client.get("/guild/build")
    assert r.status_code in [200, 302]


def test_guild_roadmap_redirects_unauthenticated(portal_client):
    r = portal_client.get("/guild/build/roadmap")
    assert r.status_code in [200, 302]


def test_owner_roadmap_hides_recursive_pdf_link_and_renders_mermaid():
    from minimoi_portal.app import app

    app.config["TESTING"] = True
    app.config["SESSION_COOKIE_SECURE"] = False
    with app.test_client() as client:
        with client.session_transaction() as session:
            session["user"] = {
                "username": "robert",
                "display_name": "Robert",
                "tier": "owner",
            }

        response = client.get("/guild/build/roadmap")
        html = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "Download the formatted PDF" not in html
        assert html.count('class="language-mermaid"') == 3
        assert '/static/vendor/mermaid-11.16.0.min.js' in html
        assert "cdn.jsdelivr.net" not in html
        assert 'mermaid.run({ nodes: diagrams }).catch' in html
        assert "min-width: 680px" in html

        mermaid_asset = client.get("/static/vendor/mermaid-11.16.0.min.js")
        assert mermaid_asset.status_code == 200
        assert mermaid_asset.content_type.startswith("text/javascript")
