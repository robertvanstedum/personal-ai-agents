"""
tests/test_german_state_dir_resolution.py — German dev-drift fix, part 2
(2026-08-02): the web app (html_server.py/german_domain.py) already routed
state through GERMAN_STATE_DIR, but six standalone tools -- transcript
parser, reviewer, session generator, status utility, Anki importer,
transcript watcher -- plus the Telegram bot each computed their own
checkout-relative (and in three cases, already-dead: "language/german/"
does not exist in this repo layout) data paths. Running any of them
against a release checkout would write real history into a location the
web app never reads, silently splitting it again.

These tests prove every one of those readers/writers now resolves through
the same GERMAN_STATE_DIR (state) / GERMAN_DIR (application config, ships
with code) split as the web app, and that state writes never land inside
the release checkout.
"""
import importlib
import sys

import pytest


@pytest.fixture
def isolated_state_dir(tmp_path, monkeypatch):
    """A GERMAN_STATE_DIR distinct from GERMAN_DIR, so any accidental
    fallback to the checkout is immediately visible as a wrong-path
    assertion failure rather than silently passing."""
    state_dir = tmp_path / "external-state"
    state_dir.mkdir()
    monkeypatch.setenv("GERMAN_STATE_DIR", str(state_dir))
    for name in list(sys.modules):
        if name == "german_domain" or name.split(".")[-1] in {
            "status", "reviewer", "parse_transcript", "get_german_session",
            "import_cards", "watch_transcripts",
        } or name == "core.telegram.telegram_bot":
            del sys.modules[name]
    import german_domain
    importlib.reload(german_domain)
    return state_dir


def test_german_domain_state_dir_follows_env_var(isolated_state_dir):
    import german_domain
    assert german_domain.GERMAN_STATE_DIR == isolated_state_dir
    assert german_domain.GERMAN_STATE_DIR != german_domain.GERMAN_DIR


def test_german_domain_state_dir_defaults_to_german_dir_when_unset(monkeypatch):
    monkeypatch.delenv("GERMAN_STATE_DIR", raising=False)
    import german_domain
    importlib.reload(german_domain)
    assert german_domain.GERMAN_STATE_DIR == german_domain.GERMAN_DIR


def test_status_defaults_to_state_dir_not_checkout(isolated_state_dir):
    import status
    importlib.reload(status)
    assert status.GERMAN_STATE_DIR == isolated_state_dir


def test_reviewer_state_and_config_paths_split_correctly(isolated_state_dir):
    import reviewer
    importlib.reload(reviewer)
    assert reviewer.GERMAN_STATE_DIR == isolated_state_dir
    # Config always follows the code, never the isolated state override.
    assert reviewer.GERMAN_DIR != isolated_state_dir
    assert str(reviewer.GERMAN_DIR).endswith("domains/german/data")


def test_parse_transcript_domain_defaults_read_config_not_state(
    isolated_state_dir, tmp_path
):
    import parse_transcript
    importlib.reload(parse_transcript)
    assert parse_transcript.GERMAN_STATE_DIR == isolated_state_dir

    # domain.json lives in the release checkout's config, not the
    # isolated state dir -- _load_domain_defaults must find it there
    # regardless of what sessions_dir (state) is passed in.
    fake_sessions_dir = isolated_state_dir / "sessions"
    fake_sessions_dir.mkdir()
    defaults = parse_transcript._load_domain_defaults(fake_sessions_dir)
    assert isinstance(defaults, dict)  # doesn't raise; config resolves via GERMAN_DIR


def test_get_german_session_config_dir_is_checkout_not_state(isolated_state_dir):
    import get_german_session
    importlib.reload(get_german_session)
    assert get_german_session.GERMAN_STATE_DIR == isolated_state_dir
    assert get_german_session.GERMAN_DIR != isolated_state_dir


def test_import_cards_anki_dir_and_tracker_follow_state_dir(isolated_state_dir):
    import import_cards
    importlib.reload(import_cards)
    assert import_cards.ANKI_DIR == isolated_state_dir / "anki"
    assert import_cards.TRACKER == isolated_state_dir / "imported_files.txt"
    assert str(import_cards.ANKI_DIR.resolve()).startswith(str(isolated_state_dir.resolve()))


def test_watch_transcripts_config_and_state_paths_split_correctly(isolated_state_dir):
    import watch_transcripts
    importlib.reload(watch_transcripts)
    assert watch_transcripts.GERMAN_STATE_DIR == isolated_state_dir
    assert watch_transcripts.GERMAN_DIR != isolated_state_dir
    # sync_config.json (app config) must resolve via GERMAN_DIR even
    # though this whole module is otherwise driven by GERMAN_STATE_DIR.
    assert str(watch_transcripts.GERMAN_DIR).endswith("domains/german/data")


def test_telegram_bot_state_paths_follow_state_dir_not_checkout(isolated_state_dir):
    from core.telegram import telegram_bot
    importlib.reload(telegram_bot)
    assert telegram_bot.GERMAN_STATE_DIR == isolated_state_dir
    assert telegram_bot.GERMAN_DIR != isolated_state_dir


def test_parse_transcript_write_lands_only_in_state_dir_never_checkout(
    isolated_state_dir,
):
    """End-to-end proof, not just a path-constant check: actually run the
    write path and confirm nothing appears under the release checkout's
    real domains/german/data, however that path is currently spelled."""
    import parse_transcript
    importlib.reload(parse_transcript)
    from german_domain import GERMAN_DIR

    checkout_sessions_before = set((GERMAN_DIR / "sessions").glob("*.json"))

    sessions_dir = isolated_state_dir / "sessions"
    sessions_dir.mkdir(exist_ok=True)
    raw = (
        "##2026-08-02|TestPersona|test_scenario|5|writing\n"
        "Ich: Hallo, wie geht es dir?\n"
        "TestPersona: Mir geht es gut, danke!\n"
    )
    out_path = parse_transcript.parse_transcript(raw, sessions_dir)

    assert out_path.parent == sessions_dir
    assert out_path.exists()
    checkout_sessions_after = set((GERMAN_DIR / "sessions").glob("*.json"))
    assert checkout_sessions_after == checkout_sessions_before, (
        "a state write leaked into the release checkout's sessions/ dir"
    )
