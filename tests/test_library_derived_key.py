"""Purpose capability only; synthetic constants, no secret provisioning/network."""
import hashlib,hmac
import pytest
from core import library_intake_transport as protocol

BASE={'MINIMOI_LIBRARY_INTAKE':'1','MINIMOI_LIBRARY_INTAKE_ENV':'dev','MINIMOI_LIBRARY_INTAKE_OWNER':'robert'}
MASTER='synthetic-test-master-0123456789abcdef0123456789'
DERIVED=hmac.digest(MASTER.encode(),b'minimoi.library-intake.v1','sha256').hex()

def test_existing_master_and_curator_derived_only_are_same_capability():
    portal=protocol.configured({**BASE,'PORTAL_SECRET_KEY':MASTER})
    curator=protocol.configured({**BASE,'MINIMOI_LIBRARY_INTAKE_DERIVED_KEY':DERIVED})
    assert portal.key==curator.key==bytes.fromhex(DERIVED)
    assert MASTER not in repr(portal)and DERIVED not in repr(curator)
    value=protocol.payload(portal,'c-synthetic','n-synthetic','https://example.com/','Owner reason')
    raw=protocol.packed(value);headers=protocol.headers(portal,raw,now=100,nonce='a'*32)
    assert protocol.verify(curator,raw,headers,now=100)[0]==value
    receipt=b'{"status":"saved"}'
    assert protocol.response_signature(portal,'a'*32,receipt)==protocol.response_signature(curator,'a'*32,receipt)


def test_equal_both_sources_are_admitted_but_disagreement_refuses():
    expected=protocol.configured({**BASE,'PORTAL_SECRET_KEY':MASTER,'MINIMOI_LIBRARY_INTAKE_DERIVED_KEY':DERIVED})
    assert expected.key==bytes.fromhex(DERIVED)
    with pytest.raises(ValueError,match='sources_disagree'):
        protocol.configured({**BASE,'PORTAL_SECRET_KEY':MASTER,'MINIMOI_LIBRARY_INTAKE_DERIVED_KEY':'0'*64})

@pytest.mark.parametrize('value',['','g'*64,'a'*63,'a'*65,'AA'*32,123])
def test_malformed_derived_never_silently_falls_back_to_master(value):
    with pytest.raises(ValueError,match='derived_key_invalid'):
        protocol.configured({**BASE,'PORTAL_SECRET_KEY':MASTER,'MINIMOI_LIBRARY_INTAKE_DERIVED_KEY':value})


def test_missing_sources_and_weak_present_master_refuse():
    with pytest.raises(ValueError,match='shared_secret_required'):protocol.configured(BASE)
    with pytest.raises(ValueError,match='shared_secret_required'):
        protocol.configured({**BASE,'PORTAL_SECRET_KEY':'weak','MINIMOI_LIBRARY_INTAKE_DERIVED_KEY':DERIVED})


def test_default_off_does_not_parse_or_use_credential_sources():
    assert protocol.configured({'MINIMOI_LIBRARY_INTAKE_DERIVED_KEY':'bad','PORTAL_SECRET_KEY':'bad'})is None


def test_blank_curator_master_uses_derived_key_and_authenticates_portal():
    portal = protocol.configured({**BASE, 'PORTAL_SECRET_KEY': MASTER})
    curator = protocol.configured({**BASE, 'PORTAL_SECRET_KEY': '', 'MINIMOI_LIBRARY_INTAKE_DERIVED_KEY': DERIVED})
    value = protocol.payload(portal, 'conversation', 'note', 'https://example.com/article', 'Saved reason')
    raw = protocol.packed(value)
    signed = protocol.headers(portal, raw, now=100, nonce='b' * 32)
    assert protocol.verify(curator, raw, signed, now=100)[0] == value
    with pytest.raises(ValueError, match='shared_secret_required'):
        protocol.configured({**BASE, 'PORTAL_SECRET_KEY': ''})
    assert protocol.configured({'MINIMOI_LIBRARY_INTAKE': '0', 'PORTAL_SECRET_KEY': ''}) is None
