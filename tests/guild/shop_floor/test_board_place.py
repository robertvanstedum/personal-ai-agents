"""Board free placement (11 Oct, BOARD_SIMPLE_NOTEPAD_PROPOSAL): where a note sits on a 1200-wide logical board. Placement is one transaction,
idempotent, checked against the arrangement revision, refuses an overlap, never touches a note's text version and survives Done/Trash."""
from __future__ import annotations

import json
import uuid

import pytest

from minimoi_portal.guild_ui import stores  # noqa: F401  (imports the board module in the order it needs)
from minimoi_portal.guild_ui.board import BOARD_MAX_Y, BOARD_W, NOTE_H, NOTE_W

from test_board_api import API, _add, _board, _post, board  # noqa: F401  (fixtures)
from floor_helpers import write_headers
from floor_helpers import load_portal  # noqa: F401  (pytest fixture)
from floor_db_helpers import floor_db, floored  # noqa: F401  (pytest fixtures)
from test_phone_briefing_no_paid_calls import no_outbound  # noqa: F401


def place(client, token, moves, rev, **kw):
    return _post(client, token, "/postits/layout", moves=moves, expect_order_rev=rev, **kw)


def test_a_new_board_has_no_positions_and_placing_saves_them_without_touching_versions(board):
    client = board.owner()
    token = board.csrf(client)
    a, b = _add(client, token, "a"), _add(client, token, "b")
    now = _board(client)
    assert all(p["pos_x"] is None and p["pos_y"] is None for p in now["active"])
    r = place(client, token, [{"id": a["id"], "x": 20, "y": 20}, {"id": b["id"], "x": 240, "y": 20}], now["order_rev"])
    assert r.status_code == 200 and r.get_json()["result"] == "moved"
    assert r.get_json()["layout"]["positions"] == {str(a["id"]): [20, 20], str(b["id"]): [240, 20]}
    after = {p["id"]: p for p in _board(client)["active"]}
    assert (after[a["id"]]["pos_x"], after[a["id"]]["pos_y"]) == (20, 20)
    assert after[a["id"]]["version"] == a["version"]                                  # a placement is not a text change
    assert _board(client)["order_rev"] == now["order_rev"] + 1


def test_a_stale_arrangement_conflicts_and_returns_the_current_one(board):
    client = board.owner()
    token = board.csrf(client)
    a, b = _add(client, token, "a"), _add(client, token, "b")
    rev = _board(client)["order_rev"]
    assert place(client, token, [{"id": a["id"], "x": 0, "y": 0}], rev).status_code == 200
    late = place(client, token, [{"id": b["id"], "x": 400, "y": 0}], rev)
    assert late.status_code == 409 and late.get_json()["result"] == "conflict"
    assert late.get_json()["layout"]["positions"] == {str(a["id"]): [0, 0]}


def test_an_overlap_is_refused_and_a_touching_neighbour_is_fine(board):
    client = board.owner()
    token = board.csrf(client)
    a, b = _add(client, token, "a"), _add(client, token, "b")
    rev = _board(client)["order_rev"]
    place(client, token, [{"id": a["id"], "x": 100, "y": 100}], rev)
    rev = _board(client)["order_rev"]
    bad = place(client, token, [{"id": b["id"], "x": 100 + NOTE_W - 1, "y": 100 + NOTE_H - 1}], rev)
    assert bad.status_code == 409 and bad.get_json()["result"] == "occupied"
    assert _board(client)["order_rev"] == rev and [p["pos_x"] for p in _board(client)["active"] if p["id"] == b["id"]] == [None]
    ok = place(client, token, [{"id": b["id"], "x": 100 + NOTE_W, "y": 100 + NOTE_H}], rev)               # exactly touching corners
    assert ok.status_code == 200
    rev = _board(client)["order_rev"]
    two = place(client, token, [{"id": a["id"], "x": 0, "y": 0}, {"id": b["id"], "x": 10, "y": 10}], rev)  # two moves that collide with each other
    assert two.status_code == 409 and two.get_json()["result"] == "occupied"


