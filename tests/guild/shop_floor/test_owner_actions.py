"""Owner notes and reminders (owner_actions.py): parsed from the owner's own words only, saved only
through the Chief of Staff platform's receipt, kept as a conversation receipt, never claimed from prose.
No network, no model call; the CoS route is a fake that answers like the real Confer operations."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from types import SimpleNamespace

import pytest
import requests

from minimoi_portal.guild_ui import owner_actions as oa
from minimoi_portal.guild_ui.mc.cos import ChiefOfStaffBackend
from minimoi_portal.guild_ui.mc import CachedHealth

from floor_helpers import write_headers
from floor_helpers import load_portal  # noqa: F401  (pytest fixture)
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401  (pytest fixtures)

# Sun 18 Oct 2026, 22:30 in Chicago (CDT, UTC-5).
NOW = datetime(2026, 10, 19, 3, 30, tzinfo=timezone.utc)
API = "/guild-next/api/v1"


def z(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


# ── the grammar ─────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('text,payload', [
    ('/note buy milk', 'buy milk'),
    ('Save a note: the gate code is 4411', 'the gate code is 4411'),
    ('Make a note that the gate code is 4411', 'the gate code is 4411'),
    ('take a note: call Anna back', 'call Anna back'),
    ('Note to self: renew passport', 'renew passport'),
])
def test_note_commands_use_the_cos_grammar(text, payload):
    action = oa.parse(text, NOW)
    assert action == oa.Action('note', text=payload)


@pytest.mark.parametrize('text,due,words', [
    ('Remind me at 9am to call the plumber', '2026-10-19T14:00Z', 'call the plumber'),
    ('Hey, remind me at 9am to call the plumber', '2026-10-19T14:00Z', 'call the plumber'),
    ('remind me to call the plumber at 9', '2026-10-19T14:00Z', 'call the plumber'),        # 9 and 21 passed: next is 9 am
    ('remind me to take the bins out tonight at 11', '2026-10-19T04:00Z', 'take the bins out'),
    ('Remind me in 20 minutes to check the oven', '2026-10-19T03:50Z', 'check the oven'),
    ('remind me in half an hour to stretch.', '2026-10-19T04:00Z', 'stretch'),
    ('Remind me tomorrow at 2:30 pm to email Anna', '2026-10-19T19:30Z', 'email Anna'),
    ('set a reminder for tomorrow morning to water the plants', '2026-10-19T14:00Z', 'water the plants'),
    ('remind me on Friday at 10 to renew the passport', '2026-10-23T15:00Z', 'renew the passport'),
    ('Can you remind me at noon tomorrow to book tickets?', '2026-10-19T17:00Z', 'book tickets'),
    ('remind me to look at the roof tomorrow at 10', '2026-10-19T15:00Z', 'look at the roof'),
    ('remind me that the meeting is at 3 pm', '2026-10-19T20:00Z', 'the meeting is at 3 pm'),
    ('remind me about the dentist tomorrow at 8 am', '2026-10-19T13:00Z', 'the dentist tomorrow at 8 am'),
    ('/remind 2026-10-19T14:00Z call the plumber', '2026-10-19T14:00Z', 'call the plumber'),
])
def test_reminder_times_resolve_in_chicago(text, due, words):
    action = oa.parse(text, NOW)
    assert action.kind == 'reminder' and action.refusal is None
    assert action.due == z(due) and action.text == words


def test_reminder_after_the_dst_change_uses_standard_time():
    friday = datetime(2026, 10, 30, 15, 0, tzinfo=timezone.utc)
    assert oa.parse('remind me on Monday at 9 am to file taxes', friday).due == z('2026-11-02T15:00Z')   # CST


@pytest.mark.parametrize('text,words', [
    ('remind me to call mom', 'Say the whole reminder with its time'),
    ('remind me tomorrow to call the bank', 'Say what time'),
    ('remind me at 25:00 to stretch', 'not one I can read'),
    ('remind me in 0 minutes to stretch', 'between one minute and seven days'),
    ('/remind tomorrow call the plumber', 'Use /remind'),
    ('/remind 2026-13-45T99:99Z impossible date', 'Use /remind'),
    ('/remind 2026-10-18T10:00Z already gone', 'in the future'),
    ('remind me to ' + 'x' * 501 + ' at 5 pm', 'Keep a reminder to 500'),
])
def test_recognized_reminders_that_cannot_be_set_refuse_and_schedule_nothing(text, words):
    action = oa.parse(text, NOW)
    assert action.kind == 'reminder' and action.due is None and words in action.refusal
    assert 'Nothing was scheduled.' in action.refusal


def test_today_at_a_passed_time_is_refused():
    assert 'already passed' in oa.parse('remind me today at 9 am to stretch', NOW).refusal


@pytest.mark.parametrize('text', [
    'what reminders do I have?', "don't remind me about that", 'He said remind me at 5 to call', 'I need a reminder system',
    'remind me what we decided yesterday', 'remind me about the plan', 'remind me that we agreed on Tuesday',
    'noted', 'Note: the build is green', 'make a post-it: buy milk', 'How is the build going?', '',
])
def test_ordinary_chat_is_not_an_action(text):
    assert oa.parse(text, NOW) is None


def test_overlong_note_is_refused_not_passed_on():
    action = oa.parse('/note ' + 'x' * 1500, NOW)
    assert action.kind == 'note' and 'Nothing was saved.' in action.refusal


# ── the operation and its receipt ───────────────────────────────────────────────────────────────

class FakeCos:
    """Answers like the Chief of Staff's Confer operations: note_save and reminder_set, deduplicated by request id."""
    kind = 'cos'

    def __init__(self, answer=None):
        self.calls = []
        self.seen = {}
        self.answer = answer

    def operation(self, text, *, conversation_id, request_id):
        self.calls.append((text, conversation_id, request_id))
        if self.answer is not None:
            return self.answer(text, request_id)
        status = 'deduplicated' if request_id in self.seen else 'saved'
        self.seen[request_id] = text
        if text.startswith('/note '):
            return {'state': 'ok', 'reply': 'Note saved in COS platform memory.',
                    'operation': {'type': 'note_save', 'status': status, 'operation_id': request_id, 'storage': 'cos_platform_memory'}}
        due, words = re.fullmatch(r'/remind (\S+) (.+)', text).groups()
        return {'state': 'ok', 'reply': 'Reminder saved.', 'operation': {
            'type': 'reminder_set', 'status': status, 'operation_id': request_id, 'storage': 'cos_reminders',
            'delivery': 'telegram', 'due_utc': due, 'reminder_id': 'rem-' + hashlib.sha256(request_id.encode()).hexdigest()[:12]}}


