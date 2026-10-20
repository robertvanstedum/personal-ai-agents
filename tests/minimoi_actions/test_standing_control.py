from datetime import datetime, timezone, timedelta
import tempfile
from pathlib import Path
import unittest
from uuid import uuid4

from core.minimoi_actions.context import ActionContractError
from core.minimoi_actions.standing_control import ControlRequest, effective_status, _publish


class ControlTests(unittest.TestCase):
    def test_request_is_strict_and_bounded(self):
        for action, version in [('stop', 0), ('pause', True), ('resume', -1), ('pause', 1.0), ('pause', 2**63)]:
            with self.subTest(action=action, version=version), self.assertRaises(ActionContractError):
                ControlRequest(str(uuid4()), action, version)
        with self.assertRaises(ActionContractError):
            ControlRequest('not-a-uuid', 'pause', 0)

    def test_only_matching_ack_means_effective_including_second_precision(self):
        now = datetime(2026, 10, 20, 18, 0, 0, 900000, tzinfo=timezone.utc)
        desired = {'version': 4, 'state': 'paused', 'updated_at': now.isoformat()}
        ack = {'schema': 'mc-standing-control-ack/1', 'applied_version': 4,
               'applied_state': 'paused', 'effective_at': now.replace(microsecond=0).isoformat(), 'reason': None}
        self.assertEqual(effective_status(desired, ack, now=now)['state'], 'paused')
        for bad in [None, [], {}, dict(ack, applied_version=3), dict(ack, applied_version=True),
                    dict(ack, applied_state='enabled'), dict(ack, reason='control_unreadable_fail_safe'),
                    dict(ack, effective_at=(now-timedelta(seconds=2)).isoformat()),
                    dict(ack, effective_at=(now+timedelta(seconds=2)).isoformat()),
                    dict(ack, effective_at='bad')]:
            self.assertEqual(effective_status(desired, bad, now=now)['state'], 'pending')
        self.assertEqual(effective_status(None, ack, now=now)['state'], 'pending')

    def test_export_rejects_symlinks_and_unsafe_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root/'control.json'
            target.symlink_to(root/'elsewhere')
            with self.assertRaises(ActionContractError):
                _publish(target, {'state': 'enabled'})
            target.unlink();root.chmod(0o777)
            with self.assertRaises(ActionContractError):
                _publish(target, {'state': 'enabled'})
            root.chmod(0o700)
