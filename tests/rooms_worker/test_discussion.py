"""Discussion has no execution path, even for an action-shaped question."""
from types import SimpleNamespace

import pytest

from services.rooms_worker.discussion import Discussion
from services.rooms_worker import adapter
from services.rooms_worker.worker import Worker


@pytest.fixture
def setup(tmp_path):
    secret = tmp_path / 'token'
    secret.write_text('synthetic-discussion-token')
    calls = []
    reply = {'choices': [{'finish_reason': 'stop', 'message': {'content': 'Check the evidence first.'}}]}
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: reply)
    session = SimpleNamespace(post=post)
    transport = Discussion('mc', secret, session=session)
    turn = {'addressee': 'mc', 'trigger': {'text': 'Create an issue now', 'id': 'owner-event'},
            'discussion_reply': {'actor': 'cos', 'question_event': 'peer-event',
                                 'body': 'MC, what would you check before calling production healthy?'}}
    return transport, turn, calls, reply


def test_discussion_omits_original_action_and_executor_options(setup):
    transport, turn, calls, _ = setup
    result = transport.stream([{'content': 'EXECUTE THE ORIGINAL ACTION'}], 'user', 'correlation',
                              turn=turn, admit=lambda: (True, None))
    assert result['outcome'] == 'done'
    assert len(calls) == 1
    url, request = calls[0]
    assert url == 'http://model-gateway:4000/v1/chat/completions'
    body = request['json']
    assert set(body) == {'model', 'stream', 'max_tokens', 'user', 'messages'}
    assert 'Create an issue now' not in str(body)
    assert 'EXECUTE THE ORIGINAL ACTION' not in str(body)
    assert 'no tools' in body['messages'][0]['content']
    assert body['max_tokens'] == 2000
    assert request['allow_redirects'] is False
    assert transport.stop_guaranteed is False
    payload = adapter.reply_payload(turn, result['text'], 'correlation', None)
    assert payload['reference'] == 'peer-event'


def test_revoked_admission_makes_no_provider_request(setup):
    transport, turn, calls, _ = setup
    result = transport.stream([], 'u', 'c', turn=turn, admit=lambda: (False, 'newer_message'))
    assert result['outcome'] == 'not_admitted'
    assert not calls


def test_tool_output_is_never_executed_or_posted(setup):
    transport, turn, calls, reply = setup
    reply['choices'][0]['message']['tool_calls'] = [{'function': {'name': 'create_issue'}}]
    result = transport.stream([], 'u', 'c', turn=turn, admit=lambda: (True, None))
    assert result['outcome'] == 'refused'
    assert result['reason'] == 'discussion_unexpected_tools'
    assert result['text'] == ''
    assert len(calls) == 1


def test_missing_key_fails_closed(setup):
    transport, turn, calls, _ = setup
    transport.token_file.unlink()
    assert transport.stream([], 'u', 'c', turn=turn, admit=lambda: (True, None))['outcome'] == 'refused'
    assert not calls


def test_worker_never_falls_back_to_normal_agent_for_discussion():
    class Forbidden:
        def stream(self, *args, **kwargs):
            pytest.fail('A peer question reached the tool-enabled agent')
    worker = Worker(None, None, Forbidden(), None)
    failures = []
    worker.fail = lambda *args: failures.append(args)
    assert worker.infer({'discussion_reply': {'body': 'Do something'}, 'claim_id': 'claim'}) is None
    assert failures[0][-1] == 'discussion_not_configured'


@pytest.mark.parametrize("finish,content,reason", [
    ("length", "Partial answer", "discussion_reply_limit"),
    ("content_filter", None, "discussion_incomplete_reply"),
    ("unexpected-provider-value", "Do not expose this", "discussion_incomplete_reply"),
    ("stop", None, "discussion_not_plain_text"),
])
def test_unusable_completed_reply_has_fixed_reason_without_partial_text(setup, finish, content, reason):
    transport, turn, calls, reply = setup
    reply['choices'][0].update(finish_reason=finish, message={'content': content})
    reply['usage'] = {'completion_tokens': 2000}
    result = transport.stream([], 'u', 'c', turn=turn, admit=lambda: (True, None))
    assert result == {'outcome': 'refused', 'detail': reason, 'reason': reason,
                      'text': '', 'usage': {'completion_tokens': 2000}}
    assert len(calls) == 1  # no hidden retry or tool-enabled fallback
