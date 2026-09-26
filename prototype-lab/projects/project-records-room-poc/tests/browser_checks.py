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
    store._browser_test_app=app
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
    page.locator("#mobile-rooms").click()
    expect(page.locator("#logout")).to_be_visible()
    page.locator("#mobile-room-close").click()
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


def test_owner_requests_cos_and_reply_appears_end_to_end(live,tmp_path):
    from test_cos_records_bridge import LocalSession
    from integration.cos_records_bridge import RoomClient
    from integration.cos_agent_responder import respond,SyntheticSessionPolicy
    from integration.cos_room_responder import TurnJournal
    page,store,url,room,_,_=live
    token=store.add_principal('robert','cos-browser-client',dict(id='cos-dev',label='Chief of Staff'))['access_token']
    store.membership('robert','cos-browser-invite',room,dict(actor='cos-dev',role='contributor'))
    key=tmp_path/'cos.key';key.write_text(token);key.chmod(0o600)
    config=tmp_path/'cos.json';config.write_text(json.dumps(dict(url='http://127.0.0.1:18880',actor_id='cos-dev',token_file=str(key))));config.chmod(0o600)
    app=store._browser_test_app;queue=app.extensions['cos_requests'];queue.allowed=frozenset([room])
    import requests
    from urllib.parse import urlsplit
    class EphemeralLoopback(requests.Session):
        def request(self,method,target,**kwargs):
            parts=urlsplit(target)
            kwargs['headers']['Host']=urlsplit(url).netloc
            return super().request(method,url+parts.path+('?' + parts.query if parts.query else ''),**kwargs)
    client=RoomClient(config,session=EphemeralLoopback());journal=TurnJournal(tmp_path/'turns')
    calls=[]
    def model(data,request_id,action):
        calls.append(data)
        return dict(text='Browser integration fixture: I received your saved question.',coordination_request_id=request_id,
                    openclaw_run_id='chatcmpl_'+str(uuid4()),agent_id='cos-agent-a',mode='actual_agent_response')
    signin(page,url,store.owner_key,room)
    page.locator('#message-body').fill('Synthetic owner question: Chief of Staff, are you here?')
    page.locator('#send-message').click()
    expect(page.locator('#message-body')).to_have_value('')
    expect(page.locator('#ask-cos')).to_have_count(0)
    page.locator('#toggle-cos-auto').click()
    expect(page.locator('#cos-auto-status')).to_contain_text('active')
    queue.schedule_auto()
    assert not calls
    queue.run_once(lambda room,key,action,guard:respond(room,key,client=client,model=model,journal=journal,
        policy=SyntheticSessionPolicy([room]),owner_authorized=True,action=action,expected_guard=guard,
        authorization_check=lambda:queue.check_auto_authority(key)))
    expect(page.locator('#messages')).to_contain_text('Browser integration fixture: I received your saved question.',timeout=10000)
    expect(page.locator('#cos-request-status')).to_contain_text('reply saved',timeout=10000)
    assert len(calls)==1 and calls[0]['records'][-1]['body'].startswith('Synthetic owner question')
    # Catch-up must use the same readable projection and warning as the room.
    page.locator('[data-view="activity"]').click()
    expect(page.locator('#activity-list .activity-excerpt')).to_contain_text('Browser integration fixture: I received your saved question.')
    expect(page.locator('#activity-list')).not_to_contain_text('request_fingerprint')
    expect(page.locator('#activity-list .agent-warning')).to_contain_text('Synthetic test only')
    page.locator('#activity-list').get_by_role('button',name='Synthetic design review').click()
    expect(page.locator('#message-body')).to_be_enabled()
    # Already invited: a normal saved message triggers the next response.
    page.locator('#message-body').fill('Synthetic followup: respond automatically now.')
    page.locator('#send-message').click()
    expect(page.locator('#message-body')).to_have_value('')
    queue.schedule_auto()
    assert queue.run_once(lambda room,key,action,guard:respond(room,key,client=client,model=model,journal=journal,
        policy=SyntheticSessionPolicy([room]),owner_authorized=True,action=action,expected_guard=guard,
        authorization_check=lambda:queue.check_auto_authority(key)))
    expect(page.locator('#messages').get_by_text('Browser integration fixture: I received your saved question.',exact=True)).to_have_count(2,timeout=10000)
    assert len(calls)==2
    # GET status fails while POST pause is still available.
    page.route('**/cos-requests',lambda route:route.fulfill(status=503,content_type='application/json',body='{"error":"status unavailable"}'))
    expect(page.locator('#cos-request-status')).to_contain_text('status unavailable',timeout=10000)
    expect(page.locator('#toggle-cos-auto')).to_be_enabled()
    page.locator('#toggle-cos-auto').click()
    expect(page.locator('#toggle-cos-auto')).to_be_enabled()
    assert not queue.status('robert',room)['auto']['enabled']
    page.unroute('**/cos-requests')
    expect(page.locator('#cos-auto-status')).to_contain_text('off')
    page.locator('#message-body').fill('Synthetic message while CoS is paused.')
    page.locator('#send-message').click()
    expect(page.locator('#message-body')).to_have_value('')
    queue.schedule_auto()
    assert not queue.run_once(lambda *args: (_ for _ in ()).throw(AssertionError('paused')))
    page.evaluate('Date.now = () => 0') # client clock cannot revive an expired invitation
    queue.set_auto('robert',room,{'enabled':True})
    with store.connect() as db:db.execute("UPDATE cos_auto SET expires='2000-01-01T00:00:00+00:00' WHERE room=?",(room,))
    # No scheduler sweep: the raw enabled flag remains true.
    assert queue.status('robert',room)['auto']['enabled']
    expect(page.locator('#cos-auto-status')).to_contain_text('invitation expired',timeout=10000)
    expect(page.locator('#toggle-cos-auto')).to_have_text('Invite CoS to auto-reply')


