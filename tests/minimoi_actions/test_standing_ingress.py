"""Internal HTTP boundary checks; no listener, credentials or live delivery."""

from dataclasses import replace
import hashlib
import json
import unittest
from uuid import uuid4

from core.minimoi_actions.context import ActionContractError
from core.minimoi_actions.standing import Destination, ProducerPolicy, StandingEvent, ack, brief_text
from services.minimoi_actions.standing_ingress import create_app, PATH

TOKEN = 'synthetic-producer-token-for-local-tests-only'
TEXT = b'MC operations brief.\nAll checks healthy.'
POLICY = ProducerPolicy('worker', 'test-owner', 'dev', hashlib.sha256(TOKEN.encode()).hexdigest(),
                        ('a' * 64,), (Destination('mc-operations', 'guild/test-conversation'),))


def envelope(**changes):
    return dict(schema='mc-standing-event/1', event_id=str(uuid4()),
                cycle_key='dev.2026-10-20.p0.morning', policy_hash='a' * 64,
                environment='dev', kind='brief', occurred_at='2026-10-20T10:40:00Z',
                source_refs=[dict(id='source1', digest='b' * 64)], finding_key=None,
                payload_ref='cycle.brief', payload_digest=hashlib.sha256(TEXT).hexdigest(),
                destination_binding='mc-operations') | changes


class ProbeStore:
    def __init__(self):
        self.calls = []
    def accept(self, event, policy, resolver):
        self.calls.append((event, policy))
        brief_text(resolver(policy, event.data['payload_ref']), event.data['payload_digest'])
        return ack(event.data['event_id'], 'accepted', 'brief_kept', '1'), 200
    def get(self, event_id, policy):
        self.calls.append((event_id, policy))
        return None


class IngressTests(unittest.TestCase):
    def setUp(self):
        self.store = ProbeStore()
        self.resolutions = []
        def resolve(policy, object_id):
            self.resolutions.append((policy, object_id))
            return TEXT
        self.resolve = resolve
        self.client = create_app(environment='dev', policies=(POLICY,), store=self.store,
                                 load_payload=resolve, enabled=True).test_client()
        self.headers = {'Authorization': 'Bearer ' + TOKEN}

    def post(self, data, **kw):
        return self.client.post(PATH, data=json.dumps(data), content_type='application/json',
                                headers=self.headers, **kw)

    def test_disabled_by_default(self):
        client = create_app(environment='dev', policies=(POLICY,), store=self.store,
                            load_payload=self.resolve).test_client()
        self.assertEqual(client.post(PATH, json=envelope(), headers=self.headers).status_code, 503)
        self.assertEqual(self.store.calls, [])

    def test_authentication_precedes_payload_access(self):
        for headers in ({}, {'Authorization': 'Bearer wrong-gateway-token'}, {'Cookie': 'owner=true'}):
            self.assertEqual(self.client.post(PATH, json=envelope(), headers=headers).status_code, 401)
        self.assertEqual(self.store.calls, [])
        self.assertEqual(self.resolutions, [])

    def test_valid_request_uses_configured_producer_scope(self):
        result = self.post(envelope())
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json['state'], 'accepted')
        self.assertIs(self.store.calls[0][1], POLICY)
        self.assertEqual(self.resolutions, [(POLICY, 'cycle.brief')])

    def test_scope_destination_and_model_supplied_owner_refused(self):
        for changes in [dict(environment='prod'), dict(policy_hash='c' * 64),
                        dict(destination_binding='other-owner'), dict(principal_id='other'),
                        dict(payload_ref='/etc/passwd'), dict(payload_ref='https://evil.invalid')]:
            with self.subTest(changes=changes):
                self.assertEqual(self.post(envelope(**changes)).status_code, 400)
        self.assertEqual(self.store.calls, [])
        self.assertEqual(self.resolutions, [])

    def test_size_type_duplicates_timestamp_and_schema(self):
        self.assertEqual(self.client.post(PATH, data='{}', headers=self.headers).status_code, 415)
        self.assertEqual(self.client.post(PATH, data=b'x' * 65537, content_type='application/json',
                                         headers=self.headers).status_code, 413)
        raw = json.dumps(envelope())[:-1] + ',"kind":"recovery"}'
        self.assertEqual(self.client.post(PATH, data=raw, content_type='application/json',
                                         headers=self.headers).status_code, 400)
        for changes in [dict(occurred_at='2026-10-20T10:00:00'), dict(occurred_at=1),
                        dict(event_id='not-uuid'), dict(source_refs=[{}]),
                        dict(source_refs=[dict(id='x', digest='a' * 64)] * 65)]:
            with self.subTest(changes=changes):
                self.assertEqual(self.post(envelope(**changes)).status_code, 400)
        self.assertEqual(self.store.calls, [])

    def test_resolver_errors_are_sanitized_and_never_claim_no_effect(self):
        def fail(*_):
            raise RuntimeError('private-secret-canary')
        client = create_app(environment='dev', policies=(POLICY,), store=self.store,
                            load_payload=fail, enabled=True).test_client()
        result = client.post(PATH, json=envelope(), headers=self.headers)
        self.assertEqual(result.status_code, 503)
        self.assertNotIn('private-secret', result.get_data(as_text=True))

    def test_get_authentication_and_missing_event(self):
        url = PATH + '/' + str(uuid4())
        self.assertEqual(self.client.get(url).status_code, 401)
        self.assertEqual(self.client.get(url, headers=self.headers).status_code, 404)
        self.assertIs(self.store.calls[0][1], POLICY)

    def test_payload_hash_and_platform_note_limit(self):
        for payload, digest in [(TEXT, '0' * 64), (b'x' * 65537, '0' * 64),
                                (b'x' * 4001, hashlib.sha256(b'x' * 4001).hexdigest())]:
            with self.subTest(size=len(payload)), self.assertRaises(ActionContractError):
                brief_text(payload, digest)

    def test_event_is_immutable_and_digest_includes_destination_and_sources(self):
        data = envelope()
        event = StandingEvent(json.dumps(data).encode())
        changed = event.data
        changed['source_refs'].clear()
        self.assertEqual(len(event.data['source_refs']), 1)
        self.assertNotEqual(event.digest, StandingEvent(json.dumps(data | {'source_refs': []}).encode()).digest)
        self.assertNotIn('source1', repr(event))
        with self.assertRaises(ValueError):
            create_app(environment='dev', policies=(POLICY, replace(POLICY, producer_id='other')),
                       store=self.store, load_payload=self.resolve)


if __name__ == '__main__':
    unittest.main()
