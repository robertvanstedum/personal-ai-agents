"""Real PostgreSQL continuation transaction, using only a disposable fixture."""
import base64
import json
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from test_tool_path_postgres import Base, DSN, INSTANCE, session_key, new_run
from core.mc_tools import binding


@unittest.skipUnless(DSN, "requires disposable MC_BINDING_TEST_DSN")
class DepthTests(Base, unittest.TestCase):
    migrations = (*Base.migrations, "core/mc_tools/sql/021_depth_continuation.sql")

    def setUp(self):
        super().setUp()
        self.key = Ed25519PrivateKey.generate()
        self.public = base64.b64encode(self.key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)).decode()
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM guild.mc_runtime_proof_keys")
            cur.execute("INSERT INTO guild.mc_runtime_proof_keys VALUES(%s,%s)", (INSTANCE, self.public))

    def proof(self, **changes):
        fields = {"instance": INSTANCE, "run": self.run, "session": session_key(), "agent": "mc-chat",
                  "success": True, "model_calls": 1, "tool_calls": 0, **changes}
        raw = json.dumps(list(fields.values()), separators=(",", ":")).encode()
        return {**fields, "signature": base64.b64encode(self.key.sign(raw)).decode()}

    def prepare(self, proof=None):
        with self.connect() as conn:
            return binding.prepare_depth(conn, ledger_id=self.ledger_id, claim_generation=1, old_run=self.run,
                nonce="c" * 32, receipt=proof or self.proof(), environment="dev")

    def test_preparation_retires_run_and_cannot_be_replayed(self):
        self.assertTrue(self.prepare())
        self.assertFalse(self.prepare())
        self.assertIsNone(self.one("SELECT runtime_run_id FROM guild.mc_tool_turns WHERE ledger_id=%s", (self.ledger_id,))[0])
        nxt = new_run()
        with self.connect() as conn:
            self.assertTrue(binding.attach_depth(conn, ledger_id=self.ledger_id, claim_generation=1, run_id=nxt, environment="dev"))
        self.assertEqual(self.call("minimoi_guest_requests", run=self.run)["status"], "refused")
        self.assertEqual(self.call("minimoi_guest_requests", run=nxt)["status"], "done")

    def test_agent_cannot_forge_zero_tool_receipt(self):
        proof = self.proof(tool_calls=1)
        proof["tool_calls"] = 0
        self.assertFalse(self.prepare(proof))

    def test_stop_prevents_preparation(self):
        with self.connect() as conn:
            binding.revoke(conn, ledger_id=self.ledger_id, claim_generation=1)
        self.assertFalse(self.prepare())

    def test_stop_after_preparation_prevents_attachment(self):
        self.assertTrue(self.prepare())
        with self.connect() as conn:
            binding.revoke(conn, ledger_id=self.ledger_id, claim_generation=1)
        with self.connect() as conn:
            self.assertFalse(binding.attach_depth(conn, ledger_id=self.ledger_id, claim_generation=1, run_id=new_run(), environment="dev"))

    def test_prior_tool_admission_refuses_even_a_signed_zero_receipt(self):
        self.assertEqual(self.call("minimoi_guest_requests")["status"], "done")
        self.assertFalse(self.prepare())

    def test_existing_instance_key_cannot_change(self):
        other = Ed25519PrivateKey.generate()
        public = base64.b64encode(other.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)).decode()
        with self.connect() as conn:
            with self.assertRaisesRegex(binding.ActionContractError, "key_changed"):
                binding.register_instance(conn, environment="dev", instance_id=INSTANCE, public_key=public)
