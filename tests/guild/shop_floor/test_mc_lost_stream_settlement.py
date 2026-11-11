"""A turn whose stream was lost (portal restart: lease expired, 'unknown') settles as 'failed' only on the runtime's
signed end receipt for its exact run (Dev, 11 Nov 2026: such a turn kept its helper result waiting forever)."""
import base64
import contextlib
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from minimoi_portal.guild_ui.mc import turn_ledger as tl

RUN = "chatcmpl_11111111-1111-4111-8111-111111111111"
INST, AGENT, USER = "a" * 32, "mc-chat", "guild-mc:test"
KEY = Ed25519PrivateKey.generate()
PUB = base64.b64encode(KEY.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)).decode()


def receipt(**over):
    r = {"instance": INST, "run": RUN, "session": f"agent:{AGENT}:openai-user:{USER}", "agent": AGENT,
         "success": True, "model_calls": 3, "tool_calls": 2}
    r.update(over)
    fields = ("instance", "run", "session", "agent", "success", "model_calls", "tool_calls")
    message = json.dumps([r[k] for k in fields], ensure_ascii=False, separators=(",", ":")).encode()
    return {**r, "signature": base64.b64encode(KEY.sign(message)).decode()}


def proved(r, **over):
    args = {**dict(run=RUN, instance=INST, agent=AGENT, user=USER, public_key=PUB), **over}
    return tl.run_end_proved(r, **args)


def test_only_this_runs_signed_receipt_proves_the_end():
    assert proved(receipt())
    assert proved(receipt(success=False))                              # ended badly is still ended
    assert not proved(receipt(), run="chatcmpl_22222222-2222-4222-8222-222222222222")
    assert not proved(receipt(), user="guild-mc:someone-else")
    assert not proved(receipt(), instance="b" * 32)
    forged = dict(receipt(), tool_calls=0)                             # changed after signing
    assert not proved(forged)
    other = Ed25519PrivateKey.generate()
    assert not proved(receipt(), public_key=base64.b64encode(other.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)).decode())
    assert not proved(None) and not proved({"run": RUN})


class Cur:
    def __init__(self, row, rowcount=1):
        self.row, self.rowcount, self.sql = row, rowcount, []
    def execute(self, sql, args=()):
        self.sql.append((sql, args))
    def fetchone(self):
        return self.row


def ledger(cur):
    led = tl.TurnLedger.__new__(tl.TurnLedger)
    led._tx = contextlib.contextmanager(lambda principal=None: (yield cur))
    return led


def test_a_lost_turn_settles_as_failed_on_proof_and_its_pause_says_so():
    cur = Cur((7, RUN, INST, AGENT, USER, PUB))
    assert ledger(cur).settle_unknown(principal="robert", conversation_id="c-1", note_request_id="n-1", run_end=lambda run: receipt())
    updates = [s for s, _ in cur.sql if s.startswith("UPDATE")]
    assert any("state = 'failed'" in s for s in updates) and any("mc_turn_barrier SET reason = 'failed'" in s for s in updates)


def test_without_proof_the_turn_stays_unknown():
    for row, fetched in (((7, RUN, INST, AGENT, USER, PUB), None),            # no receipt from the relay
                         ((7, RUN, INST, AGENT, USER, PUB), receipt(run="chatcmpl_22222222-2222-4222-8222-222222222222")),
                         ((7, None, INST, AGENT, USER, PUB), receipt()),       # no run was ever bound
                         ((7, RUN, INST, AGENT, USER, None), receipt()),       # no registered key for the instance
                         (None, receipt())):                                   # not an unknown, lease-expired turn
        cur = Cur(row)
        assert not ledger(cur).settle_unknown(principal="robert", conversation_id="c-1", note_request_id="n-1", run_end=lambda run: fetched)
        assert not [s for s, _ in cur.sql if s.startswith("UPDATE")]

    def boom(run):
        raise TimeoutError
    cur = Cur((7, RUN, INST, AGENT, USER, PUB))
    assert not ledger(cur).settle_unknown(principal="robert", conversation_id="c-1", note_request_id="n-1", run_end=boom)
