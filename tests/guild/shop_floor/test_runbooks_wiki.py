from floor_helpers import load_portal, staging
from floor_helpers import write_headers

API = "/guild-next/api/v1/wiki/pages"
PAGE = "/guild-next/guild/operate/runbooks"


def body(**changes):
    return {"title": "Recovery procedure", "body": "# Check first\nDo not restart without checking logs.", "page": None, "revision": 0, "idempotency_key": "wiki-create-0001", **changes}


def test_create_edit_history_conflict_retry_search_and_safe_render(staging):
    client = staging.owner()
    headers = write_headers(staging.csrf(client))
    first = client.post(API, json=body(), headers=headers)
    assert first.status_code == 200
    page = first.json["page"]
    assert client.post(API, json=body(), headers=headers).json == first.json
    edit = body(page=page, revision=1, title="Recovery updated", body="<script>alert(1)</script>\n[unsafe](javascript:alert(1))", idempotency_key="wiki-edit-00001")
    assert client.post(API, json=edit, headers=headers).json["revision"] == 2
    assert client.post(API, json={**edit, "body": "lost edit", "idempotency_key": "wiki-conflict-01"}, headers=headers).status_code == 409
    assert client.post(API, json={**edit, "body": "key misuse"}, headers=headers).status_code == 409
    latest = client.get(PAGE + "?page=" + page)
    assert latest.status_code == 200 and b"Recovery updated" in latest.data
    assert b"<script>alert(1)</script>" not in latest.data and b'href="javascript:' not in latest.data
    old = client.get(PAGE + "?page=" + page + "&revision=1")
    assert b"Do not restart" in old.data and b"data-wiki-editor" not in old.data
    assert b"Recovery updated" in client.get(PAGE + "?q=Recovery").data
    assert b"No matching pages" in client.get(PAGE + "?q=absentword").data


def test_wiki_guards_and_validation(staging):
    for client in (staging.guest(), staging.client()):
        assert client.get(PAGE).status_code in (302, 403)
        assert client.post(API, json=body()).status_code in (401, 403)
    client = staging.owner()
    token = staging.csrf(client)
    assert client.post(API, json=body()).status_code == 403
    assert client.post(API, json=body(), headers=write_headers(token, mode="off_record")).status_code == 409
    for change in ({"title": ""}, {"title": "x"*161}, {"body": "x"*24001}, {"page": "../escape"}, {"revision": True}):
        assert client.post(API, json=body(**change), headers=write_headers(token)).status_code == 422


def test_operate_has_no_provisional_subtabs_its_tools_are_quiet_body_links_and_maintain_alias_is_wiki(staging):
    page = staging.owner().get(PAGE).get_data(as_text=True)                                              # a tool's page: one way back, no tab row (10 Oct)
    assert "data-operate-back" in page and "subnav-more" not in page and "In design</a>" not in page and "guild-sections" not in page
    operate = staging.owner().get("/guild-next/guild/operate").get_data(as_text=True)
    assert "subnav-link" not in operate and "subnav-more" not in operate and "In design</a>" not in operate
    tools = operate[operate.index("data-operate-tools"):]
    tools = tools[:tools.index("</p>")]
    for label in ("Build host", "Runbooks", "Agents", "MC tending", "Operational checks", "Administration"):
        assert f">{label}</a>" in tools, label                                                           # every working tool is still one link away
    assert ">Maintain</a>" not in page and ">Checks</a>" not in page
    assert b"Search runbooks" in staging.owner().get("/guild-next/guild/operate/maintain").data


def test_refused_storage_never_creates_database(staging, monkeypatch):
    from domains.guild.queue_store import QueueStore
    monkeypatch.setattr(QueueStore, "write_problem", lambda self: "missing persistent mount")
    client = staging.owner()
    assert b"Wiki storage is unavailable" in client.get(PAGE).data
    assert client.post(API, json=body(), headers=write_headers(staging.csrf(client))).status_code == 503
    assert not (staging.queue_path.parent / "runbooks.sqlite3").exists()
