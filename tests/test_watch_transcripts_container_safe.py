"""
tests/test_watch_transcripts_container_safe.py — issue #53.

watch_transcripts.py previously assumed a real Dropbox mount always exists
(blindly mkdir(parents=True)-ing into it) and read the Telegram token only
via a raw macOS keyring call. Neither assumption holds in a container/EC2
node with no Dropbox client. These tests cover the container-safe fix:
the watcher no-ops cleanly when Dropbox isn't present, and the Telegram
send path degrades gracefully instead of crashing on a missing keyring.
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "domains" / "german"))
import watch_transcripts


def test_dropbox_available_true_when_parent_exists(tmp_path, monkeypatch):
    dropbox_root = tmp_path / "Dropbox"
    dropbox_root.mkdir()
    monkeypatch.setattr(watch_transcripts, "BASE_PATH", dropbox_root / "German_Sessions")
    assert watch_transcripts._dropbox_available() is True


def test_dropbox_available_false_when_parent_missing(tmp_path, monkeypatch):
    missing_root = tmp_path / "Dropbox"  # never created
    monkeypatch.setattr(watch_transcripts, "BASE_PATH", missing_root / "German_Sessions")
    assert watch_transcripts._dropbox_available() is False


def test_main_no_ops_without_creating_directories_when_dropbox_missing(tmp_path, monkeypatch, capsys):
    missing_root = tmp_path / "Dropbox"
    base_path = missing_root / "German_Sessions"
    monkeypatch.setattr(watch_transcripts, "BASE_PATH", base_path)
    monkeypatch.setattr(watch_transcripts, "INBOX", base_path / "transcripts" / "inbox")
    monkeypatch.setattr(watch_transcripts, "PROCESSED", base_path / "transcripts" / "processed")
    monkeypatch.setattr(watch_transcripts, "PROMPTS", base_path / "prompts")
    monkeypatch.setattr(watch_transcripts, "LOGS", base_path / "logs")

    watch_transcripts.main()

    assert not missing_root.exists()  # nothing created at all, not even the parent
    err = capsys.readouterr().err
    assert "not Dropbox-capable" in err


def test_tg_send_prefers_system_token_over_keyring():
    with patch("utils.telegram.get_system_token", return_value="sys-token") as mock_token, \
         patch("utils.telegram.get_chat_id", return_value="12345") as mock_chat, \
         patch("requests.post") as mock_post:
        watch_transcripts._tg_send("hello")
    mock_token.assert_called_once()
    mock_chat.assert_called_once()
    mock_post.assert_called_once()
    assert mock_post.call_args.args[0] == "https://api.telegram.org/botsys-token/sendMessage"


def test_tg_send_survives_missing_keyring_module():
    """If utils.telegram fails to resolve credentials AND keyring isn't
    installed (container case), _tg_send must log and return, not crash —
    the old code's unconditional `import keyring` at the top of the
    function would have raised ModuleNotFoundError here."""
    with patch("utils.telegram.get_system_token", side_effect=Exception("no SSM/keychain")), \
         patch.dict(sys.modules, {"keyring": None}):
        watch_transcripts._tg_send("hello")  # must not raise
