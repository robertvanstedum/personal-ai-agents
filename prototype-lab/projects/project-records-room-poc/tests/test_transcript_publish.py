import json
import pytest
from test_transcript_snapshot import source
from store import Store, Problem
from transcript_publish import publish, recover, verify


def test_publication_idempotent_private_and_final(source):
    store,room=source
    first=publish(store,"robert",room)
    before=(first/"manifest.json").read_bytes()
    assert publish(store,"robert",room)==first
    assert (first/"manifest.json").read_bytes()==before
    assert verify(first)["publication_status"]=="in_progress"
    assert first.stat().st_mode&0o077==0
    for name in ("manifest.json","transcript.json","transcript.md"):
        assert (first/name).stat().st_mode&0o077==0
    store.state("robert","close",room,dict(state="closed",version=1,checkpoint="Done"))
    final=publish(store,"robert",room)
    assert final!=first and first.exists()
    assert verify(final)["publication_status"]=="final"


@pytest.mark.parametrize("point",["after_queue","before_rename","after_rename"])
def test_interrupted_publication_restarts_without_recapture(source,point):
    store,room=source
    def fault(stage):
        if stage==point: raise RuntimeError("Synthetic interruption")
    with pytest.raises(RuntimeError): publish(store,"robert",room,fault=fault)
    store.append("robert","later",room,dict(body="Not part of queued snapshot"))
    restored=Store(store.root)
    results=recover(restored,"robert")
    assert len(results)==1
    assert "Not part" not in (results[0]/"transcript.md").read_text()
    verify(results[0])
    assert recover(restored,"robert")==[]
    assert publish(restored,"robert",room)!=results[0]


def test_tampered_bundle_refused(source):
    store,room=source
    path=publish(store,"robert",room)
    (path/"transcript.md").write_text("Tampered")
    with pytest.raises(ValueError,match="integrity"): verify(path)
    with pytest.raises(ValueError,match="integrity"): publish(store,"robert",room)


def test_nonowner_and_symlink_destination_denied(source,tmp_path):
    store,room=source
    with pytest.raises(Problem): publish(store,"cos-dev",room)
    with pytest.raises(Problem): recover(store,"cos-dev")
    (store.root/"transcripts").symlink_to(tmp_path,target_is_directory=True)
    with pytest.raises(ValueError): publish(store,"robert",room)


def test_operator_cli_publishes_and_recovers(source):
    import subprocess
    import sys
    from pathlib import Path
    store,room=source
    cli=Path(__file__).resolve().parents[1]/"manage.py"
    output=subprocess.run([sys.executable,str(cli),"publish-transcript","--data-dir",str(store.root),
                           "--session-id",room],capture_output=True,text=True,check=True,timeout=15)
    result=json.loads(output.stdout)
    assert result["automatic_publication"] is False
    assert result["off_device_backup"] is False
    verify(result["bundle_directory"])
    recovered=subprocess.run([sys.executable,str(cli),"recover-transcripts","--data-dir",str(store.root)],
                             capture_output=True,text=True,check=True,timeout=15)
    assert json.loads(recovered.stdout)=={"recovered":[]}