def test_activity_is_read_only_scoped_and_preserves_room_drafts(live):
    page,store,url,first,second,reviewer_token=live
    store.append('robert','activity-first',first,dict(body='Visible working session update'))
    store.append('robert','activity-private',second,dict(body='Separate private status discussion'))
    before=[len(store.room('robert',r)['events']) for r in [first,second]]
    signin(page,url,reviewer_token,first)
    page.locator('#message-body').fill('Unsent working-room draft')
    page.locator('[data-view="activity"]').click()
    expect(page.locator('#activity-list')).to_contain_text('Visible working session update')
    expect(page.locator('#activity-list')).not_to_contain_text('Separate private status discussion')
    expect(page.locator('#activity-status')).to_contain_text('1 sessions')
    page.locator('#activity-list').get_by_role('button',name='Synthetic design review').click()
    expect(page.locator('#message-body')).to_have_value('Unsent working-room draft')
    assert before==[len(store.room('robert',r)['events']) for r in [first,second]]
    page.locator('[data-view="activity"]').click()
    expect(page.locator('#activity-list')).to_contain_text('Visible working session update')
    page.locator('#logout').click()
    expect(page.locator('#activity-list')).to_be_empty()
    expect(page.locator('#activity-status')).to_be_empty()


def test_activity_keeps_available_sessions_when_one_fails(live):
    page,store,url,first,second,_=live
    store.append('robert','available-activity',first,dict(body='Available session survives another failure'))
    signin(page,url,store.owner_key,first)
    page.route(f'**/api/v1/rooms/{second}',lambda route:route.fulfill(status=500,content_type='application/json',body='{"error":"temporary failure"}'))
    page.locator('[data-view="activity"]').click()
    expect(page.locator('#activity-list')).to_contain_text('Available session survives another failure')
    expect(page.locator('#activity-status')).to_contain_text('1 session(s) unavailable')
    page.unroute(f'**/api/v1/rooms/{second}')
    page.locator('#refresh-activity').click()
    expect(page.locator('#activity-status')).to_contain_text('2 sessions')
    expect(page.locator('#activity-status')).not_to_contain_text('unavailable')


def test_activity_omits_membership_revoked_during_fetch(live):
    page,store,url,first,second,token=live
    store.membership('robert','second-membership',second,dict(actor='reviewer',role='contributor'))
    store.append('robert','revoked-content',second,dict(body='Revoked content must not appear'))
    signin(page,url,token,first)
    def revoke(route):
        store.membership('robert','revoke-on-fetch',second,dict(actor='reviewer',role='remove'))
        route.continue_()
    page.route(f'**/api/v1/rooms/{second}',revoke)
    page.locator('[data-view="activity"]').click()
    expect(page.locator('#activity-status')).to_contain_text('1 sessions')
    expect(page.locator('#activity-list')).not_to_contain_text('Revoked content must not appear')
    expect(page.locator('#activity-list')).not_to_contain_text('Synthetic incident bridge')