class Floor:
    floor = 'owner-floor'

    def __init__(self):
        self.rows = {}

    def add_note(self, key, text, author, **kw):
        old = self.rows.get(key)
        if old is not None:
            return SimpleNamespace(outcome='kept' if old['text'] == text else 'idempotency_mismatch', value=old, repeated=True)
        self.rows[key] = {'request_id': key, 'text': text, 'who': author.id, **kw}
        return SimpleNamespace(outcome='kept', value=self.rows[key], repeated=False)


def run(text, cos=None, floor=None, key='kept-note-1'):
    cos = cos or FakeCos()
    floor = floor or Floor()
    return oa.execute(text, cos=cos, conversation=floor, principal='robert', turn_key=key, now=NOW), cos, floor


def test_reminder_sends_one_canonical_command_and_keeps_a_named_receipt():
    out, cos, floor = run('Remind me at 9am to call the plumber')
    assert cos.calls == [('/remind 2026-10-19T14:00:00Z call the plumber', 'owner-floor', cos.calls[0][2])]
    op = out['operation']
    assert op['saved'] and op['type'] == 'reminder_set' and op['due_utc'] == '2026-10-19T14:00:00Z' and op['delivery'] == 'telegram'
    assert out['reply'] == ('Reminder set for Mon 19 Oct, 9:00 AM Chicago time: call the plumber. '
                            f"It will arrive by Telegram. Receipt {op['reminder_id']}.")
    assert out['history_saved'] and out['reply_note']['text'] == out['reply'] and out['reply_note']['who'] == 'guild_platform'


