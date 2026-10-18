"""Codex's review of the composed sync package (17 Oct 2026): a corrupt batch followed by a valid one.

The integrity handler reused the name of the source-key map for the rejected batch's versions (a list), so the next valid batch crashed
(``AttributeError: 'list' object has no attribute 'get'``) and neither batch progressed. Codex's repro, kept as the regression test, plus
the acknowledgements: the corrupt batch is acknowledged rejected and the valid one is stored."""
from tests.vault.test_vault_apply import w, apply, changes_on_mac, send, V, ack, statuses, inventory, mac_digest   # noqa: F401


def test_corrupt_batch_does_not_block_following_valid_batch(w):
    apply(w)
    changes_on_mac(w)
    bad = send(w, [w.ids['a']])
    cipher = w.destdir / V.BATCH_PREFIX / bad / V.BATCH_OBJECT
    data = bytearray(cipher.read_bytes())
    data[len(data)//2] ^= 1
    cipher.write_bytes(bytes(data))
    good = send(w, [w.ids['d']])
    result = apply(w)
    assert result['rejected_batches'] == 1
    assert result['stored'] == 1
    assert statuses(ack(w, bad)) == {w.ids['a']: f"{V.REJECTED}:ciphertext_mismatch"}
    assert statuses(ack(w, good)) == {w.ids['d']: V.STORED}
