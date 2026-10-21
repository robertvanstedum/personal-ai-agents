"""Real Postgres acceptance, opt-in on an isolated local test database.

MC_BINDING_TEST_DSN must point to a local database named mc12_test_*. This suite
creates guild plus the two binding tables and truncates those tables per test.
Never use an application database. No fake cursor substitutes for these checks.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import threading
import time
import unittest

from core.minimoi_actions.context import ActionContext, ActionContractError
from core.minimoi_actions.bindings import RuntimeIdentity, RuntimeCall
from core.minimoi_actions.binding_store import BindingStore, RegistryUnavailable

from postgres_fixture_support import DSN, PostgresFixture


@unittest.skipUnless(DSN, 'isolated local MC_BINDING_TEST_DSN required')
class PostgresBindingTests(PostgresFixture, unittest.TestCase):
    migrations = ('core/minimoi_actions/sql/001_runtime_bindings.sql',
                  'core/minimoi_actions/sql/007_runtime_execution_start.sql')

    def setUp(self):
        with self.guard.cursor() as cur:
            cur.execute('TRUNCATE guild.mc_runtime_bindings, guild.mc_runtime_sessions')
        self.guard.commit()
        self.store = BindingStore(self.connect)
        self.identity = RuntimeIdentity('runtime', 'boot-1', 'mc', 'dev')
        self.context = ActionContext('owner', 'mc', 'dev', 'conversation', 'turn', 'dispatch',
                                     1, datetime.now(timezone.utc) + timedelta(minutes=5),
                                     'policy', 'role', 'private', 'budget')
        self.call = RuntimeCall(self.identity, 'session', 'run-1', 'dispatch', 1)

    def register(self):
        registration = self.store.admit(self.context, self.identity, 'session')
        self.store.bind(registration.context_reference, self.call)
        self.store.start(registration.context_reference, self.call)
        return registration.context_reference

    def test_start_requires_bound_context_and_is_durable_single_use(self):
        reference = self.store.admit(self.context, self.identity, 'session').context_reference
        with self.assertRaisesRegex(ActionContractError, 'binding_not_registered'):
            self.store.start(reference, self.call)
        self.store.bind(reference, self.call)
        with self.assertRaisesRegex(ActionContractError, 'binding_not_started'):
            with self.store.locked_context(reference, self.call):
                self.fail('unstarted invocation executed')
        self.store.start(reference, self.call)
        with self.assertRaisesRegex(ActionContractError, 'binding_already_started'):
            BindingStore(self.connect).start(reference, self.call)
        # Retrying registration must not erase the already-consumed claim.
        self.store.bind(reference, self.call)
        with self.assertRaisesRegex(ActionContractError, 'binding_already_started'):
            self.store.start(reference, self.call)

    def test_two_starters_have_exactly_one_durable_winner(self):
        reference = self.store.admit(self.context, self.identity, 'session').context_reference
        self.store.bind(reference, self.call)
        barrier = threading.Barrier(2)
        def attempt(_):
            barrier.wait(timeout=3)
            try:
                BindingStore(self.connect).start(reference, self.call)
                return 'started'
            except ActionContractError as exc:
                return str(exc)
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, (1, 2)))
        self.assertCountEqual(outcomes, ['started', 'binding_already_started'])

    def test_start_transaction_rollback_leaves_no_claim(self):
        from contextlib import contextmanager
        class FailBeforeCommit(BindingStore):
            @contextmanager
            def _transaction(self):
                with super()._transaction() as conn:
                    yield conn
                    raise RuntimeError('synthetic_before_commit')
        reference = self.store.admit(self.context, self.identity, 'session').context_reference
        self.store.bind(reference, self.call)
        with self.assertRaisesRegex(RuntimeError, 'synthetic_before_commit'):
            FailBeforeCommit(self.connect).start(reference, self.call)
        self.store.start(reference, self.call)
        with self.store.locked_context(reference, self.call) as (_, context):
            self.assertEqual(context, self.context)

    def test_start_rechecks_expiry_after_waiting_for_session_lock(self):
        context = replace(self.context, expires_at=datetime.now(timezone.utc)+timedelta(seconds=1))
        reference = self.store.admit(context, self.identity, 'session').context_reference
        self.store.bind(reference, self.call)
        entering = threading.Event()
        def attempt():
            entering.set()
            self.store.start(reference, self.call)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT generation FROM guild.mc_runtime_sessions FOR UPDATE")
                pending = pool.submit(attempt)
                self.assertTrue(entering.wait(timeout=1))
                time.sleep(0.1)
                self.assertFalse(pending.done(), 'start must wait for session lock')
                # Let the admitted expiry pass while holding the session lock.
                time.sleep(max(0, (context.expires_at-datetime.now(timezone.utc)).total_seconds())+0.05)
            with self.assertRaisesRegex(ActionContractError, 'binding_expired'):
                pending.result(timeout=3)
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute('SELECT started_at FROM guild.mc_runtime_bindings WHERE dispatch_id=%s',
                        (context.dispatch_id,))
            self.assertIsNone(cur.fetchone()[0])

    def test_revocation_and_boot_change_prevent_start(self):
        reference = self.store.admit(self.context, self.identity, 'session').context_reference
        self.store.bind(reference, self.call)
        with self.assertRaisesRegex(ActionContractError, 'binding_call_mismatch'):
            self.store.start(reference, replace(self.call,
                identity=replace(self.identity, instance_id='new-boot')))
        self.store.end(self.identity, 'session', 'dispatch', 1, cancelled=True)
        with self.assertRaisesRegex(ActionContractError, 'binding_inactive'):
            self.store.start(reference, self.call)

    def test_registration_and_terminal_state_survive_store_recreation(self):
        reference = self.register()
        restarted = BindingStore(self.connect)
        with restarted.locked_context(reference, self.call) as (_, context):
            self.assertEqual(context, self.context)
        self.assertTrue(restarted.end(self.identity, 'session', 'dispatch', 1, cancelled=True))
        self.assertFalse(restarted.end(self.identity, 'session', 'dispatch', 1, cancelled=True))
        with self.assertRaisesRegex(ActionContractError, '^binding_inactive$'):
            with BindingStore(self.connect).locked_context(reference, self.call):
                self.fail('cancelled binding accepted')

    def test_missing_registration_wrong_reference_and_restart_identity_refused(self):
        reference = self.store.admit(self.context, self.identity, 'session').context_reference
        with self.assertRaisesRegex(ActionContractError, '^binding_not_registered$'):
            with self.store.locked_context(reference, self.call):
                self.fail('tool self-registered')
        self.store.bind(reference, self.call)
        for ref, call in [('A' * 43, self.call),
                          (reference, replace(self.call, session_key='other')),
                          (reference, replace(self.call, identity=replace(self.identity, instance_id='boot-2')))]:
            with self.subTest(call=call), self.assertRaises(ActionContractError):
                with self.store.locked_context(ref, call):
                    self.fail('mismatched call accepted')

    def test_next_generation_revokes_old_run_and_late_registration(self):
        old = self.register()
        second = replace(self.context, dispatch_id='dispatch-2', turn_id='turn-2', generation=2)
        self.store.admit(second, self.identity, 'session')
        for method in (self.store.bind, self.store.start):
            with self.assertRaisesRegex(ActionContractError, '^binding_superseded$'):
                method(old, self.call)
        with self.assertRaisesRegex(ActionContractError, '^binding_superseded$'):
            with self.store.locked_context(old, self.call):
                self.fail('old run accepted')

    def test_runtime_run_cannot_attach_to_two_dispatches_even_after_close(self):
        reference = self.register()
        self.store.end(self.identity, 'session', 'dispatch', 1, cancelled=False)
        second = replace(self.context, dispatch_id='dispatch-2', conversation_id='other')
        new = self.store.admit(second, self.identity, 'session-2')
        with self.assertRaisesRegex(ActionContractError, '^binding_storage_conflict$'):
            self.store.bind(new.context_reference, replace(self.call, dispatch_id='dispatch-2', session_key='session-2'))
        with self.assertRaisesRegex(ActionContractError, '^binding_inactive$'):
            self.store.bind(reference, self.call)

    def test_concurrent_same_generation_has_one_winner(self):
        start = threading.Barrier(2)
        def attempt(i):
            start.wait(timeout=3)
            try:
                self.store.admit(replace(self.context, dispatch_id=f'dispatch-{i}'), self.identity, 'session')
                return 'admitted'
            except ActionContractError as exc:
                return str(exc)
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, [1, 2]))
        self.assertCountEqual(outcomes, ['admitted', 'binding_generation_conflict'])

    def test_failed_admission_rolls_back_supersession(self):
        reference = self.register()
        with self.assertRaisesRegex(ActionContractError, '^binding_storage_conflict$'):
            self.store.admit(replace(self.context, generation=2), self.identity, 'session')
        with self.store.locked_context(reference, self.call) as (_, context):
            self.assertEqual(context.generation, 1)

    def test_cancel_is_serialized_with_receipt_preparation(self):
        reference = self.register()
        started = threading.Event()
        finished = threading.Event()
        def cancel():
            started.set()
            result = self.store.end(self.identity, 'session', 'dispatch', 1, cancelled=True)
            finished.set()
            return result
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.store.locked_context(reference, self.call):
                result = pool.submit(cancel)
                self.assertTrue(started.wait(2))
                self.assertFalse(finished.wait(.1))
            self.assertTrue(result.result(timeout=4))
        with self.assertRaisesRegex(ActionContractError, '^binding_inactive$'):
            with self.store.locked_context(reference, self.call):
                self.fail('cancelled binding accepted')

    def test_cross_owner_session_and_actor_close_refused(self):
        self.register()
        with self.assertRaisesRegex(ActionContractError, '^binding_session_owner_conflict$'):
            self.store.admit(replace(self.context, principal_id='other', generation=2, dispatch_id='other'),
                             self.identity, 'session')
        with self.assertRaisesRegex(ActionContractError, '^binding_identity_mismatch$'):
            self.store.end(replace(self.identity, actor_id='other'), 'session', 'dispatch', 1, cancelled=True)

    def test_autocommit_connections_are_refused(self):
        def autocommit():
            conn = self.connect()
            conn.autocommit = True
            return conn
        with self.assertRaisesRegex(RegistryUnavailable, '^binding_transaction_required$'):
            BindingStore(autocommit).admit(self.context, self.identity, 'session')


if __name__ == '__main__':
    unittest.main()
