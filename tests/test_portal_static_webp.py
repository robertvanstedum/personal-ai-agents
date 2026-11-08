"""The portal serves .webp pictures as images, so "View full picture" opens them instead of downloading (Robert, 8 Nov)."""
import mimetypes


def test_webp_is_served_as_an_image():
    import minimoi_portal.app as portal_app

    assert mimetypes.guess_type("x.webp")[0] == "image/webp"
    response = portal_app.app.test_client().get("/static/guild/guild-craftsman.webp")
    try:
        assert response.status_code == 200
        assert response.mimetype == "image/webp"
    finally:
        response.close()
