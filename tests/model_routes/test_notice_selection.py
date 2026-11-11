import json
from types import SimpleNamespace
import pytest

from core.notice_selection import MARKER, SelectionBuffer
from minimoi_portal.guild_ui.pending_notices import Plan
from minimoi_portal.guild_ui.conversations import ConversationStore
from minimoi_portal.guild_ui.mc.stream import Delta, Finish, Failure

NONCE = 'a' * 32
LINE = '- W-1: Review the design. Answer: reply in the work item.'


def packet(nonce, ids):
    return MARKER + '\n' + json.dumps({'nonce': nonce, 'ids': ids})


@pytest.mark.parametrize('split', range(len(MARKER) + 3))
def test_control_never_leaks_across_chunk_boundaries(split):
    raw = 'Answer first.\n\n' + packet(NONCE, ['W-1:abc'])
    parser = SelectionBuffer(NONCE)
    point = len('Answer first.\n\n') + split
    text = parser.feed(raw[:point]) + parser.feed(raw[point:])
    tail, ids = parser.finish(completed=True)
    assert text + tail == 'Answer first.\n\n'
    assert ids == ['W-1:abc']


@pytest.mark.parametrize('control', [
    '{"nonce":"wrong","ids":[]}', 'not json', '{"nonce":"' + NONCE + '","ids":[],"ids":[]}',
    '{"nonce":"' + NONCE + '","ids":[]}untrusted trailing prose', 'x' * 5000,
])
def test_invalid_control_is_discarded_without_a_selection(control):
    parser = SelectionBuffer(NONCE)
    assert parser.feed('Answer.\n' + MARKER + control) == 'Answer.\n'
    assert parser.finish(completed=True) == ('', [])


def test_no_marker_preserves_ordinary_markdown_and_length_never_selects():
    parser = SelectionBuffer(NONCE)
    body = parser.feed('Answer with a trailing [')
    tail, ids = parser.finish(completed=True)
    assert body + tail == 'Answer with a trailing [' and not ids
    parser = SelectionBuffer(NONCE)
    parser.feed('Answer.\n' + packet(NONCE, ['x']))
    assert parser.finish(completed=False) == ('', [])


@pytest.fixture
def rig(tmp_path):
    store = ConversationStore(str(tmp_path), base_floor='test')
    conv, _ = store.create('robert', key='pending-notices')
    notes = {}
    def plan(reference='reply-1', conversation=None):
        # Each turn reconstructs both adapter and metadata from disk.
        return Plan(ConversationStore(str(tmp_path), base_floor='test'), conversation or conv,
                    'robert', SimpleNamespace(get_note=notes.get, floor=(conversation or conv)['notes_floor']), reference)
    return store, conv, notes, plan


def offer(plan, *, version=1, explicit=False, current=lambda: True):
    return plan.offer('W-1', {'generation': version}, LINE, explicit=explicit, current=current)


def answer(plan):
    return 'The answer to your question.\n\n' + packet(plan.nonce, list(plan.offers))


def keep(notes, reference, text):
    notes[reference] = {'who': 'master_craftsman', 'author_kind': 'agent', 'text': text}


def test_offer_is_not_delivery_and_failed_save_retries_but_saved_answer_survives_reload(rig):
    _, _, notes, make = rig
    first = make()
    assert offer(first)
    second = make('reply-2')
    assert offer(second)  # prompt exposure alone acknowledged nothing
    rendered = second.transform(answer(second))
    assert rendered.index('question.') < rendered.index(LINE) and MARKER not in rendered
    failed = make('reply-3')
    assert offer(failed)  # prepared reference with no canonical answer
    keep(notes, 'reply-2', rendered)  # save reached disk, even if acknowledgement was lost
    assert not offer(make('reply-4'))
    assert offer(make('reply-5'), version=2)
    assert offer(make('reply-6'), explicit=True)


def test_fresh_conversation_unrelated_author_and_exact_text_checks(rig):
    store, _, notes, make = rig
    first = make()
    offer(first)
    rendered = first.transform(answer(first))
    keep(notes, 'reply-1', rendered)
    fresh, _ = store.create('robert', key='fresh-pending-notices')
    assert offer(make('reply-fresh', fresh))
    notes['reply-1']['who'] = 'robert'
    assert offer(make('reply-2'))
    keep(notes, 'reply-1', 'I referred to W-1, but did not show its notice.')
    assert offer(make('reply-3'))