@pytest.mark.parametrize("moves", [[], "x", [{"id": 1}], [{"id": 1, "x": -1, "y": 0}], [{"id": 1, "x": BOARD_W - NOTE_W + 1, "y": 0}],
                                   [{"id": 1, "x": 0, "y": BOARD_MAX_Y + 1}], [{"id": 1, "x": 0, "y": 0}, {"id": 1, "x": 220, "y": 0}],
                                   [{"id": 1, "x": 1.5, "y": 0}], [{"id": True, "x": 0, "y": 0}], [{"id": 1, "x": "0", "y": 0}]])
def test_bad_placements_are_422_and_change_nothing(board, moves):
    client = board.owner()
    token = board.csrf(client)
    _add(client, token, "a")
    rev = _board(client)["order_rev"]
    assert place(client, token, moves, rev).status_code == 422
    assert _board(client)["order_rev"] == rev


def test_an_unknown_note_is_404_and_a_binned_note_cannot_be_placed(board):
    client = board.owner()
    token = board.csrf(client)
    a = _add(client, token, "a")
    rev = _board(client)["order_rev"]
    assert place(client, token, [{"id": 999999, "x": 0, "y": 0}], rev).status_code == 404
    assert _post(client, token, f"/postits/{a['id']}/bin").status_code == 200
    rev = _board(client)["order_rev"]
    assert place(client, token, [{"id": a["id"], "x": 0, "y": 0}], rev).status_code == 404


def test_a_retry_with_the_same_key_is_the_same_answer_and_a_changed_body_is_refused(board):
    client = board.owner()
    token = board.csrf(client)
    a = _add(client, token, "a")
    rev = _board(client)["order_rev"]
    key = uuid.uuid4().hex
    body = dict(moves=[{"id": a["id"], "x": 20, "y": 20}], expect_order_rev=rev, idempotency_key=key)
    first = client.post(f"{API}/postits/layout", json=body, headers=write_headers(token))
    again = client.post(f"{API}/postits/layout", json=body, headers=write_headers(token))
    assert first.status_code == again.status_code == 200 and again.get_json()["repeated"] is True
    assert _board(client)["order_rev"] == rev + 1                                            # applied once
    other = client.post(f"{API}/postits/layout", json={**body, "moves": [{"id": a["id"], "x": 40, "y": 20}]}, headers=write_headers(token))
    assert other.status_code == 409 and other.get_json()["result"] == "idempotency_mismatch"


def test_placing_needs_the_write_guards(board):
    client = board.owner()
    token = board.csrf(client)
    a = _add(client, token, "a")
    rev = _board(client)["order_rev"]
    moves = [{"id": a["id"], "x": 0, "y": 0}]
    assert _post(client, token, "/postits/layout", mode="off_record", moves=moves, expect_order_rev=rev).status_code == 409   # Private writes nothing
    assert client.post(f"{API}/postits/layout", json={"moves": moves, "expect_order_rev": rev, "idempotency_key": "abcdefgh1234"}).status_code == 403   # no CSRF
    anon = board.app.test_client().post(f"{API}/postits/layout", json={"moves": moves, "expect_order_rev": rev, "idempotency_key": "abcdefgh1234"})
    assert anon.status_code in (401, 403)


def test_positions_survive_done_and_restore_and_leave_the_text_alone(board):
    client = board.owner()
    token = board.csrf(client)
    a = _add(client, token, "keep my spot")
    rev = _board(client)["order_rev"]
    place(client, token, [{"id": a["id"], "x": 440, "y": 220}], rev)
    assert _post(client, token, f"/postits/{a['id']}/bin").status_code == 200
    assert _post(client, token, f"/postits/{a['id']}/restore").status_code == 200
    note = _board(client)["active"][0]
    assert (note["pos_x"], note["pos_y"]) == (440, 220) and note["text"] == "keep my spot"


