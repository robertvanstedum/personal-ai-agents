"""Recent-session readers use numeric suffixes without changing stored records."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import german_domain as domain
from domains.german import reviewer, status


def write_session(root, stem, **fields):
    root.mkdir(exist_ok=True)
    value = {'session_id': stem, 'date': stem[:10], 'persona': stem,
             'scenario': 'human_session', 'user_id': 1, 'reviewer_output': {'next_focus': stem}}
    value.update(fields)
    path = root / f'{stem}.json'
    path.write_text(json.dumps(value))
    return path


@pytest.fixture
def session_root(tmp_path, monkeypatch):
    root = tmp_path / 'sessions'
    write_session(root, '2026-10-28_999')
    write_session(root, '2026-10-28_1000')
    monkeypatch.setattr(domain, 'GERMAN_STATE_DIR', tmp_path)
    monkeypatch.setattr(domain, '_SESSIONS_DIR', root)
    return root


def test_persona_and_human_advice_follow_numeric_latest(session_root):
    before = {p.name: p.read_bytes() for p in session_root.iterdir()}
    assert domain._last_session_persona() == '2026-10-28_1000'
    assert domain.get_last_human_session_suggestion() == '2026-10-28_1000'
    assert {p.name: p.read_bytes() for p in session_root.iterdir()} == before


def test_session_listing_orders_numerically_and_keeps_owner_filter(session_root):
    write_session(session_root, '2026-10-29_001', user_id=2)
    assert [x['stem'] for x in domain.get_gesprache_sessions(user_id=1)] == ['2026-10-28_1000', '2026-10-28_999']
    assert [x['stem'] for x in domain.get_gesprache_sessions(limit=1, user_id=1)] == ['2026-10-28_1000']
    assert domain.get_gesprache_sessions(user_id=None) == []
    write_session(session_root, '2026-10-29_002')
    assert domain.get_gesprache_sessions(limit=1, user_id=1)[0]['stem'] == '2026-10-29_002'


def test_status_picks_numeric_latest_reviewed(session_root):
    assert status._latest_reviewed_session(session_root).stem == '2026-10-28_1000'
    write_session(session_root, '2026-10-29_001', reviewer_output=None)
    assert status._latest_reviewed_session(session_root).stem == '2026-10-28_1000'


def test_reviewer_picks_numeric_latest_unreviewed(session_root):
    for stem in ('2026-10-28_999', '2026-10-28_1000'):
        write_session(session_root, stem, reviewer_output=None)
    assert reviewer._find_latest_unreviewed(session_root).stem == '2026-10-28_1000'
    write_session(session_root, '2026-10-29_001')
    assert reviewer._find_latest_unreviewed(session_root).stem == '2026-10-28_1000'


def test_scaffold_streak_uses_immediately_preceding_session(session_root, tmp_path):
    phrase = 'synthetic unused phrase'
    write_session(session_root, '2026-10-28_999', persona='Test', mode='voice', scaffold_avoided=[phrase])
    write_session(session_root, '2026-10-28_1000', persona='Test', mode='voice', scaffold_avoided=[])
    current = write_session(session_root, '2026-10-28_1001', persona='Test', mode='voice', raw_transcript=[])
    pool = tmp_path / 'drill_pool.json'
    pool.write_text('{"session_fed": {"pending": []}}')
    before = pool.read_bytes()
    reviewer._scaffold_analysis(json.loads(current.read_text()), current, session_root,
                                {'last_scaffold_delivered': [phrase]}, {'drill': {'promotion_threshold': 2}}, pool)
    assert pool.read_bytes() == before


def test_telegram_debug_uses_latest_session_with_fake_transport(session_root, monkeypatch):
    from core.telegram import telegram_bot as bot
    monkeypatch.setattr(bot, 'GERMAN_STATE_DIR', session_root.parent)
    monkeypatch.setattr(bot, '_german_agent_mode', lambda: 'legacy')
    reply = AsyncMock()
    asyncio.run(bot._handle_german_command(SimpleNamespace(message=SimpleNamespace(reply_text=reply)), '!german debug'))
    text = reply.call_args.args[0]
    assert 'Last session: 2026-10-28_1000.json' in text


def test_numeric_key_preserves_dates_and_deterministic_legacy_names():
    from pathlib import Path
    from domains.german.session_files import session_sort_key
    names = ['2026-10-29_001.json', '2026-10-28_1000.json', '2026-10-28_999.json',
             '2026-10-28_notes.json', 'legacy-b.json', 'legacy-a.json']
    result = sorted(map(Path, names), key=session_sort_key)
    assert [p.name for p in result] == ['2026-10-28_999.json', '2026-10-28_1000.json',
                                      '2026-10-28_notes.json', '2026-10-29_001.json', 'legacy-a.json', 'legacy-b.json']
