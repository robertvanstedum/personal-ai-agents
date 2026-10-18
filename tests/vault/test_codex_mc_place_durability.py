import hashlib
import pytest
from core.memory_sync import mc_pull as P
from core.memory_backup.backup import BackupError

def test_retry_after_rename_retries_directory_durability(tmp_path, monkeypatch):
    src=tmp_path/'source';src.write_bytes(b'owner conversation')
    watch=tmp_path/'watch';watch.mkdir()
    sha=hashlib.sha256(src.read_bytes()).hexdigest()
    calls=[]
    def fail_sync(path):
        calls.append(path)
        raise OSError('synthetic directory sync failure')
    monkeypatch.setattr(P,'_fsync_dir',fail_sync)
    with pytest.raises(BackupError):
        P.place(src,watch,'production--c-0123456789ab.json',sha,src.stat().st_size)
    assert len(calls)==1
    with pytest.raises(BackupError):
        P.place(src,watch,'production--c-0123456789ab.json',sha,src.stat().st_size)
    assert len(calls)==2