def test_retry_of_the_same_turn_is_one_operation_and_the_same_receipt():
    cos, floor = FakeCos(), Floor()
    first, _, _ = run('make a note that the gate code is 4411', cos, floor)
    again, _, _ = run('make a note that the gate code is 4411', cos, floor)
    assert cos.calls[0] == cos.calls[1] and cos.calls[0][0] == '/note the gate code is 4411'
    assert first['operation']['repeated'] is False and again['operation']['repeated'] is True
    assert first['reply'] == again['reply'] == 'Saved to Chief of Staff notes: the gate code is 4411'
    assert again['history_saved'] and len(floor.rows) == 1


@pytest.mark.parametrize('change', [
    {'operation_id': 'someone-else'}, {'due_utc': '2026-10-19T15:00:00Z'}, {'reminder_id': 'rem-NOTHEX'},
    {'status': 'failed'}, {'delivery': 'email'}, {'type': 'note_save'},
])
def test_a_receipt_that_does_not_match_the_request_is_not_success(change):
    def answer(text, request_id):
        good = FakeCos().operation(text, conversation_id='x', request_id=request_id)
        return {**good, 'operation': {**good['operation'], **change}}
    out, _, floor = run('Remind me at 9am to call the plumber', FakeCos(answer))
    assert out['operation']['saved'] is False and 'did not confirm' in out['reply'] and not floor.rows


@pytest.mark.parametrize('receipt,words', [
    ({'state': 'refused', 'reply': 'Reminder time must be in the future and within a year.'}, 'within a year. Nothing was scheduled.'),
    ({'state': 'uncertain'}, 'outcome is unknown'),
    ({'state': 'unavailable'}, 'Nothing is known to be scheduled'),
    ({'state': 'failed'}, 'Nothing is known to be scheduled'),
])
def test_failures_say_so_and_keep_no_receipt(receipt, words):
    out, _, floor = run('Remind me at 9am to call the plumber', FakeCos(lambda t, r: receipt))
    assert out['operation']['saved'] is False and words in out['reply'] and 'set for' not in out['reply'] and not floor.rows


def test_refusal_never_reaches_cos():
    out, cos, _ = run('remind me to call mom')
    assert not cos.calls and out['operation'] == {'type': 'reminder_set', 'saved': False}


def test_a_backend_that_is_not_cos_saves_nothing():
    out, _, floor = run('/note buy milk', SimpleNamespace(kind='openclaw'))
    assert out['reply'] == oa.UNAVAILABLE and not floor.rows


def test_card_numbers_are_redacted_before_they_leave():
    out, cos, _ = run('/note card 4242 4242 4242 4242 is the spare')
    assert '4242 4242' not in cos.calls[0][0] and '4242 4242' not in out['reply']


def test_ordinary_chat_returns_none_without_any_call():
    out, cos, floor = run('How is the build going?')
    assert out is None and not cos.calls and not floor.rows


# ── the CoS client keeps CoS's receipt ──────────────────────────────────────────────────────────

class Resp:
    def __init__(self, status=200, payload=None):
        self.status_code, self._payload = status, payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def backend(post):
    sent = []

    def fake_post(url, json=None, timeout=None, **kw):
        sent.append((url, json, timeout))
        if isinstance(post, Exception):
            raise post
        return post
    return ChiefOfStaffBackend('http://cos-scheduler:8769', http_post=fake_post, http_get=lambda *a, **k: Resp(200, {})), sent


def test_operation_posts_the_canonical_text_once_and_returns_the_receipt():
    op = {'type': 'note_save', 'status': 'saved', 'operation_id': 'u', 'storage': 'cos_platform_memory'}
    cos, sent = backend(Resp(200, {'reply': 'Note saved in COS platform memory.', 'operation': op}))
    out = cos.operation('/note buy milk', conversation_id='floor-1', request_id='u')
    assert out == {'state': 'ok', 'reply': 'Note saved in COS platform memory.', 'operation': op}
    url, body, timeout = sent[0]
    assert len(sent) == 1 and url.endswith('/chat') and body['text'] == '/note buy milk' and body['request_id'] == 'u'
    assert body['channel'] == 'api_text' and body['conversation_id'].startswith('guild-') and timeout == 20


