from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from core.minimoi_actions.context import ActionContractError
from core.minimoi_actions.standing_control import ControlRequest, ControlStore
from postgres_fixture_support import DSN, PostgresFixture


@unittest.skipUnless(DSN, 'requires disposable MC_BINDING_TEST_DSN')
class ControlPostgresTests(PostgresFixture, unittest.TestCase):
    migrations = ('core/minimoi_actions/sql/003_standing_control.sql',)

    def setUp(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute('TRUNCATE guild.mc_standing_control_requests,guild.mc_standing_control')
        self.store = ControlStore(self.connect)

    def test_replay_does_not_reverse_later_command(self):
        pause = ControlRequest(str(uuid4()), 'pause', 0)
        self.assertEqual(self.store.command('owner', 'dev', pause)['desired']['version'], 1)
        self.store.command('owner', 'dev', ControlRequest(str(uuid4()), 'resume', 1))
        again = self.store.command('owner', 'dev', pause)
        self.assertEqual(again['code'], 'already_applied')
        self.assertEqual(again['applied_version'], 1)
        self.assertEqual(again['desired']['state'], 'enabled')
        with self.assertRaisesRegex(ActionContractError, 'control_request_reused'):
            self.store.command('owner', 'dev', ControlRequest(pause.request_id, 'resume', 0))
        self.assertEqual(self.store.desired('owner', 'dev')['version'], 2)

    def test_version_race_has_one_winner(self):
        def send(_):
            try:
                return self.store.command('owner', 'dev', ControlRequest(str(uuid4()), 'resume', 0))['code']
            except ActionContractError as exc:
                return str(exc)
        with ThreadPoolExecutor(max_workers=4) as pool:
            result = list(pool.map(send, range(4)))
        self.assertEqual(result.count('accepted'), 1)
        self.assertEqual(result.count('control_version_conflict'), 3)
        self.assertEqual(self.store.desired('owner', 'dev')['version'], 1)

    def test_retry_same_request_serializes_and_scopes_are_separate(self):
        request = ControlRequest(str(uuid4()), 'resume', 0)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.store.command('owner', 'dev', request)['code'], range(4)))
        self.assertEqual(results.count('accepted'), 1)
        self.assertEqual(results.count('already_applied'), 3)
        self.assertIsNone(self.store.desired('other-owner', 'dev'))
        self.assertIsNone(self.store.desired('owner', 'prod'))

    def test_export_failure_recovers_current_version_without_replaying_command(self):
        self.store.command('owner', 'dev', ControlRequest(str(uuid4()), 'pause', 0))
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)/'control.json'
            with patch('core.minimoi_actions.standing_control.os.replace', side_effect=OSError('fixture')):
                with self.assertRaises(Exception):
                    self.store.export('owner', 'dev', target)
            self.assertFalse(target.exists())
            self.assertEqual(list(Path(tmp).iterdir()), [])
            self.store.command('owner', 'dev', ControlRequest(str(uuid4()), 'resume', 1))
            self.store.export('owner', 'dev', target)
            doc = json.loads(target.read_text())
            self.assertEqual((doc['version'], doc['state']), (2, 'enabled'))
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.store.export('owner', 'dev', target), doc)

    def test_missing_scope_never_exports_default_enable(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)/'control.json'
            self.assertIsNone(self.store.export('owner', 'dev', target))
            self.assertFalse(target.exists())
