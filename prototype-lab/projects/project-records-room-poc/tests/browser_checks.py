"""Opt-in real-Chrome regression checks using only synthetic temporary records.

Run explicitly with pytest; requires a loopback listener and installed Chrome.
"""
import json
from pathlib import Path
import socket
import sys
from threading import Thread
from uuid import uuid4

import pytest
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import create_app


@pytest.fixture
def live(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0)); port=sock.getsockname()[1]
    app=create_app(tmp_path/"private",port=port,testing=True)
    store=app.extensions["records_store"]
    def room(title):
        return store.create_room("robert",str(uuid4()),dict(title=title,purpose="Synthetic browser validation only",
                     mode="meeting",recording_acknowledged=True))["result"]["id"]
    first=room("Synthetic design review"); second=room("Synthetic incident bridge")
    token=store.add_principal("robert","client",dict(id="reviewer",label="Synthetic reviewer"))["access_token"]
    store.membership("robert","invite",first,dict(actor="reviewer",role="contributor"))
    server=make_server("127.0.0.1",port,app,threaded=True)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(channel="chrome",headless=True)
        context=browser.new_context(viewport={"width":1512,"height":1040})
        page=context.new_page()
        errors=[];page.on("pageerror",lambda error:errors.append(str(error)))
        yield page,store,f"http://127.0.0.1:{port}",first,second,token
        assert not errors
        context.close();browser.close()
    server.shutdown();thread.join(timeout=5)


def signin(page,url,token,room):
    page.goto(f"{url}/#room/{room}")
    page.locator("#access-key").fill(token)
    page.locator("#login-form button").click()
    expect(page.locator("#workspace")).to_be_visible()
    expect(page.locator("#message-body")).to_be_enabled()


def test_separate_notes_after_meeting_closes(live):
    page,store,url,first,second,_=live
    signin(page,url,store.owner_key,first)
    page.locator("#message-body").fill("Keep the full exchange, including alternatives.")
    page.locator("#send-message").click()
    expect(page.locator("#messages")).to_contain_text("Keep the full exchange")
    store.state("robert","notes-close",first,dict(state="closed",version=1,checkpoint="Meeting finished"))
    page.reload()
    expect(page.locator("#message-body")).to_be_disabled()
    expect(page.locator("#pause-room")).to_be_hidden()
    expect(page.locator("#paused-notice")).to_contain_text("closed permanently")
    page.locator("#new-note").click()
    page.locator("#field-title").fill("Review outcomes")
    page.locator("#field-kind").select_option("decision_summary")
    page.locator("#field-body").fill("We discussed two options. No artifact has been accepted.")
    page.locator("#field-context_class").select_option("external_source")
    page.locator("#modal-submit").click()
    expect(page.locator("#meeting-notes")).to_contain_text("Review outcomes")
    expect(page.locator("#messages")).not_to_contain_text("We discussed two options")
    page.locator("#meeting-notes").get_by_role("button",name="Read / download").click()
    expect(page.locator("#modal")).to_contain_text("We discussed two options")
    with page.expect_download() as downloaded:
        page.get_by_role("button",name="Download note",exact=True).click()
    assert downloaded.value.suggested_filename.startswith("meeting-note-")
    assert "Material provenance: external_source" in Path(downloaded.value.path()).read_text()
    page.locator("#modal-submit").click()
    assert len(store.room("robert",first)["notes"])==1
    assert store.room("robert",first)["notes"][0]["context_class"]=="external_source"
    assert not any(e["kind"]=="decision" for e in store.room("robert",first)["events"])
    page.locator("#logout").click()
    expect(page.locator("#meeting-notes")).to_be_empty()


