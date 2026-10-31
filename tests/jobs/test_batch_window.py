from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from core.jobs import batch_window as W
D=datetime(2026,10,14,6,tzinfo=ZoneInfo('America/Chicago')).timestamp()
def test_unchanged_without_deadline():
 assert W.remaining(120,environ={},clock=lambda:D+100)==120
@pytest.mark.parametrize('raw',['bad','inf','nan','0',str(D+1),str(D+86400)])
def test_invalid_deadline_closed(raw):
 with pytest.raises(W.BatchDeadline,match='batch_deadline_invalid'):W.remaining(environ={W.ENV:raw},clock=lambda:D-100)
def test_remaining_and_exact_boundary():
 assert W.remaining(120,environ={W.ENV:str(D)},clock=lambda:D-17)==17
 with pytest.raises(W.BatchDeadline,match='outside_window'):W.remaining(environ={W.ENV:str(D)},clock=lambda:D)
def test_dst_boundary_is_local_six():
 d=datetime(2026,11,1,6,tzinfo=ZoneInfo('America/Chicago')).timestamp()
 assert W.remaining(120,environ={W.ENV:str(d)},clock=lambda:d-60)==60