@pytest.mark.parametrize("w,h", [(139,170),(481,170),(180,129),(180,451),(True,170),(180,1.5),(180,None)])
def test_size_limits_refuse_invalid_values_without_layout_change(board,w,h):
    c=board.owner(); t=board.csrf(c); a=_add(c,t,"size"); rev=_board(c)["order_rev"]
    assert place(c,t,[{"id":a["id"],"x":0,"y":0,"w":w,"h":h}],rev).status_code == 422
    assert _board(c)["order_rev"] == rev


def test_resize_is_persistent_and_moves_keep_the_saved_size(board):
    c=board.owner(); t=board.csrf(c); a=_add(c,t,"size"); rev=_board(c)["order_rev"]
    fingerprint=c.get(f"{API}/board/rev").get_json()
    body=dict(moves=[{"id":a["id"],"x":0,"y":0,"w":400,"h":300}],expect_order_rev=rev,idempotency_key=uuid.uuid4().hex)
    first=c.post(f"{API}/postits/layout",json=body,headers=write_headers(t)); assert first.status_code == 200
    assert c.post(f"{API}/postits/layout",json=body,headers=write_headers(t)).get_json()["repeated"]
    b=_board(c); card=b["active"][0]; assert (card["size_w"],card["size_h"],card["version"]) == (400,300,a["version"])
    assert c.get(f"{API}/board/rev").get_json() != fingerprint
    assert place(c,t,[{"id":a["id"],"x":801,"y":0}],b["order_rev"]).status_code == 422
    assert place(c,t,[{"id":a["id"],"x":800,"y":20}],b["order_rev"]).status_code == 200
    assert _board(c)["active"][0]["size_w"] == 400
    assert place(c,t,[{"id":a["id"],"x":0,"y":0,"w":180,"h":170}],rev).status_code == 409


def test_growing_into_neighbour_is_refused_and_shrinking_frees_space(board):
    c=board.owner(); t=board.csrf(c); a=_add(c,t,"a"); b=_add(c,t,"b"); rev=_board(c)["order_rev"]
    assert place(c,t,[{"id":a["id"],"x":0,"y":0},{"id":b["id"],"x":220,"y":0}],rev).status_code==200
    rev=_board(c)["order_rev"]
    r=place(c,t,[{"id":a["id"],"x":0,"y":0,"w":300,"h":200}],rev)
    assert r.status_code==409 and r.get_json()["result"]=="occupied"
    assert r.get_json()["layout"]["sizes"][str(a["id"])] == [180,170]
    assert place(c,t,[{"id":a["id"],"x":0,"y":0,"w":140,"h":130}],rev).status_code==200
    rev=_board(c)["order_rev"]
    assert place(c,t,[{"id":b["id"],"x":140,"y":0}],rev).status_code==200
    assert _post(c,t,f"/postits/{a['id']}/bin").status_code==200
    assert _post(c,t,f"/postits/{a['id']}/restore").status_code==200
    assert next(p for p in _board(c)["active"] if p["id"]==a["id"])["size_w"]==140


def test_restore_large_note_finds_a_free_position_without_shifting_neighbour(board):
    c=board.owner();t=board.csrf(c);a=_add(c,t,'large');b=_add(c,t,'neighbour');rev=_board(c)['order_rev']
    assert place(c,t,[{'id':a['id'],'x':0,'y':0,'w':400,'h':300},{'id':b['id'],'x':500,'y':0}],rev).status_code==200
    _post(c,t,f"/postits/{a['id']}/bin")
    assert place(c,t,[{'id':b['id'],'x':0,'y':0}],_board(c)['order_rev']).status_code==200
    assert _post(c,t,f"/postits/{a['id']}/restore").status_code==200
    cards={p['id']:p for p in _board(c)['active']}
    assert cards[a['id']]['size_w']==400 and (cards[b['id']]['pos_x'],cards[b['id']]['pos_y'])==(0,0)
    from minimoi_portal.guild_ui.board import BoardMixin
    assert not BoardMixin._overlap((cards[a['id']]['pos_x'],cards[a['id']]['pos_y'],400,300),(0,0,180,170))
