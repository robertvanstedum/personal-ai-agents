from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import unittest
from uuid import uuid4

from core.minimoi_actions.binding_store import BindingStore
from core.minimoi_actions.bindings import RuntimeIdentity
from core.minimoi_actions.context import ActionContext
from services.minimoi_actions.runtime_listener import ACTION_PATH, BIND_PATH, START_PATH, RuntimePolicy, create_app
from postgres_fixture_support import DSN, PostgresFixture


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


@unittest.skipUnless(DSN, 'requires disposable MC_BINDING_TEST_DSN')
class ListenerTests(PostgresFixture, unittest.TestCase):
    migrations = ('core/minimoi_actions/sql/001_runtime_bindings.sql',
                  'core/minimoi_actions/sql/007_runtime_execution_start.sql')

    def setUp(self):
        self.identity = RuntimeIdentity('fixture-runtime', 'boot-one', 'mc', 'dev')
        self.start_token = 'synthetic-starter-'+'s'*32
        self.bind_token, self.tool_token = 'synthetic-binder-'+'b'*32, 'synthetic-executor-'+'e'*32
        self.policy = RuntimePolicy(self.identity, digest(self.bind_token), digest(self.tool_token),
                                    frozenset({('fixture.observe', 1)}), digest(self.start_token))
        self.bindings = BindingStore(self.connect)
        self.result_mode = 'valid'
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute('TRUNCATE guild.mc_runtime_bindings,guild.mc_runtime_sessions')
            cur.execute('CREATE TABLE IF NOT EXISTS guild.mc_listener_fixture (principal_id text,turn_id text)')
            cur.execute('TRUNCATE guild.mc_listener_fixture')
        self.client = self.app().test_client()
        self.context = ActionContext('owner', 'mc', 'dev', 'conversation-A', 'turn-A', 'dispatch-A', 1,
            datetime.now(timezone.utc)+timedelta(minutes=5), 'policy', 'role', 'recorded', 'budget')
        self.ref = self.bindings.admit(self.context, self.identity, 'session-A').context_reference
        self.metadata = dict(schema='mc-runtime-bind/1', context_reference=self.ref, session_key='session-A',
                             run_id='run-A', dispatch_id='dispatch-A', generation=1)
        self.request = dict(operation='fixture.observe', operation_version=1, arguments={})

    def app(self, **kwargs):
        return create_app(environment='dev', policies=kwargs.pop('policies', (self.policy,)),
                          bindings=self.bindings, execute=self.execute, **({'enabled': True} | kwargs))

    def execute(self, conn, context, tool):
        # A transaction-local synthetic write exposes accidental commits on
        # malformed responses. This is not a production capability/dispatcher.
        with conn.cursor() as cur:
            cur.execute('INSERT INTO guild.mc_listener_fixture VALUES (%s,%s)',
                        (context.principal_id, context.turn_id))
        if self.result_mode == 'exception':
            raise RuntimeError('PRIVATE upstream secret '+self.tool_token)
        result = dict(version=1, operation_id=str(uuid4()), status='done', code='fixture_verified',
                      receipt_id=str(uuid4()), work_id=None, links=[], confirmation=None,
                      verified_state={'turn':context.turn_id})
        if self.result_mode == 'oversize':
            result['verified_state'] = {'text': 'x'*65536}
        if self.result_mode == 'invalid':
            result['status'] = 'invented'
        return result

    def headers(self, metadata=None, token=None):
        m = metadata or self.metadata
        return {'Authorization':'Bearer '+(token or self.tool_token),
                'X-MC-Context':m['context_reference'], 'X-MC-Session':m['session_key'],
                'X-MC-Run':m['run_id'], 'X-MC-Dispatch':m['dispatch_id'],
                'X-MC-Generation':str(m['generation'])}

    def bind(self, metadata=None, token=None, client=None):
        return (client or self.client).post(BIND_PATH, json=metadata or self.metadata,
                 headers={'Authorization':'Bearer '+(token or self.bind_token)})

    def start(self, metadata=None, token=None, client=None):
        return (client or self.client).post(START_PATH,
                 json=(metadata or self.metadata) | {'schema': 'mc-runtime-start/1'},
                 headers={'Authorization': 'Bearer '+(token or self.start_token)})

    def ready(self, metadata=None):
        self.assertEqual(self.bind(metadata).status_code, 200)
        self.assertEqual(self.start(metadata).status_code, 200)

    def action(self, metadata=None, body=None, token=None, client=None):
        return (client or self.client).post(ACTION_PATH, json=self.request if body is None else body,
                                           headers=self.headers(metadata, token))

    def count(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM guild.mc_listener_fixture')
            return cur.fetchone()[0]

    def test_start_credential_is_separate_and_replay_is_not_an_ack(self):
        self.assertEqual(self.start().status_code, 409)
        self.assertEqual(self.bind().status_code, 200)
        for token in (self.bind_token, self.tool_token):
            self.assertEqual(self.start(token=token).status_code, 401)
        self.assertEqual(self.bind(token=self.start_token).status_code, 401)
        self.assertEqual(self.action(token=self.start_token).status_code, 401)
        self.assertEqual(self.start().json, {'schema': 'mc-runtime-start-ack/1', 'status': 'started'})
        self.assertEqual(self.start().status_code, 409)
        self.assertEqual(self.count(), 0)

    def test_lost_start_ack_does_not_make_replay_safe(self):
        self.assertEqual(self.bind().status_code, 200)
        original = self.bindings.start
        def lost_ack(reference, call):
            original(reference, call)
            raise RuntimeError('synthetic_after_commit')
        self.bindings.start = lost_ack
        try:
            reply = self.start()
            self.assertEqual(reply.status_code, 503)
            self.assertEqual(reply.json, {'error': 'runtime_start_unconfirmed'})
        finally:
            self.bindings.start = original
        self.assertEqual(self.start().status_code, 409)
        self.assertEqual(self.count(), 0)

    def test_start_refuses_forged_metadata_and_colliding_credentials(self):
        self.assertEqual(self.bind().status_code, 200)
        for metadata in (self.metadata | {'run_id': 'other'}, self.metadata | {'generation': 2},
                         self.metadata | {'context_reference': 'Z'*43}, self.metadata | {'owner': True}):
            self.assertIn(self.start(metadata).status_code, (400, 409))
        for token in (self.policy.bind_token_sha256, self.policy.execute_token_sha256):
            with self.assertRaisesRegex(ValueError, 'runtime_listener_policy_invalid'):
                replace(self.policy, start_token_sha256=token)
        self.assertEqual(self.start().status_code, 200)

    def test_disabled_and_distinct_credentials_and_no_owner_cookie_bypass(self):
        self.assertEqual(self.action(client=self.app(enabled=False).test_client()).status_code, 503)
        self.assertEqual(self.action(token=self.bind_token).status_code, 401)
        self.assertEqual(self.bind(token=self.tool_token).status_code, 401)
        self.assertEqual(self.client.post(ACTION_PATH, json=self.request,
                         headers={'Cookie':'owner=true'}).status_code, 401)
        self.assertEqual(self.action(token='incorrect-'+'x'*32).status_code, 401)
        self.assertEqual(self.count(), 0)

    def test_first_tool_cannot_self_bind_then_registered_call_keeps_structured_result(self):
        self.assertEqual(self.action().status_code, 400)
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.bind().status_code, 200)
        self.assertEqual(self.bind().status_code, 200)
        self.assertEqual(self.action().status_code, 400)
        self.assertEqual(self.start().status_code, 200)
        result = self.action()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json['verified_state'], {'turn':'turn-A'})
        self.assertNotIn(self.ref, result.text)
        self.assertNotIn(self.tool_token, result.text)
        self.assertEqual(self.count(), 1)

    def test_model_fields_cannot_override_context_or_select_another_capability(self):
        self.ready()
        for key in ('context_reference', 'principal_id', 'run_id', 'grant_ids', 'approved'):
            self.assertEqual(self.action(body=self.request | {key:'forged'}).status_code, 400)
        self.assertEqual(self.action(body=self.request | {'operation':'shell.execute'}).status_code, 403)
        self.assertEqual(self.action(body=self.request | {'operation_version':2}).status_code, 403)
        self.assertEqual(self.count(), 0)

    def test_overlapping_sessions_stay_separate_and_old_generation_is_fenced(self):
        self.ready()
        b = replace(self.context, conversation_id='conversation-B', turn_id='turn-B', dispatch_id='dispatch-B')
        ref_b = self.bindings.admit(b, self.identity, 'session-B').context_reference
        meta_b = dict(self.metadata, context_reference=ref_b, session_key='session-B', run_id='run-B', dispatch_id='dispatch-B')
        self.assertEqual(self.bind(meta_b).status_code, 200)
        self.assertEqual(self.start(meta_b).status_code, 200)
        self.assertEqual(self.action().json['verified_state']['turn'], 'turn-A')
        self.assertEqual(self.action(meta_b).json['verified_state']['turn'], 'turn-B')
        self.assertEqual(self.action(dict(self.metadata, context_reference=ref_b)).status_code, 400)
        next_a = replace(self.context, dispatch_id='dispatch-next-A', turn_id='turn-next-A', generation=2)
        new_ref = self.bindings.admit(next_a, self.identity, 'session-A').context_reference
        new_meta = dict(self.metadata, dispatch_id=next_a.dispatch_id, context_reference=new_ref, generation=2, run_id='run-next-A')
        self.assertEqual(self.action().status_code, 400)
        self.assertEqual(self.bind().status_code, 400)
        self.assertEqual(self.bind(new_meta).status_code, 200)
        self.assertEqual(self.start(new_meta).status_code, 200)
        self.assertEqual(self.action(new_meta).json['verified_state']['turn'], 'turn-next-A')
        self.assertEqual(self.action(meta_b).status_code, 200)

    def test_cancel_and_runtime_restart_refuse_retained_callbacks(self):
        self.ready()
        restarted = replace(self.policy, identity=replace(self.identity, instance_id='boot-two'),
                            bind_token_sha256=digest('new-bind-'+'b'*32), execute_token_sha256=digest('new-tool-'+'e'*32))
        client = self.app(policies=(restarted,)).test_client()
        self.assertEqual(self.action(client=client).status_code, 401)
        self.assertEqual(self.action(client=client, token='new-tool-'+'e'*32).status_code, 400)
        self.bindings.end(self.identity, 'session-A', 'dispatch-A', 1, cancelled=True)
        self.assertEqual(self.action().status_code, 400)
        self.assertEqual(self.bind().status_code, 400)
        self.assertEqual(self.count(), 0)

    def test_malformed_output_or_exception_rolls_back_local_effect_and_redacts_error(self):
        self.ready()
        for mode in ('oversize', 'invalid', 'exception'):
            self.result_mode = mode
            result = self.action()
            self.assertEqual(result.status_code, 503)
            self.assertEqual(result.json, {'error':'action_outcome_unconfirmed'})
            self.assertNotIn('PRIVATE', result.text)
            self.assertEqual(self.count(), 0)

    def test_size_duplicates_and_forged_binding_metadata_are_refused(self):
        self.ready()
        for metadata in (dict(self.metadata, generation=True), dict(self.metadata, run_id='different'),
                         dict(self.metadata, principal_id='other'), dict(self.metadata, context_reference='x'*43)):
            self.assertEqual(self.bind(metadata).status_code, 400)
        result = self.client.post(ACTION_PATH, data=b'{"operation":"fixture.observe","operation":"other.op",'
            b'"operation_version":1,"arguments":{}}', headers=self.headers(), content_type='application/json')
        self.assertEqual(result.status_code, 400)
        result = self.client.post(ACTION_PATH, data=b'x'*65537, headers=self.headers(), content_type='application/json')
        self.assertEqual(result.status_code, 413)
        self.assertEqual(self.count(), 0)


if __name__ == '__main__':
    unittest.main()
