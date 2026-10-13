"""The Board's placement geometry (static/js/board_layout.js), run in node: no overlap, nearest free spot, first-free-cell flow, keyboard steps,
and the snapshot on the first move."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[3] / "minimoi_portal" / "guild_ui" / "static" / "js" / "board_layout.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def run(script: str):
    code = f"import * as L from {json.dumps(MODULE.as_uri())};\nconst out = (() => {{ {script} }})();\nconsole.log(JSON.stringify(out));"
    done = subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_overlap_is_strict_so_touching_notes_are_fine():
    r = run("return [L.overlaps({x:0,y:0},{x:179,y:0}), L.overlaps({x:0,y:0},{x:180,y:0}), L.overlaps({x:0,y:0},{x:0,y:169}), L.overlaps({x:0,y:0},{x:180,y:170})];")
    assert r == [True, False, True, False]


def test_nearest_free_keeps_a_free_spot_and_otherwise_finds_the_closest_free_one():
    assert run("return L.nearestFree({x:105,y:98}, []);") == {"x": 100, "y": 100}                              # snapped
    taken = "[{x:100,y:100}]"
    r = run(f"return L.nearestFree({{x:110,y:110}}, {taken});")
    assert r != {"x": 100, "y": 100}
    assert abs(r["x"] - 100) >= 180 or abs(r["y"] - 100) >= 170                                                # not overlapping
    assert run(f"const t={taken}; const p=L.nearestFree({{x:110,y:110}}, t); return L.isFree(p, t);") is True
    # clamped into the board
    far = run("return L.nearestFree({x:5000,y:-50}, []);")
    assert far == {"x": 1020, "y": 0}


def test_a_blocked_row_pushes_a_drop_to_the_nearest_gap_not_on_top():
    r = run("const t=[0,180,360,540,720,900,1020].map(x=>({x,y:0})); return L.nearestFree({x:500,y:10}, t);")
    assert r["y"] >= 170                                                                                        # below the full row


def test_unplaced_notes_flow_into_the_first_free_cells_in_list_order_and_never_touch_saved_ones():
    notes = "[{id:1},{id:2,pos_x:210,pos_y:10},{id:3},{id:4}]"
    r = run(f"const m = L.layoutAll({notes}); return [...m.entries()];")
    pos = {k: v for k, v in r}
    assert pos[2] == {"x": 210, "y": 10, "placed": True}
    assert pos[1] == {"x": 10, "y": 10, "placed": False}                                                        # first cell
    assert pos[3]["x"] >= 390 or pos[3]["y"] > 10                                                               # not on note 2 (240..440)
    assert pos[4] != pos[3]
    all_ = list(pos.values())
    for i, a in enumerate(all_):
        for b in all_[i + 1:]:
            assert abs(a["x"] - b["x"]) >= 180 or abs(a["y"] - b["y"]) >= 170


def test_a_full_first_row_wraps_to_the_next_and_the_board_grows():
    notes = "[" + ",".join(f"{{id:{i}}}" for i in range(1, 9)) + "]"
    r = run(f"const m = L.layoutAll({notes}); return [[...m.values()].map(p=>[p.x,p.y]), L.boardHeight(m)];")
    cells, height = r
    assert cells[:6] == [[10 + 200 * i, 10] for i in range(6)] and cells[6] == [10, 200]      # six across, then the next row
    assert height >= 200 + 170 + 40


def test_keyboard_steps_move_one_step_and_refuse_a_blocked_or_off_board_step():
    assert run("return L.stepFrom({x:100,y:100}, 'ArrowRight', []);") == {"x": 120, "y": 100}
    assert run("return L.stepFrom({x:100,y:100}, 'ArrowDown', [], true);") == {"x": 100, "y": 200}
    assert run("return L.stepFrom({x:0,y:0}, 'ArrowLeft', []);") is None
    assert run("return L.stepFrom({x:0,y:0}, 'ArrowRight', [{x:180,y:0}]);") is None


def test_the_first_move_saves_every_note_where_it_is_drawn_and_later_ones_only_the_moved_note():
    first = run("const m=L.layoutAll([{id:1},{id:2}]); return L.movesFor(2,{x:400,y:20}, m, false);")
    assert first == [{"id": 1, "x": 10, "y": 10}, {"id": 2, "x": 400, "y": 20}]
    later = run("const m=L.layoutAll([{id:1,pos_x:20,pos_y:20},{id:2,pos_x:240,pos_y:20}]); return L.movesFor(2,{x:400,y:20}, m, true);")
    assert later == [{"id": 2, "x": 400, "y": 20}]



def test_variable_geometry_affects_collision_movement_layout_and_height():
    assert run("return L.overlaps({x:0,y:0,w:400,h:300},{x:220,y:200});") is True
    assert run("return L.inside({x:801,y:0,w:400,h:300});") is False
    assert run("return L.stepFrom({x:800,y:0,w:400,h:300},'ArrowRight',[]);") is None
    assert run("return L.nearestFree({x:5000,y:0,w:400,h:300},[]);") == {"x":800,"y":0,"w":400,"h":300}
    assert run("const m=L.layoutAll([{id:1,pos_x:0,pos_y:500,size_w:400,size_h:450},{id:2}]); return [L.boardHeight(m),L.overlaps(m.get(1),m.get(2))];") == [990,False]


def test_two_hundred_mixed_cards_fit_without_overlap_and_restore_collision_reflows():
    assert run("const n=Array.from({length:200},(_,i)=>({id:i,size_w:i%2?400:180,size_h:i%2?300:170}));const p=[...L.layoutAll(n).values()];return p.every((a,i)=>L.inside(a)&&p.every((b,j)=>i===j||!L.overlaps(a,b)));") is True
    assert run("const p=[...L.layoutAll([{id:1,pos_x:0,pos_y:0,size_w:400,size_h:300},{id:2,pos_x:220,pos_y:0}]).values()];return !L.overlaps(p[0],p[1]);") is True



def test_client_and_server_rectangle_vectors_match():
    from minimoi_portal.guild_ui import stores
    from minimoi_portal.guild_ui.board import BoardMixin
    vectors=[((0,0,400,300),(400,0,140,130)),((0,0,400,300),(399,299,140,130)),((100,100,140,130),(240,100,480,450)),((0,0,480,450),(40,450,180,170))]
    for a,b in vectors:
        aa=dict(zip(('x','y','w','h'),a));bb=dict(zip(('x','y','w','h'),b))
        assert run(f'return L.overlaps({json.dumps(aa)},{json.dumps(bb)});')==BoardMixin._overlap(a,b)
