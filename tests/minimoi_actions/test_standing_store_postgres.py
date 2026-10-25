"""Actual note/receipt atomicity and HTTP readback on a disposable database."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
import threading
import unittest
from unittest.mock import patch

from core.minimoi_actions.context import ActionContractError
from core.minimoi_actions.standing import Destination, StandingEvent
from core.minimoi_actions.standing_store import StandingStore
from services.minimoi_actions.standing_ingress import create_app, PATH
from postgres_fixture_support import DSN, PostgresFixture
from test_standing_ingress import TOKEN, TEXT, POLICY, envelope


@unittest.skipUnless(DSN, 'isolated local MC_BINDING_TEST_DSN required')
class StandingStorePostgresTests(PostgresFixture, unittest.TestCase):
    migrations = ('minimoi_portal/guild_ui/sql/001_floor_b1.sql',
                  'core/minimoi_actions/sql/002_standing_ingress.sql')

    def setUp(self):
        with self.guard.cursor() as cur:
            cur.execute('TRUNCATE guild.mc_standing_events, guild.floor_messages RESTART IDENTITY')
        self.guard.commit()
        self.store = StandingStore(self.connect)
        self.event = StandingEvent(json.dumps(envelope()).encode())
        self.loads = []

    def load(self, policy, object_id):
        self.loads.append((policy, object_id))
        return TEXT

    def count(self, table):
        assert table in ('mc_standing_events', 'floor_messages')
        with self.guard.cursor() as cur:
            cur.execute('SELECT count(*) FROM guild.' + table)
            count = cur.fetchone()[0]
        self.guard.commit()
        return count

    def test_brief_kept_once_and_replay_needs_no_artifact_reread(self):
        first, status = self.store.accept(self.event, POLICY, self.load)
        again, _ = self.store.accept(self.event, POLICY, lambda *_: self.fail('reread artifact'))
        self.assertEqual((status, first['state'], again['state']), (200, 'accepted', 'already_applied'))
        self.assertEqual(first['record_id'], again['record_id'])
        self.assertEqual(self.count('floor_messages'), 1)
        with self.guard.cursor() as cur:
            cur.execute('SELECT floor,author_kind,author_label,text FROM guild.floor_messages')
            self.assertEqual(cur.fetchone(), ('guild/test-conversation', 'platform', 'MC standing operations', TEXT.decode()))
        self.guard.commit()

    def test_changed_envelope_is_conflict_and_original_receipt_survives(self):
        original, _ = self.store.accept(self.event, POLICY, self.load)
        changed = StandingEvent(json.dumps(self.event.data | {'source_refs': []}).encode())
        conflict, status = self.store.accept(changed, POLICY, self.load)
        self.assertEqual((status, conflict['code']), (409, 'event_id_reused'))
        self.assertEqual(self.store.get(self.event.data['event_id'], POLICY)['record_id'], original['record_id'])
        self.assertEqual(self.count('floor_messages'), 1)
        with self.guard.cursor() as cur:
            cur.execute('SELECT conflict_count,last_conflict_digest FROM guild.mc_standing_events')
            self.assertEqual(cur.fetchone(), (1, changed.digest))
        self.guard.commit()

    def test_note_and_receipt_roll_back_together(self):
        from minimoi_portal.guild_ui.stores import FloorStores
        original = FloorStores.add_note
        def fail_after_note(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('injected after note before receipt')
        with patch.object(FloorStores, 'add_note', fail_after_note), self.assertRaises(RuntimeError):
            self.store.accept(self.event, POLICY, self.load)
        self.assertEqual(self.count('floor_messages'), 0)
        self.assertEqual(self.count('mc_standing_events'), 0)
        self.assertEqual(self.store.accept(self.event, POLICY, self.load)[0]['state'], 'accepted')

    def test_payload_error_keeps_nothing_and_scope_is_not_producer_selected(self):
        with self.assertRaises(ActionContractError):
            self.store.accept(self.event, POLICY, lambda *_: b'wrong bytes')
        self.assertEqual(self.count('mc_standing_events'), 0)
        foreign = StandingEvent(json.dumps(self.event.data | {'destination_binding': 'somebody-else'}).encode())
        with self.assertRaises(ActionContractError):
            self.store.accept(foreign, POLICY, lambda *_: self.fail('foreign artifact read'))
        self.assertEqual(self.count('floor_messages'), 0)

    def test_concurrent_duplicate_commits_one_note(self):
        start = threading.Barrier(2)
        def send(_):
            start.wait(timeout=3)
            return self.store.accept(self.event, POLICY, self.load)[0]['state']
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(send, [1, 2])), ['accepted', 'already_applied'])
        self.assertEqual(self.count('floor_messages'), 1)

    def test_finding_is_deferred_without_fake_work_or_payload_read(self):
        event = StandingEvent(json.dumps(self.event.data | {'kind': 'finding', 'destination_binding': None}).encode())
        result, _ = self.store.accept(event, POLICY, lambda *_: self.fail('premature import'))
        self.assertEqual(result['state'], 'deferred')
        self.assertIsNone(result['work_id'])
        self.assertIsNone(result['record_id'])
        self.assertEqual(self.count('floor_messages'), 0)

    def test_http_delivery_and_scope_bound_reconciliation(self):
        other_token = 'another-synthetic-producer-token-for-tests'
        other = replace(POLICY, producer_id='other', principal_id='other-owner',
                        token_sha256=hashlib.sha256(other_token.encode()).hexdigest(),
                        destinations=(Destination('other-ops', 'guild/other'),))
        client = create_app(environment='dev', policies=(POLICY, other), store=self.store,
                            load_payload=self.load, enabled=True).test_client()
        headers = {'Authorization': 'Bearer ' + TOKEN}
        result = client.post(PATH, data=self.event.raw, content_type='application/json', headers=headers)
        self.assertEqual((result.status_code, result.json['state']), (200, 'accepted'))
        url = PATH + '/' + self.event.data['event_id']
        self.assertEqual(client.get(url, headers=headers).json['record_id'], result.json['record_id'])
        self.assertEqual(client.get(url, headers={'Authorization': 'Bearer ' + other_token}).status_code, 404)
        self.assertEqual(self.count('floor_messages'), 1)


if __name__ == '__main__':
    unittest.main()
