"""Request-bound proposals, atomic Board records, no real models or network."""
import json
import pytest
from minimoi_portal.guild_ui.mc import board_outcomes as O
from minimoi_portal.guild_ui.mc.backend import Health, TurnResult, MasterCraftsmanBackend
from minimoi_portal.guild_ui.stores import MASTER_CRAFTSMAN
from board_media_helpers import board, key
from floor_helpers import load_portal, write_headers
from floor_db_helpers import floor_db, floored, keyed
from test_mc_turns import _keep, _ask, _turn_on


def source(board):
    return board.floor.add_postit("Check the actual dev backends", board.author, idempotency_key=key()).value


def proposal(row, **changes):
    return json.dumps(dict(action="report_board_outcome",source_id=row["id"],version=row["version"],
                          answer="Three backends could not be resolved.",outcome="Dev backend access blocked; no config changes.",**changes))


def test_prepare_requires_explicit_owner_write_command(board):
    row = source(board)
    for text in (f"What is Board note #{row['id']}?", f'He said "Leave the outcome on the Board." Board #{row["id"]}',
                 f"Board #{row['id']}. Do not record the outcome on the Board."):
        assert O.prepare(text, board.floor, "owner-note-1") is None
    plan = O.prepare(f"Pick up Board note #{row['id']}. Leave the outcome on the Board.", board.floor, "owner-note-1")
    assert plan.sources == ((row["id"],row["version"]),)
    assert O.parse(plan, "Need clarification first") is None


def test_commit_requires_clean_kept_answer_and_is_idempotent(board):
    row = source(board)
    plan = O.Plan(((row["id"],row["version"]),),"owner-note-1")
    parsed = O.parse(plan, proposal(row))
    assert O.commit(plan,parsed,board.floor,clean=False,answer_kept=True)["state"] == "not_saved"
    assert O.commit(plan,parsed,board.floor,clean=True,answer_kept=False)["state"] == "not_saved"
    saved = O.commit(plan,parsed,board.floor,clean=True,answer_kept=True)
    assert saved["state"] == "saved"
    rows = {r["id"]:r for r in board.floor.board().data["active"]}
    assert rows[saved["postit_id"]]["author"] == MASTER_CRAFTSMAN.id
    assert rows[row["id"]]["text"] == row["text"] and rows[row["id"]]["version"] == row["version"]
    # An exact replay returns its existing receipt even after a source edit.
    board.floor.set_text(row["id"], "Edited after the report", board.author, expect_version=row["version"],idempotency_key=key())
    again = O.commit(plan,parsed,board.floor,clean=True,answer_kept=True)
    assert again["postit_id"] == saved["postit_id"] and again["repeated"]
    changed = dict(parsed,text="Changed replay must not create a second outcome")
    assert O.commit(plan,changed,board.floor,clean=True,answer_kept=True)["reason"] == "idempotency_mismatch"


def test_source_edit_or_bin_before_commit_refuses(board):
    row = source(board)
    plan = O.Plan(((row["id"],row["version"]),),"owner-note-2")
    parsed = O.parse(plan,proposal(row))
    board.floor.set_text(row["id"], "Owner changed the task",board.author,expect_version=row["version"],idempotency_key=key())
    assert O.commit(plan,parsed,board.floor,clean=True,answer_kept=True)["reason"] == "conflict"
    other = source(board)
    board.floor.bin_postit(other["id"],board.author,idempotency_key=key())
    assert board.floor.add_outcome(other["id"],other["version"],"No write",turn_key="different").outcome == "conflict"


@pytest.mark.parametrize("mutate",[
    lambda d:d.update(source_id=True),lambda d:d.update(version=True),lambda d:d.update(source_id=99999),
    lambda d:d.update(author="robert"),lambda d:d.update(action="delete"),lambda d:d.update(outcome="x"*300),
    lambda d:d.update(answer=""),lambda d:d.update(outcome=[]),
])
def test_untrusted_proposals_cannot_change_scope(board,mutate):
    row = source(board);plan=O.Plan(((row["id"],row["version"]),),"owner-note-3")
    d=json.loads(proposal(row));mutate(d)
    with pytest.raises(ValueError):O.parse(plan,json.dumps(d))


def test_duplicate_json_keys_refused(board):
    row=source(board);plan=O.Plan(((row["id"],row["version"]),),"owner-note-4")
    with pytest.raises(ValueError):O.parse(plan,proposal(row).replace('"action":', '"action":"delete","action":'))


