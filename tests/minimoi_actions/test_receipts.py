from dataclasses import replace
import unittest
from uuid import uuid4

from core.minimoi_actions.context import ActionContractError
from core.minimoi_actions.receipts import EffectIntent, EffectLease
from core.minimoi_actions.requests import ToolRequest


class IntentTests(unittest.TestCase):
    def test_effect_digest_covers_content_version_authority_and_target(self):
        intent = EffectIntent(str(uuid4()), ToolRequest('guest.reject', 1, b'{"b":2,"a":1}'))
        self.assertEqual(intent.digest, replace(intent,
            request=ToolRequest('guest.reject', 1, b'{"a":1,"b":2}')).digest)
        self.assertNotEqual(intent.digest, replace(intent, grant_id='different').digest)
        self.assertNotEqual(intent.digest, replace(intent, expected_target_version='2').digest)
        self.assertNotEqual(intent.digest, replace(intent, proposal_id=str(uuid4())).digest)
        self.assertNotEqual(intent.digest, replace(intent,
            request=ToolRequest('guest.reject', 2, b'{"a":1,"b":2}')).digest)
        self.assertNotEqual(intent.digest, replace(intent,
            request=ToolRequest('guest.reject', 1, b'{"a":2,"b":2}')).digest)
        self.assertNotEqual(intent.digest, replace(intent, reservation_ids=(str(uuid4()),)).digest)

    def test_strict_ids_and_no_secret_repr(self):
        request = ToolRequest('guest.reject', 1, b'{}')
        for value in ('not-uuid', True, str(uuid4()).upper()):
            with self.assertRaises(ActionContractError):
                EffectIntent(value, request)
        with self.assertRaises(ActionContractError):
            EffectIntent(str(uuid4()), request, reservation_ids=[])
        with self.assertRaises(ActionContractError):
            EffectIntent(str(uuid4()), request, grant_id='bad\nvalue')
        lease = EffectLease('owner', 'dev', str(uuid4()), 1, 'worker', 'a'*43, 'execute')
        self.assertNotIn(lease.token, repr(lease))
        with self.assertRaises(ActionContractError):
            replace(lease, generation=True)


if __name__ == '__main__':
    unittest.main()
