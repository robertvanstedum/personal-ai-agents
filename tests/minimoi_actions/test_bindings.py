"""Run-binding decisions only: no claim of durable registry or runtime proof."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest

from core.minimoi_actions.context import ActionContext, ActionContractError
from core.minimoi_actions.bindings import (
    RuntimeIdentity, RuntimeCall, RunBinding, bind_run, check_bound_call, end_binding,
)

NOW = datetime(2026, 10, 20, 18, tzinfo=timezone.utc)


def fixture():
    context = ActionContext('owner', 'mc', 'dev', 'conversation', 'turn', 'dispatch',
                            1, NOW + timedelta(minutes=2), 'policy', 'role', 'private', 'budget')
    identity = RuntimeIdentity('runtime', 'boot-1', 'mc', 'dev')
    binding = RunBinding(context, identity, 'session')
    call = RuntimeCall(identity, 'session', 'run-1', 'dispatch', 1)
    return binding, call


LIVE = dict(now=NOW, current_dispatch_id='dispatch', current_generation=1)


class BindingTests(unittest.TestCase):
    def test_tools_cannot_register_themselves(self):
        binding, call = fixture()
        with self.assertRaisesRegex(ActionContractError, '^binding_not_registered$'):
            check_bound_call(binding, call, **LIVE)
        self.assertEqual(binding.state, 'awaiting_run')

    def test_exact_registration_is_idempotent_and_preserves_admission(self):
        binding, call = fixture()
        bound = bind_run(binding, call, **LIVE)
        self.assertEqual(bind_run(bound, call, **LIVE), bound)
        self.assertIs(check_bound_call(bound, call, **LIVE), binding.context)
        self.assertEqual(binding.state, 'awaiting_run')

    def test_no_rebinding_to_another_run(self):
        binding, call = fixture()
        bound = bind_run(binding, call, **LIVE)
        with self.assertRaisesRegex(ActionContractError, '^binding_run_conflict$'):
            bind_run(bound, replace(call, run_id='run-2'), **LIVE)
        with self.assertRaisesRegex(ActionContractError, '^binding_not_registered$'):
            check_bound_call(bound, replace(call, run_id='run-2'), **LIVE)

    def test_no_cross_session_dispatch_generation_or_runtime_identity(self):
        binding, call = fixture()
        bound = bind_run(binding, call, **LIVE)
        alternatives = [replace(call, session_key='other'), replace(call, dispatch_id='other'),
                        replace(call, generation=2)]
        for field, value in [('runtime_id', 'other'), ('instance_id', 'boot-2'),
                             ('actor_id', 'other'), ('environment', 'prod')]:
            alternatives.append(replace(call, identity=replace(call.identity, **{field: value})))
        for other in alternatives:
            for function in (bind_run, check_bound_call):
                with self.subTest(other=other, function=function), self.assertRaisesRegex(
                        ActionContractError, '^binding_call_mismatch$'):
                    function(bound, other, **LIVE)

    def test_superseded_call_and_delayed_registration_both_refused(self):
        binding, call = fixture()
        bound = bind_run(binding, call, **LIVE)
        for current in [dict(current_dispatch_id='new-dispatch', current_generation=1),
                        dict(current_dispatch_id='dispatch', current_generation=2)]:
            for function, item in [(bind_run, binding), (check_bound_call, bound)]:
                with self.subTest(current=current), self.assertRaisesRegex(
                        ActionContractError, '^binding_superseded$'):
                    function(item, call, now=NOW, **current)

    def test_exact_expiry_blocks_registration_and_calls(self):
        binding, call = fixture()
        for function, item in [(bind_run, binding),
                               (check_bound_call, bind_run(binding, call, **LIVE))]:
            with self.assertRaisesRegex(ActionContractError, '^binding_expired$'):
                function(item, call, **(LIVE | dict(now=binding.context.expires_at)))

    def test_cancel_and_close_keep_tombstones_and_cannot_reactivate(self):
        binding, call = fixture()
        for bound in [binding, bind_run(binding, call, **LIVE)]:
            for cancelled in [False, True]:
                ended = end_binding(bound, cancelled=cancelled)
                self.assertEqual(ended.run_id, bound.run_id)
                self.assertIs(end_binding(ended, cancelled=not cancelled), ended)
                for function in (bind_run, check_bound_call):
                    with self.assertRaisesRegex(ActionContractError, '^binding_inactive$'):
                        function(ended, call, **LIVE)

    def test_constructor_invariants_and_fixed_errors(self):
        binding, call = fixture()
        for kwargs in [dict(state='other'), dict(state=[]), dict(state='bound'),
                       dict(run_id='unexpected'), dict(session_key='bad\u202e'),
                       dict(identity=replace(call.identity, environment='prod'))]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ActionContractError):
                replace(binding, **kwargs)
        for kwargs in [dict(generation=True), dict(run_id=''), dict(identity={})]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ActionContractError):
                replace(call, **kwargs)
        for obj in (binding, call, call.identity):
            self.assertNotIn('boot-1', repr(obj))
            self.assertNotIn('dispatch', repr(obj))


if __name__ == '__main__':
    unittest.main()