def test_coordination_question_answer_and_executive_snapshot(live):
    page,store,url,work,executive,_=live
    queue=store._browser_test_app.extensions['coordination']
    request=queue.create('reviewer','browser-question',work,dict(kind='owner_input',title='Need Robert direction',body='Which option?',assignee='robert'))['result']
    source=store.append('robert','browser-source',work,dict(body='Working-room progress for executive briefing'))['result']
    signin(page,url,store.owner_key,work)
    expect(page.locator('#coordination-inbox')).to_be_visible(timeout=15000)
    expect(page.locator('#coordination-items')).to_contain_text('Need Robert direction',timeout=10000)
    page.locator('#coordination-items').get_by_role('button',name='Answer request').click()
    page.locator('#field-body').fill('Proceed with option A')
    page.locator('#modal-submit').click()
    expect(page.locator('#coordination-items')).to_contain_text('Result submitted',timeout=10000)
    assert queue.list('reviewer',work)['items'][0]['steps'][-1]['actor']=='robert'
    before=store.room('robert',work)['events']
    page.goto(f'{url}/#room/{executive}')
    expect(page.locator('#room-title')).to_have_text('Synthetic incident bridge')
    page.locator('#new-snapshot').click()
    page.locator('#field-source').select_option(work)
    page.locator('#modal-submit').click()
    expect(page.locator('#modal-title')).to_have_text('Select records to disclose')
    page.locator(f'input[value="{source["id"]}"]').check()
    page.locator('#field-disclosure').check()
    page.locator('#modal-submit').click()
    expect(page.locator('#executive-snapshots')).to_contain_text('1 selected records',timeout=10000)
    page.locator('#executive-snapshots summary').click()
    expect(page.locator('#executive-snapshots')).to_contain_text('Working-room progress for executive briefing')
    assert store.room('robert',work)['events']==before
    page.screenshot(path='/private/tmp/records-beta-executive-desktop.png',full_page=True)
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path='/private/tmp/records-beta-executive-mobile.png',full_page=True)
    page.set_viewport_size({'width':1512,'height':1040})
    page.locator('#executive-snapshots').get_by_role('button',name='Send handoff back').click()
    page.locator('#field-title').fill('Authorized next step')
    page.locator('#field-body').fill('Review the revised plan')
    page.locator('#field-assignee').select_option('reviewer')
    page.locator('#modal-submit').click()
    expect(page.locator('#modal')).not_to_be_visible()
    assert queue.list('robert',work)['items'][0]['source_snapshot']
    assert queue.list('robert',work)['items'][0]['state']=='requested'


def test_populated_conversation_composer_and_keyboard(live):
    page,store,url,room,_,_=live
    for n in range(24):store.append('robert',f'layout-{n}',room,dict(body=f'Populated message {n}. '+('Long context for realistic layout. '*8)))
    signin(page,url,store.owner_key,room)
    for width,height in [(1512,1040),(1280,720),(1024,600),(900,500)]:
        page.set_viewport_size(dict(width=width,height=height))
        page.screenshot(path=f'/private/tmp/records-layout-{width}.png',full_page=True)
        expect(page.locator('#send-message')).to_be_in_viewport()
        expect(page.locator('#message-body')).to_be_in_viewport()
        bounds=page.locator('#send-message').bounding_box()
        assert bounds['y']+bounds['height']<=height
        transcript=page.locator('#messages').bounding_box()
        assert transcript['height'] >= (200 if height>=720 else 100), transcript
    page.locator('#message-body').fill('Keyboard send acceptance')
    page.locator('#message-body').press('Enter')
    expect(page.locator('#messages')).to_contain_text('Keyboard send acceptance')
    expect(page.locator('#message-body')).to_have_value('')
    page.locator('#message-body').fill('Line one')
    page.locator('#message-body').press('Shift+Enter')
    expect(page.locator('#message-body')).to_have_value('Line one\n')
    page.screenshot(path='/private/tmp/records-populated-desktop.png',full_page=True)


def test_safe_markdown_keeps_raw_record_and_rejects_active_content(live):
    page,store,url,room,_,_=live
    body='**Bold** and *italic* with `code`\n- First\n- Second\n```python\nprint("hello")\n```\n[Docs](https://example.com/docs)\n[Unsafe](javascript:alert(1))\n<img src=x onerror=alert(1)>'
    event=store.append('robert','markdown-fixture',room,dict(body=body))['result']
    signin(page,url,store.owner_key,room)
    node=page.locator('#event-'+event['id'])
    expect(node.locator('strong')).to_have_text('Bold')
    expect(node.locator('em')).to_have_text('italic')
    expect(node.locator('pre code')).to_contain_text('print("hello")')
    expect(node.locator('li')).to_have_count(2)
    expect(node.locator('a')).to_have_attribute('href','https://example.com/docs')
    expect(node.locator('img,script,iframe')).to_have_count(0)
    expect(node).to_contain_text('[Unsafe](javascript:alert(1))')
    assert store.room('robert',room)['events'][-1]['body']==body


