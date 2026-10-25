import json
import time
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from minimoi_portal.guild_ui.mc_recall import Source
from core.mc_tools.recall import Unavailable

C='c-aaaaaaaaaaaa'
def fixture(tmp_path):
    folder=tmp_path/'conversations';folder.mkdir()
    data=dict(id=C,principal='robert',legacy=False,archived=False,scope='operate',notes_floor='guild/'+C)
    (folder/(C+'.json')).write_text(json.dumps(data))
    services=SimpleNamespace(store=SimpleNamespace(folder=str(tmp_path),path='synthetic'),floor=SimpleNamespace(floor='guild',_database_url=lambda:'synthetic'))
    s=Source(services,'robert');s.begin(time.monotonic()+2);return s,folder

def test_no_migration_or_source_writes_and_bad_directory_is_unavailable(tmp_path):
    s,folder=fixture(tmp_path);before={p.name:p.read_bytes() for p in folder.iterdir()}
    assert len(s.conversations())==1
    assert before=={p.name:p.read_bytes() for p in folder.iterdir()}
    for i in range(1001):(folder/str(i)).touch()
    with pytest.raises(Unavailable,match='directory_bound'):s.conversations()

def test_nonblocking_database_wait_obeys_remaining_deadline(tmp_path):
    from psycopg2 import extensions
    s,_=fixture(tmp_path);s.conn=SimpleNamespace(poll=lambda:extensions.POLL_READ,fileno=lambda:42)
    with patch('minimoi_portal.guild_ui.mc_recall.time.monotonic',side_effect=[0,0,3]),patch('minimoi_portal.guild_ui.mc_recall.select.select') as wait:
        s.deadline=2
        with pytest.raises(Unavailable,match='deadline'):s.wait()
        wait.assert_called_once_with([42],[],[],2)

def test_no_connection_after_deadline(tmp_path):
    s,_=fixture(tmp_path);s.deadline=0
    with patch('minimoi_portal.guild_ui.mc_recall.psycopg2.connect') as connect:
        with pytest.raises(Unavailable):s.run(lambda q:q('SELECT 1'))
        connect.assert_not_called()


def test_unknown_string_scope_excluded_but_bad_floor_and_schema_refused(tmp_path):
    from minimoi_portal.guild_ui.mc_recall import Excluded
    s,folder=fixture(tmp_path);path=folder/(C+'.json');original=json.loads(path.read_text())
    path.write_text(json.dumps({**original,'scope':'other-domain'}))
    assert s.conversations()==[]
    with pytest.raises(Unavailable):s.required(C)
    path.write_text(json.dumps({**original,'scope':'other-domain','notes_floor':'cos'}))
    with pytest.raises(Unavailable):s.conversations()
    path.write_text(json.dumps({**original,'scope':[]}))
    with pytest.raises(Unavailable):s.conversations()