def test_quiet_room_and_two_separate_sessions(live):
    page,store,url,first,_,_=live
    signin(page,url,store.owner_key,first)
    page.locator("#new-project").click()
    page.locator("#field-title").fill("Synthetic persistent room")
    page.locator("#field-purpose").fill("Shared context, not a session transcript")
    page.locator("#modal-submit").click()
    expect(page.locator("#parent-title")).to_have_text("Synthetic persistent room")
    expect(page.locator("#parent-sessions")).to_contain_text("No sessions yet")
    expect(page.locator("#room-workspace")).to_be_hidden()
    assert len(store.rooms("robert"))==2
    parent_id=page.url.split("#project/")[1]
    page.locator("#new-child-session").click()
    assert page.locator("#field-parent").input_value()==parent_id
    page.locator("#field-title").fill("First discussion")
    page.locator("#field-purpose").fill("First purpose")
    page.locator("#field-capture").check()
    page.locator("#modal-submit").click()
    expect(page.locator("#room-title")).to_have_text("First discussion")
    first_child=page.url.split("#room/")[1]
    page.locator("#message-body").fill("FIRST_SESSION_ONLY")
    page.locator("#send-message").click()
    expect(page.locator("#messages")).to_contain_text("FIRST_SESSION_ONLY")
    page.locator("#close-room").click()
    page.locator("#field-checkpoint").fill("First session complete")
    page.locator("#modal-submit").click()
    expect(page.locator("#pause-room")).to_be_hidden()
    page.locator(f'[data-parent="{parent_id}"]').click()
    expect(page.locator("#parent-sessions")).to_contain_text("First discussion · closed")
    page.locator("#new-child-session").click()
    page.locator("#field-title").fill("Second discussion")
    page.locator("#field-purpose").fill("Second purpose")
    page.locator("#field-capture").check()
    page.locator("#modal-submit").click()
    expect(page.locator("#room-title")).to_have_text("Second discussion")
    expect(page.locator("#messages")).not_to_contain_text("FIRST_SESSION_ONLY")
    siblings=store.persistent_room("robert",parent_id)["sessions"]
    assert len(siblings)==2 and len({s["id"] for s in siblings})==2
    assert store.room("robert",first_child)["state"]=="closed"
    assert all(len(store.room("robert",s["id"])["members"])==1 for s in siblings)
    page.locator(f'[data-parent="{parent_id}"]').click()
    expect(page.locator("#parent-sessions")).to_contain_text("Second discussion · active")
    shots=Path(__file__).resolve().parents[1]/"evidence"/"screenshots"
    shots.mkdir(parents=True,exist_ok=True)
    page.screenshot(path=str(shots/"persistent-room-desktop.png"),full_page=True)
    page.set_viewport_size({"width":390,"height":844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(shots/"persistent-room-mobile.png"),full_page=True)
    page.locator("#logout").click()
    expect(page.locator("#parent-title")).to_be_empty()
    expect(page.locator("#parent-sessions")).to_be_empty()


def test_parent_ui_never_lists_unauthorized_sibling(live):
    page,store,url,first,_,token=live
    sibling=store.create_session("robert","sibling",first,dict(title="HIDDEN_SIBLING",purpose="Hidden",recording_acknowledged=True))["result"]["id"]
    signin(page,url,token,first)
    page.locator(f'[data-parent="{first}"]').click()
    expect(page.locator("#parent-title")).to_have_text("Synthetic design review")
    expect(page.locator("#parent-sessions")).not_to_contain_text("HIDDEN_SIBLING")
    expect(page.locator("#room-list")).not_to_contain_text("HIDDEN_SIBLING")
    expect(page.locator("#new-child-session")).to_be_hidden()
    expect(page.locator("#new-project")).to_be_hidden()
    assert sibling not in page.locator("#parent-sessions").inner_html()


def test_delayed_parent_cannot_replace_session_navigation(live):
    page,store,url,first,second,_=live
    signin(page,url,store.owner_key,first)
    delayed=[]
    page.route(f"**/api/v2/rooms/{second}",lambda route:delayed.append(route),times=1)
    page.locator(f'[data-parent="{second}"]').click()
    expect(page.locator("#room-workspace")).to_be_hidden()
    page.evaluate("id => location.hash='room/'+id",first)
    expect(page.locator("#room-title")).to_have_text("Synthetic design review")
    expect(page.locator("#room-workspace")).to_be_visible()
    assert delayed
    delayed[0].continue_()
    page.wait_for_timeout(200)
    expect(page.locator("#parent-workspace")).to_be_hidden()
    expect(page.locator("#room-title")).to_have_text("Synthetic design review")


def test_browser_workflow_and_layout(live):
    page,store,url,first,second,_=live
    signin(page,url,store.owner_key,first)
    page.locator("#event-kind").select_option("proposal")
    page.locator("#message-body").fill("Keep explicit capture boundaries; do not record casual chat.")
    page.locator("#send-message").click()
    page.get_by_role("button",name="Record my decision").click()
    page.locator("#field-body").fill("Approved for this synthetic local test only.")
    page.locator("#modal-submit").click()
    expect(page.locator("#decisions")).to_contain_text("Approved for this synthetic local test only.")
    page.locator("#pause-room").click()
    page.locator("#field-checkpoint").fill("Resume with integration boundaries.")
    page.locator("#modal-submit").click()
    expect(page.locator("#message-body")).to_be_disabled()
    expect(page.locator("#upload")).to_be_hidden()
    page.locator("#pause-room").click()
    page.locator("#field-checkpoint").fill("Continue the synthetic review.")
    page.locator("#modal-submit").click()
    expect(page.locator("#message-body")).to_be_enabled()
    page.locator("#upload").click()
    page.locator("#field-file").set_input_files({"name":"synthetic-evidence.txt","mimeType":"text/plain","buffer":b"Synthetic source: original bytes retained."})
    page.locator("#field-source_note").fill("Generated fixture for UI validation; not a real discussion.")
    page.locator("#modal-submit").click()
    expect(page.locator("#documents")).to_contain_text("synthetic-evidence.txt")
    with page.expect_download() as downloaded:
        page.locator("#export-md").click()
    assert "Approved for this synthetic local test only." in Path(downloaded.value.path()).read_text()
    shots=Path(__file__).resolve().parents[1]/"evidence"/"screenshots"
    shots.mkdir(parents=True,exist_ok=True)
    page.screenshot(path=str(shots/"room-desktop.png"),full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.set_viewport_size({"width":390,"height":844})
    expect(page.locator("#logout")).to_be_visible()
    page.screenshot(path=str(shots/"room-mobile.png"),full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.set_viewport_size({"width":1512,"height":1040})
    page.locator('[data-view="vault"]').click()
    page.locator("#search-query").fill("original bytes")
    page.locator("#search-form button").click()
    expect(page.locator("#search-results")).to_contain_text("synthetic-evidence.txt")
    page.screenshot(path=str(shots/"vault-desktop.png"),full_page=True)


def test_drafts_stay_in_their_room(live):
    page,store,url,first,second,_=live
    signin(page,url,store.owner_key,first)
    page.locator("#message-body").fill("Private unsubmitted draft for room one")
    page.evaluate("id => location.hash='room/'+id",second)
    expect(page.locator("#room-title")).to_have_text("Synthetic incident bridge")
    expect(page.locator("#message-body")).to_have_value("")
    page.locator("#message-body").fill("Draft for room two")
    page.evaluate("id => location.hash='room/'+id",first)
    expect(page.locator("#room-title")).to_have_text("Synthetic design review")
    expect(page.locator("#message-body")).to_have_value("Private unsubmitted draft for room one")
    assert len(store.room("robert",second)["events"])==1


def test_logout_removes_private_cached_content(live):
    page,store,url,first,second,token=live
    store.append("robert","secret",second,dict(body="OWNER_PRIVATE_SENTINEL"))
    signin(page,url,store.owner_key,second)
    page.locator("#message-body").fill("UNSENT_OWNER_SENTINEL")
    page.locator('[data-view="vault"]').click()
    page.locator("#search-query").fill("OWNER_PRIVATE_SENTINEL")
    page.locator("#search-form button").click()
    expect(page.locator("#search-results")).to_contain_text("OWNER_PRIVATE_SENTINEL")
    page.locator("#logout").click()
    expect(page.locator("#login")).to_be_visible()
    page.evaluate("id => location.hash='room/'+id",first)
    page.locator("#access-key").fill(token)
    page.locator("#login-form button").click()
    expect(page.locator("#profile-name")).to_have_text("Synthetic reviewer")
    expect(page.locator("#room-title")).to_have_text("Synthetic design review")
    assert "OWNER_PRIVATE_SENTINEL" not in page.locator("body").text_content()
    expect(page.locator("#message-body")).to_have_value("")
    assert page.locator("#search-query").input_value()==""


def test_committed_500_recovers_after_reload_without_duplicate(live):
    page,store,url,first,_,_=live
    signin(page,url,store.owner_key,first)
    def lose_response(route):
        response=route.fetch()
        assert response.status==201
        route.fulfill(status=500,content_type="application/json",body=json.dumps({"error":"Synthetic lost confirmation"}))
    page.route(f"**/api/v1/rooms/{first}/events",lose_response,times=1)
    page.locator("#message-body").fill("ONE_COMMITTED_CONTRIBUTION")
    page.locator("#send-message").click()
    expect(page.locator("#pending-operation")).to_be_visible()
    metadata=page.evaluate("sessionStorage.getItem('minimoi.pending.robert')")
    assert metadata and "ONE_COMMITTED_CONTRIBUTION" not in metadata
    page.reload()
    expect(page.locator("#messages")).to_contain_text("ONE_COMMITTED_CONTRIBUTION")
    expect(page.locator("#pending-operation")).to_be_hidden()
    assert page.evaluate("sessionStorage.getItem('minimoi.pending.robert')")==None
    assert sum(e["body"]=="ONE_COMMITTED_CONTRIBUTION" for e in store.room("robert",first)["events"])==1


def test_delayed_room_response_cannot_replace_current_room(live):
    page,store,url,first,second,_=live
    signin(page,url,store.owner_key,first)
    delayed=[]
    page.route(f"**/api/v1/rooms/{second}",lambda route:delayed.append(route),times=1)
    page.evaluate("id => location.hash='room/'+id",second)
    page.wait_for_function("document.getElementById('message-body').disabled")
    page.wait_for_timeout(2800)  # At least one polling tick while destination is delayed.
    expect(page.locator("#message-body")).to_be_disabled()
    page.evaluate("id => location.hash='room/'+id",first)
    expect(page.locator("#message-body")).to_be_enabled()
    assert delayed
    delayed[0].continue_()
    page.wait_for_timeout(200)
    expect(page.locator("#room-title")).to_have_text("Synthetic design review")


def test_opening_session_requires_capture_acknowledgement(live):
    page,store,url,first,_,_=live
    signin(page,url,store.owner_key,first)
    page.locator("#new-room").click()
    page.locator("#field-title").fill("Synthetic deliberate thinking")
    page.locator("#field-purpose").fill("Exercise the explicit beginning of a session")
    page.locator("#field-mode").select_option("conversation")
    page.locator("#modal-submit").click()
    assert len(store.rooms("robert"))==2
    page.locator("#field-capture").check()
    page.locator("#modal-submit").click()
    expect(page.locator("#room-title")).to_have_text("Synthetic deliberate thinking")
    assert len(store.rooms("robert"))==3


def test_inflight_submit_preserves_next_draft(live):
    page,store,url,first,_,_=live
    signin(page,url,store.owner_key,first)
    delayed=[]
    page.route(f"**/api/v1/rooms/{first}/events",lambda route:delayed.append(route),times=1)
    page.locator("#message-body").fill("First submitted message")
    page.locator("#send-message").click()
    page.locator("#message-body").fill("Second not yet submitted")
    assert delayed
    delayed[0].continue_()
    expect(page.locator("#messages")).to_contain_text("First submitted message")
    expect(page.locator("#message-body")).to_have_value("Second not yet submitted")


def test_room_switch_closes_source_dialog(live):
    page,store,url,first,second,_=live
    signin(page,url,store.owner_key,first)
    page.locator("#upload").click()
    expect(page.locator("#modal")).to_be_visible()
    page.evaluate("id => location.hash='room/'+id",second)
    expect(page.locator("#room-title")).to_have_text("Synthetic incident bridge")
    expect(page.locator("#modal")).not_to_be_visible()
    assert store.room("robert",second)["documents"]==[]


def test_conflicting_reentry_retains_original_recovery_key(live):
    page,store,url,first,_,_=live
    signin(page,url,store.owner_key,first)
    key="synthetic-delayed-original"
    metadata={"path":f"/api/v1/rooms/{first}/events","key":key,"actor":"robert"}
    page.evaluate("value => sessionStorage.setItem('minimoi.pending.robert',JSON.stringify(value))",metadata)
    page.reload()
    expect(page.locator("#pending-operation")).to_be_visible()
    expect(page.locator("#toast")).to_contain_text("No receipt found yet")
    store.append("robert",key,first,dict(body="Original committed late"))
    page.locator("#message-body").fill("Different re-entered content")
    page.locator("#send-message").click()
    expect(page.locator("#toast")).to_contain_text("Idempotency key")
    assert json.loads(page.evaluate("sessionStorage.getItem('minimoi.pending.robert')"))["key"]==key
    page.locator("#dismiss-operation").click()
    expect(page.locator("#pending-operation")).to_be_hidden()
    assert len([e for e in store.room("robert",first)["events"] if e["kind"]=="message"])==1


def test_artifact_reasoning_and_provenance_in_browser(live):
    page,store,url,first,_,_=live
    signin(page,url,store.owner_key,first)
    page.locator("#context-class").select_option("external_source")
    page.locator("#message-body").fill("Synthetic quoted requirement: show order management before billing.")
    page.locator("#send-message").click()
    expect(page.locator("#messages")).to_contain_text("external_source")
    page.locator("#link-artifact").click()
    page.locator("#field-label").fill("Synthetic deck revision")
    page.locator("#field-value").fill("c"*64)
    page.locator("#field-revision").fill("v9")
    page.locator("#modal-submit").click()
    expect(page.locator("#artifact-links")).to_contain_text("Synthetic deck revision")
    page.get_by_role("button",name="Read supporting statement").click()
    expect(page.locator("#modal-fields")).to_contain_text("external_source")
    expect(page.locator("#modal-fields")).to_contain_text("show order management")
    page.locator("#modal-submit").click()
    page.locator('[data-view="vault"]').click()
    page.locator("#search-query").fill("c"*64)
    page.locator("#search-form button").click()
    expect(page.locator("#search-results")).to_contain_text("supporting message by Robert")
    expect(page.locator("#search-results")).to_contain_text("external_source")


def test_brief_discloses_earlier_records(live):
    page,store,url,first,_,_=live
    proposal=store.append("robert","proposal",first,dict(kind="proposal",body="Synthetic proposal"))["result"]
    for index in range(7):
        store.append("robert",f"decision-{index}",first,dict(kind="decision",body=f"Synthetic decision {index}",reference=proposal["id"],expected_context=store.room("robert",first)["contribution_guard"]))
        store.append("robert",f"task-{index}",first,dict(kind="task",body=f"Synthetic assignment {index}",target="reviewer",expected_context=store.room("robert",first)["contribution_guard"]))
    signin(page,url,store.owner_key,first)
    page.get_by_role("button",name="3 earlier decisions — view all 7").click()
    expect(page.locator("#modal-fields")).to_contain_text("Synthetic decision 0")
    expect(page.locator("#modal-fields")).to_contain_text("Synthetic decision 6")
    page.locator("#modal-submit").click()
    page.get_by_role("button",name="2 earlier assignments — view all 7").click()
    expect(page.locator("#modal-fields")).to_contain_text("Synthetic assignment 0")


def test_agent_contribution_readable_safe_and_receipt_linked(live, tmp_path):
    import requests
    from integration.cos_records_bridge import RoomClient
    from integration.cos_agent_responder import respond, SyntheticSessionPolicy
    from integration.cos_room_responder import TurnJournal
    page, store, url, room, _, _ = live
    token = store.add_principal('robert', 'agent-ui', dict(id='cos-dev', label='Development CoS'))['access_token']
    store.membership('robert', 'agent-invite', room, dict(actor='cos-dev', role='contributor'))
    source = store.append('robert', 'source-ui', room, dict(body='Synthetic UI question', context_class='robert_source'))['result']['id']
    key = tmp_path / 'agent-key'; key.write_text(token); key.chmod(0o600)
    config = tmp_path / 'agent-config.json'
    config.write_text(json.dumps(dict(url='http://127.0.0.1:18880', actor_id='cos-dev', token_file=str(key)))); config.chmod(0o600)
    # Use the same Flask application behind the loopback fixture, via actual HTTP.
    class EphemeralPortSession:
        def request(self, method, target, **kwargs):
            kwargs["headers"]["Host"] = url.removeprefix("http://")
            return requests.request(method, target.replace("http://127.0.0.1:18880", url, 1), **kwargs)
    client = RoomClient(config, session=EphemeralPortSession())
    reply = 'Readable synthetic answer <img src=x onerror=alert(1)>\nSecond line.'
    request = str(uuid4())
    result = respond(room, request, client=client,
        model=lambda data, rid, action: dict(text=reply, coordination_request_id=rid,
            openclaw_run_id='chatcmpl_'+str(uuid4()), agent_id='cos-agent-a', mode='actual_agent_response'),
        journal=TurnJournal(tmp_path/'journal'), policy=SyntheticSessionPolicy([room]), owner_authorized=True)
    signin(page, url, store.owner_key, room)
    contribution = page.locator('.agent-contribution')
    expect(contribution.locator('.event-body')).to_have_text(reply)
    expect(contribution.locator('img')).to_have_count(0)
    expect(contribution.locator('.agent-warning')).to_be_visible()
    expect(contribution.locator('details')).not_to_have_attribute('open', '')
    contribution.locator('summary').click()
    expect(contribution).to_contain_text(result['operation']['receipt_id'])
    contribution.locator(f'a[href="#event-{source}"]').click()
    assert page.url.endswith('#room/'+room)
    expect(page.locator('#event-'+source)).to_be_in_viewport()
    page.set_viewport_size(dict(width=390, height=844))
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.reload()
    expect(page.locator('.agent-contribution .event-body')).to_have_text(reply)


def test_imported_speaker_is_untrusted_text_not_identity(live):
    page,store,url,first,second,token=live
    store.import_conversation('reviewer','browser-import',first,dict(
        source_application='synthetic-client',coverage='Two selected synthetic turns',
        turns=[dict(speaker='robert',text='Quoted statement only'),
               dict(speaker='<img src=x onerror="window.importExecuted=true">',text='Literal label')],
        handoff='Proposed handoff; no approved assignment'))
    signin(page,url,store.owner_key,first)
    expect(page.get_by_text('Declared speaker: robert · submitted transcript label, not verified identity',exact=True)).to_be_visible()
    expect(page.get_by_text('Declared speaker: <img src=x onerror="window.importExecuted=true"> · submitted transcript label, not verified identity',exact=True)).to_be_visible()
    assert page.evaluate('window.importExecuted === undefined')
    row=page.locator('.event').filter(has_text='Quoted statement only')
    expect(row.locator('.event-author')).to_have_text('Synthetic reviewer')


def test_dev_portal_prefix_real_proxy_roundtrip(tmp_path):
    from flask import Flask,session,redirect
    from functools import wraps
    from dev_portal_bridge import install
    servers=[];threads=[]
    for _ in range(2):
        server=make_server('127.0.0.1',0,lambda env,start: [],threaded=True)
        servers.append(server)
    backend,portal_server=servers
    records=create_app(tmp_path/'private-proxy',port=backend.server_port,testing=True)
    backend.app=records
    store=records.extensions['records_store']
    room=store.create_room('robert','proxy-room',dict(title='Dev proxy synthetic',purpose='Prefix and auth verification',recording_acknowledged=True))['result']['id']
    portal=Flask('synthetic-portal');portal.secret_key='synthetic-cookie-key'
    def owner(fn):
        @wraps(fn)
        def wrapped(*a,**kw):
            if session.get('owner') is not True: return 'Owner sign-in required',401
            return fn(*a,**kw)
        return wrapped
    @portal.get('/test-signin')
    def test_signin():
        session['owner']=True
        return redirect('/app/records/')
    install(portal,owner,owner,backend=f'http://127.0.0.1:{backend.server_port}',local_port=portal_server.server_port)
    portal_server.app=portal
    for server in servers:
        thread=Thread(target=server.serve_forever,daemon=True);thread.start();threads.append(thread)
    try:
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(channel='chrome',headless=True)
            page=browser.new_page(); errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            url=f'http://127.0.0.1:{portal_server.server_port}'
            assert page.request.get(url+'/app/records/api/v1/rooms').status==401
            page.goto(url+'/test-signin')
            page.locator('#access-key').fill(store.owner_key)
            page.locator('#login-form button').click()
            expect(page.locator('#workspace')).to_be_visible()
            page.goto(url+'/app/records/#room/'+room)
            expect(page.locator('#message-body')).to_be_enabled()
            page.locator('#message-body').fill('Saved through dev prefix')
            page.locator('#send-message').click()
            expect(page.locator('.event-body').filter(has_text='Saved through dev prefix')).to_be_visible()
            assert store.room('robert',room)['events'][-1]['body']=='Saved through dev prefix'
            assert not errors
            browser.close()
    finally:
        for server in servers:server.shutdown();server.server_close()
        for thread in threads:thread.join(timeout=5)