@pytest.mark.parametrize('post,state', [
    (Resp(400, {'reply': 'Reminder time must be in the future and within a year.'}), 'refused'),
    (Resp(500, {'reply': 'COS reminder was not saved.'}), 'failed'),
    (Resp(200, {'reply': 'a model answer without a receipt'}), 'failed'),
    (Resp(503, {}), 'unavailable'),
    (requests.Timeout(), 'uncertain'),
    (requests.ConnectionError(), 'unavailable'),
])
def test_operation_outcomes_are_never_guessed(post, state):
    cos, sent = backend(post)
    out = cos.operation('/remind 2026-10-19T14:00:00Z x', conversation_id='f', request_id='u')
    assert out['state'] == state and len(sent) == 1


# ── the typed route: before any model, on the real portal ───────────────────────────────────────

class CosRoute:
    """The CoS /chat route: Confer operations for /note and /remind, a model answer otherwise."""

    def __init__(self):
        self.sent = []
        self.seen = set()

    def post(self, url, json=None, timeout=None, **kw):
        self.sent.append(json)
        text, rid = json['text'], json['request_id']
        status = 'deduplicated' if rid in self.seen else 'saved'
        if text.startswith(('/note ', '/remind ')):
            self.seen.add(rid)
        if text.startswith('/note '):
            return Resp(200, {'reply': 'Note saved in COS platform memory.', 'operation': {
                'type': 'note_save', 'status': status, 'operation_id': rid, 'storage': 'cos_platform_memory'}})
        if text.startswith('/remind '):
            due = text.split()[1]
            return Resp(200, {'reply': 'Reminder saved.', 'operation': {
                'type': 'reminder_set', 'status': status, 'operation_id': rid, 'storage': 'cos_reminders', 'delivery': 'telegram',
                'due_utc': due, 'reminder_id': 'rem-' + hashlib.sha256(rid.encode()).hexdigest()[:12]}})
        return Resp(200, {'reply': 'A model answer.'})


@pytest.fixture
def cos_portal(floored):
    route = CosRoute()
    services = floored.app.extensions['guild_ui_next']['services']
    services.mc = ChiefOfStaffBackend('http://cos-scheduler:8769', http_post=route.post, http_get=lambda *a, **k: Resp(200, {}))
    services.mc_health = CachedHealth(services.mc)
    services.mc_turns = True
    floored.extra['route'] = route
    return floored


def _keep(client, token, text):
    r = client.post(f"{API}/notes", json=keyed(text=text), headers=write_headers(token))
    assert r.status_code == 200, r.get_json()
    return r.get_json()['note']


def _ask(client, token, note_id):
    return client.post(f"{API}/mc/turns", json={'note_request_id': note_id, 'record_mode': 'on_record'},
                       headers=write_headers(token, mode='on_record'))


def test_typed_note_goes_to_cos_as_the_owners_words_only_and_replay_is_safe(cos_portal):
    client = cos_portal.owner(); token = cos_portal.csrf(client)
    note = _keep(client, token, '/note the spare key is in the blue box')
    first = _ask(client, token, note['request_id']).get_json(); again = _ask(client, token, note['request_id']).get_json()
    sent = cos_portal.extra['route'].sent
    assert [s['text'] for s in sent] == ['/note the spare key is in the blue box'] * 2      # no context blocks appended
    assert sent[0]['request_id'] == sent[1]['request_id']
    assert first['operation']['saved'] and again['operation']['repeated']
    assert first['reply_note']['text'] == 'Saved to Chief of Staff notes: the spare key is in the blue box'
    assert first['backend_kind'] == 'platform' and first['history_saved']


def test_typed_reminder_is_set_with_its_exact_time_before_any_model(cos_portal):
    client = cos_portal.owner(); token = cos_portal.csrf(client)
    note = _keep(client, token, 'Remind me in 20 minutes to check the oven')
    body = _ask(client, token, note['request_id']).get_json()
    kept = datetime.fromisoformat(note['created_at'].replace('Z', '+00:00'))
    sent = cos_portal.extra['route'].sent
    assert len(sent) == 1 and sent[0]['text'].startswith('/remind ') and sent[0]['text'].endswith(' check the oven')
    due = datetime.fromisoformat(sent[0]['text'].split()[1].replace('Z', '+00:00'))
    assert due == (kept + timedelta(minutes=20)).replace(microsecond=0)     # from when the note was kept
    assert body['operation']['saved'] and body['operation']['reminder_id'].startswith('rem-')
    assert body['reply_note']['text'].startswith('Reminder set for ')


