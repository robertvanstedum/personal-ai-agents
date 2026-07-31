import keyring
import pytest

from scripts.tools.tour_capture.auth import CaptureAuthError, credentials_for


def test_owner_profile_defaults_to_robert_and_reads_local_password(monkeypatch):
    monkeypatch.delenv("MINIMOI_CAPTURE_OWNER_USERNAME", raising=False)
    monkeypatch.setenv("MINIMOI_CAPTURE_OWNER_PASSWORD", "test-password")
    monkeypatch.setattr(keyring, "get_password", lambda *_args: None)

    assert credentials_for("owner_session") == ("robert", "test-password")


def test_unknown_auth_profile_is_rejected():
    with pytest.raises(CaptureAuthError, match="unknown capture auth profile"):
        credentials_for("guest_session")