@pytest.mark.parametrize("completion", ["stop", None, "length", "none"])
@pytest.mark.parametrize("fenced", [False, True])
def test_real_portal_turn_keeps_answer_then_records_mc_outcome(floored,monkeypatch,completion,fenced):
    services=floored.app.extensions["guild_ui_next"]["services"]
    floor=services.floor
    row=floor.add_postit("Check the dev domains",MASTER_CRAFTSMAN,idempotency_key="test-source-01").value
    class Backend(MasterCraftsmanBackend):
        kind="cos"
        def health(self):return Health("ready",reachable=True)
        def turn(self,req,cancel=None):
            assert "report_board_outcome" in req.text and "Check the dev domains" in req.text
            return TurnResult("answered","cos",text=("```json\n" + proposal(row) + "\n```") if fenced else proposal(row), trace={"finish_reason":completion} if completion is not None else None)
    _turn_on(floored,Backend())
    floored.app.extensions["guild_ui_next"]["feature_environ"]["MINIMOI_MC_BOARD_OUTCOMES"]="1"
    client=floored.owner();token=floored.csrf(client)
    note=_keep(client,token,f"Please pick up Board note #{row['id']}. Leave the outcome on the Board.")
    response=_ask(client,token,note["request_id"])
    body=response.get_json()
    assert response.status_code==200 and body["status"]=="answered",body
    assert body["reply_note"]["text"]=="Three backends could not be resolved."
    if completion != "stop":
        assert body["board_outcome"]["state"] == "not_saved"
        assert len(floor.board().data["active"]) == 1
        return
    assert body["board_outcome"]["state"]=="saved"
    rows=floor.board().data["active"]
    assert len(rows)==2 and any(r["text"].startswith(f"Re Board note #{row['id']}:") for r in rows)
    repeat=_ask(client,token,note["request_id"]).get_json()
    assert repeat["repeated"] and len(floor.board().data["active"])==2
    assert repeat["board_outcome"]["postit_id"] == body["board_outcome"]["postit_id"]


def test_other_floor_cannot_write_an_outcome_for_this_source(board):
    row=source(board)
    other=board.floor.for_floor("another-floor")
    assert other.add_outcome(row["id"],row["version"],"Attempted cross-floor outcome",turn_key="owner-note-x").outcome=="not_found"
    assert other.outcome_receipt(row["id"],turn_key="owner-note-x")["state"]=="not_saved"
    assert len(board.floor.board().data["active"])==1


def test_historical_receipt_does_not_mutate_source_or_retry(board):
    row=source(board)
    assert board.floor.outcome_receipt(row["id"],turn_key="not-run")["state"]=="not_recorded"
    first=board.floor.add_outcome(row["id"],row["version"],"Recorded report",turn_key="saved-turn")
    board.floor.bin_postit(first.value["id"],board.author,idempotency_key=key())
    receipt=board.floor.outcome_receipt(row["id"],turn_key="saved-turn")
    assert receipt["state"]=="saved" and receipt["postit_id"]==first.value["id"]
    assert board.floor.board().data["counts"]=={"active":1,"done":0,"trash":1}


@pytest.mark.parametrize("wrap", [
    lambda text: "Now recording the outcome:\n```json\n" + text + "\n```",
    lambda text: "All checked. " + text,
])
def test_wrapped_proposal_is_refused_not_displayed_or_extracted(board, wrap):
    row = source(board)
    plan = O.Plan(((row["id"], row["version"]),), "wrapped-turn")
    with pytest.raises(ValueError, match="whole response"):
        O.parse(plan, wrap(proposal(row)))
    assert len(board.floor.board().data["active"]) == 1


def test_portal_hides_fenced_command_and_does_not_write_outcome(floored):
    services = floored.app.extensions["guild_ui_next"]["services"]
    floor = services.floor
    row = floor.add_postit("Check dev domains", MASTER_CRAFTSMAN, idempotency_key="wrapped-source").value
    class Backend(MasterCraftsmanBackend):
        kind = "cos"
        def health(self): return Health("ready", reachable=True)
        def turn(self, req, cancel=None):
            return TurnResult("answered", "cos", text="Now recording:\n```json\n" + proposal(row) + "\n```", trace={"finish_reason": "stop"})
    _turn_on(floored, Backend())
    floored.app.extensions["guild_ui_next"]["feature_environ"]["MINIMOI_MC_BOARD_OUTCOMES"] = "1"
    client = floored.owner(); token = floored.csrf(client)
    note = _keep(client, token, f"Pick up Board note #{row['id']}. Leave the outcome on the Board.")
    body = _ask(client, token, note["request_id"]).get_json()
    assert "Nothing was saved" in body["reply_note"]["text"]
    assert "report_board_outcome" not in body["reply_note"]["text"]
    assert body["board_outcome"]["state"] == "not_saved"
    assert len(floor.board().data["active"]) == 1


def test_exact_json_fence_retains_authority_and_validation(board):
    row = source(board)
    plan = O.Plan(((row["id"], row["version"]),), "fenced-owner-turn")
    text = "```json\n" + proposal(row) + "\n```"
    parsed = O.parse(plan, text)
    assert O.parse(None, text) is None
    assert O.commit(plan, parsed, board.floor, clean=False, answer_kept=True)["state"] == "not_saved"
    saved = O.commit(plan, parsed, board.floor, clean=True, answer_kept=True)
    assert saved["state"] == "saved"
    again = O.commit(plan, parsed, board.floor, clean=True, answer_kept=True)
    assert again["postit_id"] == saved["postit_id"] and again["repeated"]
    assert len(board.floor.board().data["active"]) == 2
    for bad in (text + " trailing prose", "prefix\n" + text, text + "\n" + text,
                text.replace("```json", "```python"),
                text.replace('"source_id": ' + str(row["id"]), '"source_id": true'),
                text.replace('"action":', '"action":"delete","action":')):
        with pytest.raises(ValueError):
            O.parse(plan, bad)
    with pytest.raises(ValueError, match="too large"):
        O.parse(plan, " " * 16000 + text)