def test_reminder_without_a_time_asks_and_sends_nothing(cos_portal):
    client = cos_portal.owner(); token = cos_portal.csrf(client)
    note = _keep(client, token, 'remind me to call mom')
    body = _ask(client, token, note['request_id']).get_json()
    assert not cos_portal.extra['route'].sent and body['operation'] == {'type': 'reminder_set', 'saved': False}
    assert 'Say the whole reminder with its time' in body['message'] and body['reply_note'] is None


def test_ordinary_chat_still_reaches_the_model(cos_portal):
    client = cos_portal.owner(); token = cos_portal.csrf(client)
    note = _keep(client, token, 'remind me what we decided about the release')
    body = _ask(client, token, note['request_id']).get_json()
    sent = cos_portal.extra['route'].sent
    assert len(sent) == 1 and sent[0]['text'].startswith('remind me what we decided about the release')
    assert body.get('operation') is None


def test_relative_delay_preserves_elapsed_seconds():
    now = z('2026-10-19T12:00:59Z')
    action = oa.parse('remind me in one minute to stretch', now)
    assert action.due == now + timedelta(minutes=1)


def test_nonexistent_dst_clock_time_is_refused():
    action = oa.parse('remind me tomorrow at 2:30 am to stretch', z('2027-03-13T18:00:00Z'))
    assert action.refusal and 'does not exist' in action.refusal


# ── 19 Oct hotfix: late-night clocks, today's am/pm, recall with a part of the day, day-first + time ──

from zoneinfo import ZoneInfo as _Zone

_CHI = _Zone('America/Chicago')


def _mon(hour):
    return datetime(2026, 10, 19, hour, 0, tzinfo=_CHI).astimezone(timezone.utc)      # Mon 19 Oct


@pytest.mark.parametrize('text,hour,local,words', [
    ('remind me tonight at 12 to lock up', 10, '2026-10-20 00:00', 'lock up'),
    ('remind me tonight at 12 to lock up', 21, '2026-10-20 00:00', 'lock up'),
    ('remind me tonight at 12:30 to lock up', 21, '2026-10-20 00:30', 'lock up'),
    ('remind me tonight at 1 to check the door', 21, '2026-10-20 01:00', 'check the door'),
    ('remind me tonight at midnight to lock up', 21, '2026-10-20 00:00', 'lock up'),
    ('remind me tomorrow night at 12 to lock up', 10, '2026-10-21 00:00', 'lock up'),
    ('remind me tonight at 11 to take the bins out', 10, '2026-10-19 23:00', 'take the bins out'),
    ('remind me today at 7 to call', 10, '2026-10-19 19:00', 'call'),
    ('remind me today at 8:30 to call', 14, '2026-10-19 20:30', 'call'),
    ('remind me at 12 to lock up', 21, '2026-10-20 00:00', 'lock up'),
    ('remind me at 12:30 to eat', 10, '2026-10-19 12:30', 'eat'),
    ('remind me tomorrow to call Anna at 5', 10, '2026-10-20 17:00', 'call Anna'),
    ('remind me on Friday to submit the report at 10 am', 10, '2026-10-23 10:00', 'submit the report'),
    ('Set a reminder for tomorrow at 9 am to call Anna at noon', 10, '2026-10-20 09:00', 'call Anna at noon'),
])
def test_hotfix_times(text, hour, local, words):
    action = oa.parse(text, _mon(hour))
    assert action.refusal is None, action.refusal
    assert action.due.astimezone(_CHI).strftime('%Y-%m-%d %H:%M') == local and action.text == words


@pytest.mark.parametrize('text,hour', [
    ('Remind me about what we decided this morning', 15),
    ('Remind me about what we decided this morning', 7),
    ('Can you remind me about the plan for this afternoon?', 10),
    ("remind me about what's on tonight", 10),
])
def test_recall_with_only_a_part_of_the_day_stays_chat(text, hour):
    assert oa.parse(text, _mon(hour)) is None
