import unittest
from uuid import uuid4

from minimoi_portal.guild_ui.stores import FloorStores, Author, MASTER_CRAFTSMAN
from postgres_fixture_support import DSN, PostgresFixture


@unittest.skipUnless(DSN,'requires disposable MC_BINDING_TEST_DSN')
class OwnerNotePostgresTests(PostgresFixture,unittest.TestCase):
    migrations = ('minimoi_portal/guild_ui/sql/001_floor_b1.sql',)

    def setUp(self):
        self.floor = FloorStores(lambda:DSN,connect=lambda _:self.connect(),floor='source-'+uuid4().hex)
        self.owner = Author('synthetic-owner','owner','Owner')

    def test_sql_owner_kind_floor_and_byte_predicates(self):
        self.floor.add_note('mine','🙂'*20,self.owner)
        self.floor.add_note('foreign','foreign source',Author('visitor','owner','Visitor'))
        self.floor.add_note('assistant','not an owner statement',MASTER_CRAFTSMAN)
        self.assertIsNone(self.floor.get_owner_note('mine','synthetic-owner',max_bytes=79))
        self.assertEqual(self.floor.get_owner_note('mine','synthetic-owner',max_bytes=80)['text'],'🙂'*20)
        self.assertIsNone(self.floor.get_owner_note('foreign','synthetic-owner'))
        self.assertIsNone(self.floor.get_owner_note('assistant','synthetic-owner'))
        self.assertIsNone(self.floor.get_owner_note('mine','visitor'))
        other = FloorStores(lambda:DSN,connect=lambda _:self.connect(),floor='another-floor')
        self.assertIsNone(other.get_owner_note('mine','synthetic-owner'))

    def test_schema_refuses_off_record_note_before_any_capture(self):
        import psycopg2
        self.floor.add_note('mine','on-record',self.owner)
        with self.assertRaises(psycopg2.errors.CheckViolation),self.connect() as conn,conn.cursor() as cur:
            cur.execute("UPDATE guild.floor_messages SET record_mode='off_record' WHERE floor=%s",[self.floor.floor])
        self.assertEqual(self.floor.get_owner_note('mine','synthetic-owner')['record_mode'],'on_record')


if __name__ == '__main__':
    unittest.main()
