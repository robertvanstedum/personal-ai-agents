import unittest
from unittest.mock import patch
import proving_model_guard as guard

class Credentials(unittest.TestCase):
 def test_generated_gateway_key_is_accepted(self):
  with patch.dict(guard.os.environ,{'PROVING_PROXY_TOKEN':'p'*32,'UPSTREAM_KEY':'sk-'+'u'*22},clear=True):
   self.assertEqual(len(guard.credentials()[1]),25)
 def test_short_or_missing_credentials_refused(self):
  for proxy,upstream in [('', ''),('p'*31,'u'*40),('p'*32,'u'*24)]:
   with self.subTest(proxy_length=len(proxy),upstream_length=len(upstream)):
    with patch.dict(guard.os.environ,{'PROVING_PROXY_TOKEN':proxy,'UPSTREAM_KEY':upstream},clear=True):
     with self.assertRaises(ValueError):guard.credentials()
 def test_redirect_not_followed(self):
  self.assertIsNone(guard.NoRedirect().redirect_request(None,None,307,'',{},'https://example.invalid'))
if __name__=='__main__':unittest.main()
