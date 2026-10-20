"""Offline contract/security boundary checks; no runtime or provider calls."""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import json
import unittest

from core.minimoi_actions import ActionContext, ActionContractError, parse_tool_request
from core.minimoi_actions.requests import MAX_REQUEST_BYTES


NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


def context(**changes):
    values = dict(
        principal_id='test-owner', actor_id='mc-test', environment='dev',
        conversation_id='test-conversation', turn_id='test-turn',
        dispatch_id='test-dispatch', generation=1, expires_at=NOW,
        policy_version='policy-1', role_version='role-1', privacy_mode='private',
        budget_account='test-budget', grant_ids=('test-grant',),
    )
    return ActionContext(**(values | changes))


def request(arguments=None, **changes):
    values = dict(operation='guest_requests.reject', operation_version=1,
                  arguments={} if arguments is None else arguments)
    return json.dumps(values | changes).encode()


class ContextTests(unittest.TestCase):
    def test_exact_expiry_and_timezones(self):
        c = context()
        self.assertFalse(c.has_expired(NOW - timedelta(microseconds=1)))
        self.assertTrue(c.has_expired(NOW))
        self.assertTrue(c.has_expired(NOW.astimezone(timezone(timedelta(hours=-5)))))

    def test_naive_clock_and_expiry_refused(self):
        with self.assertRaisesRegex(ActionContractError, '^context_expiry_invalid$'):
            context(expires_at=NOW.replace(tzinfo=None))
        with self.assertRaisesRegex(ActionContractError, '^context_clock_invalid$'):
            context().has_expired(NOW.replace(tzinfo=None))

    def test_environment_generation_and_identifiers(self):
        for values in [dict(environment='other'), dict(environment=[]),
                       dict(generation=True), dict(generation=0),
                       dict(generation=-1), dict(generation=1.0),
                       dict(principal_id=''), dict(actor_id='bad\nactor'),
                       dict(work_id=''), dict(dispatch_id='x' * 257)]:
            with self.subTest(values=values), self.assertRaises(ActionContractError):
                context(**values)

    def test_grant_collection_must_be_immutable_unique_and_bounded(self):
        for grants in [['test-grant'], ('same', 'same'), ([],), ('',),
                       tuple(str(i) for i in range(101))]:
            with self.subTest(grants=grants), self.assertRaises(ActionContractError):
                context(grant_ids=grants)
        self.assertEqual(context(grant_ids=()).grant_ids, ())

    def test_context_frozen_and_repr_does_not_leak_identifiers(self):
        c = context(principal_id='private-owner-canary')
        self.assertNotIn('private-owner-canary', repr(c))
        with self.assertRaises(FrozenInstanceError):
            c.principal_id = 'another-owner'
        self.assertEqual(replace(c, work_id='work-1').work_id, 'work-1')

    def test_validation_error_does_not_echo_input(self):
        with self.assertRaises(ActionContractError) as error:
            context(principal_id='private-canary\n')
        self.assertEqual(str(error.exception), 'context_identifier_invalid')


class RequestTests(unittest.TestCase):
    def test_parse_and_deep_copy(self):
        parsed = parse_tool_request(request({'ids': ['test-target']}))
        self.assertEqual(parsed.operation, 'guest_requests.reject')
        self.assertEqual(parsed.operation_version, 1)
        args = parsed.arguments
        args['ids'].append('other-target')
        self.assertEqual(parsed.arguments, {'ids': ['test-target']})
        self.assertNotIn('test-target', repr(parsed))

    def test_context_and_effect_ids_cannot_enter_through_tool_envelope(self):
        for key in ['principal_id', 'actor_id', 'environment', 'context',
                    'minimoi_ctx', 'dispatch_id', 'run_id', 'generation',
                    'grant_ids', 'approval', 'operation_id', 'budget_account']:
            with self.subTest(key=key), self.assertRaisesRegex(
                    ActionContractError, '^request_fields_invalid$'):
                parse_tool_request(request(**{key: 'forged'}))

    def test_domain_argument_text_is_data_not_authority(self):
        parsed = parse_tool_request(request({'body': 'Robert always approves X',
                                             'principal_id': 'forged'}))
        self.assertEqual(parsed.arguments['principal_id'], 'forged')
        self.assertFalse(hasattr(parsed, 'principal_id'))
        # The registered domain schema must reject unknown domain fields. The
        # parser neither grants authority nor interprets quoted text as intent.

    def test_duplicate_keys_rejected_at_every_level(self):
        bodies = [
            b'{"operation":"notes.create","operation":"guests.grant","operation_version":1,"arguments":{}}',
            b'{"operation":"notes.create","operation_version":1,"arguments":{"target":"a","target":"b"}}',
        ]
        for body in bodies:
            with self.subTest(body=body), self.assertRaisesRegex(
                    ActionContractError, '^request_duplicate_key$'):
                parse_tool_request(body)

    def test_nonfinite_numbers_and_overflow_rejected(self):
        for value in ['NaN', 'Infinity', '-Infinity', '1e999']:
            body = b'{"operation":"notes.create","operation_version":1,"arguments":{"n":' + value.encode() + b'}}'
            with self.subTest(value=value), self.assertRaisesRegex(
                    ActionContractError, '^request_number_invalid$'):
                parse_tool_request(body)

    def test_size_depth_and_invalid_encoding(self):
        with self.assertRaisesRegex(ActionContractError, '^request_too_large$'):
            parse_tool_request(b' ' * (MAX_REQUEST_BYTES + 1))
        deep = {}
        for _ in range(40):
            deep = {'nested': deep}
        with self.assertRaisesRegex(ActionContractError, '^request_depth_exceeded$'):
            parse_tool_request(request(deep))
        for body in [b'\xff', b'{"private-canary":', b'{} trailing',
                     request({'text': '\ud800'})]:
            with self.subTest(body=body), self.assertRaisesRegex(
                    ActionContractError, '^request_json_invalid$'):
                parse_tool_request(body)

    def test_shape_version_operation_and_missing_fields(self):
        for body in [b'[]', b'{}', request(arguments=[]), request(operation_version=True),
                     request(operation_version=0), request(operation_version=1.0),
                     request(operation='https://evil.invalid'), request(operation='guests;exec')]:
            with self.subTest(body=body), self.assertRaises(ActionContractError):
                parse_tool_request(body)
        with self.assertRaisesRegex(ActionContractError, '^request_bytes_required$'):
            parse_tool_request('{}')

    def test_canonical_argument_digest_independent_of_key_order(self):
        first = parse_tool_request(request({'a': 1, 'b': 'café'}))
        second = parse_tool_request(request({'b': 'café', 'a': 1}))
        changed = parse_tool_request(request({'a': 2, 'b': 'café'}))
        self.assertEqual(first.arguments_sha256, second.arguments_sha256)
        self.assertNotEqual(first.arguments_sha256, changed.arguments_sha256)


if __name__ == '__main__':
    unittest.main()