def test_unread_jump_and_mobile_composer(live):
    page,store,url,first,second,_=live
    for n in range(20):store.append('robert',f'nav-{n}',first,dict(body='Earlier conversation '+str(n)+' '+('context '*40)))
    signin(page,url,store.owner_key,first)
    expect(page.locator('#jump-latest')).to_be_hidden()
    expect(page.locator(f'[data-session="{first}"] .unread-badge')).to_have_count(0)
    page.locator('#messages').evaluate('(node)=>node.scrollTop=0')
    expect(page.locator('#jump-latest')).to_be_visible()
    store.append('robert','while-reading',first,dict(body='New message while reading earlier context'))
    expect(page.locator('#messages')).to_contain_text('New message while reading earlier context',timeout=10000)
    assert page.locator('#messages').evaluate('(node)=>node.scrollTop')<100
    page.locator('#jump-latest').click()
    expect(page.locator('#jump-latest')).to_be_hidden()
    store.append('robert','other-session',second,dict(body='Other session has changed'))
    expect(page.locator(f'[data-session="{second}"] .unread-badge')).to_be_visible(timeout=15000)
    page.locator(f'[data-session="{second}"]').click()
    expect(page.locator(f'[data-session="{second}"] .unread-badge')).to_have_count(0)
    for width,height in [(390,844),(375,667),(360,640)]:
        page.set_viewport_size(dict(width=width,height=height))
        bounds=page.locator('#send-message').bounding_box()
        assert bounds['y']+bounds['height']<=height, (width,height,bounds)
        expect(page.locator('#message-body')).to_be_in_viewport()
    store.append('robert','return-unread',first,dict(body='Added while you were in another room'))
    page.set_viewport_size(dict(width=390,height=844))
    page.locator('#mobile-rooms').click()
    expect(page.locator(f'[data-session="{first}"]')).to_be_visible()
    page.locator(f'[data-session="{first}"]').click()
    expect(page.locator('.sidebar')).to_be_hidden()
    expect(page.locator('.unread-divider')).to_have_count(1)
    expect(page.locator('#jump-latest')).to_be_hidden()
    expect(page.locator('#send-message')).to_be_in_viewport()
    page.screenshot(path='/private/tmp/records-mobile-navigation.png',full_page=True)
    page.locator('#mobile-rooms').click()
    page.locator('[data-view="activity"]').click()
    expect(page.locator('#activity-view')).to_be_visible()
    expect(page.locator('[data-view="rooms"]')).to_be_visible()
    page.locator('[data-view="rooms"]').click()
    expect(page.locator('#send-message')).to_be_in_viewport()


def test_markdown_link_shows_actual_destination(live):
    page,store,url,room,_,_=live
    store.append('robert','misleading-link',room,dict(body='[https://bank.example/login](https://evil.example/steal)'))
    signin(page,url,store.owner_key,room)
    link=page.locator('#messages a[href="https://evil.example/steal"]')
    expect(link).to_have_text('https://bank.example/login (evil.example)')
    expect(link).to_have_attribute('title','https://evil.example/steal')


def test_malformed_snapshot_keeps_requests_available(live):
    page,store,url,work,executive,_=live
    queue=store._browser_test_app.extensions['coordination']
    store.membership('robert','reviewer-executive',executive,dict(actor='reviewer',role='contributor'))
    queue.create('reviewer','malformed-question',executive,dict(kind='owner_input',title='Still needs an answer',body='Can you read this?',assignee='robert'))
    source=store.append('robert','malformed-source',work,dict(body='Source'))['result']
    queue.snapshot('robert','malformed-capture',executive,dict(source=work,event_ids=[source['id']],disclosure_acknowledged=True))
    with store.connect() as db:db.execute('UPDATE executive_snapshots SET records=? WHERE room=?',('not-json',executive))
    signin(page,url,store.owner_key,executive)
    expect(page.locator('#executive-snapshots')).to_contain_text('Briefing contents unavailable',timeout=10000)
    expect(page.locator('#coordination-items')).to_contain_text('Still needs an answer')
    expect(page.locator('#coordination-items').get_by_role('button',name='Answer request')).to_be_enabled()


def test_answer_owner_request_directly_from_inbox(live):
    page,store,url,work,other,_=live
    queue=store._browser_test_app.extensions['coordination']
    request=queue.create('reviewer','inbox-direct',work,dict(kind='owner_input',title='Confirm delivery',body='Please answer Received',assignee='robert'))['result']
    signin(page,url,store.owner_key,other)
    page.locator('#coordination-inbox').click(timeout=15000)
    page.locator('#modal').get_by_role('button',name='Answer request',exact=True).click()
    expect(page.locator('#modal-title')).to_have_text('Answer request')
    page.get_by_label('Your answer',exact=True).fill('Received')
    page.locator('#modal-submit').click()
    expect(page.locator('#modal')).not_to_be_visible()
    item=next(i for i in queue.list('reviewer',work)['items'] if i['id']==request['id'])
    assert item['state']=='result_submitted'
    assert item['steps'][-1]['body']=='Received' and item['steps'][-1]['actor']=='robert'
    assert queue.list('robert',other)['items']==[]
