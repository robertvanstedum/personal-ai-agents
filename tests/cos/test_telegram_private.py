"""Spec 160 T6, Telegram half: /private and /public write the same sticky mode
the web page and voice read, and replies given under Private say so."""
import pytest

pytest.importorskip("telegram")

from core.telegram import telegram_cos_bot as bot          # noqa: E402
from domains.cos import private_mode                        # noqa: E402


@pytest.fixture
def root(tmp_path, monkeypatch):
    folder = tmp_path / "cos-turns"
    folder.mkdir(mode=0o700)
    monkeypatch.setenv("COS_TURNS_DIR", str(folder))
    return folder


def test_private_turns_on_for_every_channel_and_discloses(root):
    reply = bot._set_private(True)
    assert private_mode.is_private(root)
    assert "may still remember it" in reply and "model provider" in reply and "/public" in reply


def test_replies_carry_the_private_prefix_only_while_private(root):
    assert bot._with_private_prefix("hello") == "hello"
    bot._set_private(True)
    assert bot._with_private_prefix("hello") == "Private · hello"


def test_only_public_turns_it_off(root):
    bot._set_private(True)
    bot._set_private(False)
    assert not private_mode.is_private(root) and bot._with_private_prefix("hi") == "hi"


def test_without_a_turn_log_there_is_nothing_to_make_private(monkeypatch):
    monkeypatch.delenv("COS_TURNS_DIR", raising=False)
    assert "not kept here" in bot._set_private(True)