def test_changed_source_unknown_id_empty_selection_and_failed_stream_never_receipt(rig):
    store, conv, _, make = rig
    first = make()
    offer(first, current=lambda: False)
    assert LINE not in first.transform(answer(first))
    assert not store.get(conv['id'], 'robert').get('pending_notice_candidates')
    second = make('reply-2')
    offer(second)
    assert LINE not in second.transform('Answer.\n' + packet(second.nonce, ['not-offered']))
    assert LINE not in second.transform('Answer.\n' + packet(second.nonce, []))
    assert not store.get(conv['id'], 'robert').get('pending_notice_candidates')
    events = list(second.events([Delta(answer(second)), Failure('upstream'), Finish('stop')]))
    assert not any(LINE in e.text or MARKER in e.text for e in events if isinstance(e, Delta))
    assert not store.get(conv['id'], 'robert').get('pending_notice_candidates')


def test_streamed_selection_preserves_host_route_notice_and_only_prepares_until_saved(rig):
    _, _, notes, make = rig
    first = make()
    offer(first)
    first.route_suffix = '[The plan failed; this turn used its API.]'
    raw = answer(first) + '\n\n' + first.route_suffix
    events = list(first.events([Delta(raw[:19]), Delta(raw[19:]), Finish('stop')]))
    rendered = ''.join(e.text for e in events if isinstance(e, Delta))
    assert rendered.count(LINE) == 1 and rendered.endswith(first.route_suffix) and MARKER not in rendered
    assert offer(make('reply-2'))
    keep(notes, 'reply-1', rendered)
    assert not offer(make('reply-3'))


def test_partial_control_header_is_not_visible():
    parser = SelectionBuffer(NONCE)
    assert parser.feed('Answer.\n[[MINIMOI_NOTICES_') == 'Answer.\n'
    assert parser.finish(completed=False) == ('', [])


def test_failed_explicit_repeats_never_evict_proven_receipt(rig):
    store, conv, notes, make = rig
    initial = make()
    offer(initial)
    keep(notes, 'reply-1', initial.transform(answer(initial)))
    for n in range(8):
        explicit = make(f'failed-{n}')
        offer(explicit, explicit=True)
        explicit.transform(answer(explicit))  # fail to save each repeated answer
    assert not offer(make('later'))
    rows = store.get(conv['id'], 'robert')['pending_notice_candidates']
    assert len(rows) == 2 and rows[0]['reference'] == 'reply-1'


def test_supervision_delivery_state_is_not_new_and_explicit_reference_is_available(rig, monkeypatch):
    from minimoi_portal.guild_ui import supervision
    _, _, notes, make = rig
    monkeypatch.setenv('DATABASE_URL', 'postgresql://unused')
    decisions = [{'id': 1, 'code': 'D-1', 'text': 'Review this design', 'state': 'new'}]
    source = SimpleNamespace(pending=lambda: decisions, latest_checkin=lambda: None)
    env = {'MINIMOI_SUPERVISION': '1', 'MINIMOI_SUPERVISION_OWNER': 'robert'}
    def context(plan, words='hello'):
        return supervision.context_block('robert', environ=env, store_factory=lambda: source,
                                         notice_plan=plan, owner_text=words)
    first = make()
    context(first)
    keep(notes, 'reply-1', first.transform(answer(first)))
    decisions[0]['state'] = 'sent'
    repeat = make('reply-2')
    context(repeat)
    assert not repeat.offers
    explicit = make('reply-3')
    context(explicit, 'What about d1?')
    assert explicit.offers
    decisions[0]['text'] = 'Review the revised design'
    assert '- D-1:' not in explicit.transform(answer(explicit))  # stale snapshot cannot be shown
    changed = make('reply-4')
    context(changed)
    assert changed.offers


def test_no_offers_leave_literal_reserved_marker_untouched_and_wrong_floor_refuses(rig):
    _, _, _, make = rig
    plan = make()
    literal = 'Explain this protocol: ' + MARKER
    assert plan.transform(literal) == literal
    assert list(plan.events([Delta(literal), Finish('stop')])) == [Delta(literal), Finish('stop')]
    plan.floor.floor = 'another-conversation'
    assert not offer(plan)
