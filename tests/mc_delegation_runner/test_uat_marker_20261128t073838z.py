from core.canonical_json import digest


def test_digest_order_independence():
    assert digest({"a": 1, "b": 2}) == digest({"b": 2, "a": 1})
