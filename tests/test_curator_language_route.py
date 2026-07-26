def test_curator_language_page_is_served_from_domain_templates(curator_client):
    response = curator_client.get("/language")

    assert response.status_code == 200
    assert b"Language Learning" in response.data
    assert b"Daily practice, spaced repetition, conversation simulation" in response.data
