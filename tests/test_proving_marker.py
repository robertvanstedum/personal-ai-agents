import pytest
from minimoi_portal.proving_marker import marker

def test_disabled_has_no_marker_even_on_production():
    assert marker("https://minimoi.ai", {}) is None

@pytest.mark.parametrize("base", ["https://minimoi.ai", "http://127.0.0.1:5001", "https://dev.minimoi.ai.evil.test"])
def test_production_and_unapproved_origins_rejected(base):
    with pytest.raises(RuntimeError):
        marker(base, {"MINIMOI_PROVING_ENABLED":"1", "MINIMOI_PROVING_INSTANCE_ID":"a"*32})

@pytest.mark.parametrize("value", ["", "a"*31, "A"*32, "x"*32])
def test_bad_instance_rejected(value):
    with pytest.raises(RuntimeError):
        marker("https://dev.minimoi.ai", {"MINIMOI_PROVING_ENABLED":"1", "MINIMOI_PROVING_INSTANCE_ID":value})

def test_bound_dev_instance():
    assert marker("http://127.0.0.1:15001", {"MINIMOI_PROVING_ENABLED":"1", "MINIMOI_PROVING_INSTANCE_ID":"a"*32}) == {"environment":"dev", "instance_id":"a"*32}
