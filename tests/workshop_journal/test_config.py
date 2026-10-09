"""config.json is read like the journal: no links, private, bounded, strict, known sections only."""
from __future__ import annotations

import json
import os

import pytest

from workshop_journal.conftest import write_config
from core.workshop_journal import config, profiles, routes


def test_an_absent_config_is_empty_and_a_good_one_is_read(tmp_path):
    assert config.read(str(tmp_path)) == {} and config.read(str(tmp_path / "nowhere")) == {}
    write_config(tmp_path, json.dumps({"v": 1, "routes": {}}))
    assert config.read(str(tmp_path)) == {"v": 1, "routes": {}}


@pytest.mark.parametrize("text,reason", [
    ("{ not json", "config_unreadable"), ('{"v":1,"v":1}', "config_unreadable"), ('{"v":2}', "unknown_config_version"),
    ('{"tokens":{}}', "unknown_config_field"), ("[]", "unknown_config_field"), ('{"v":1,"routes":{"a":NaN}}', "config_unreadable"),
    ('{"pad":"' + "x" * (config.MAX_BYTES + 10) + '"}', "config_too_large"),
], ids=['not_json', 'duplicate_key', 'unknown_version', 'unknown_field_object', 'unknown_field_array', 'nan_value', 'too_large'])
def test_a_damaged_or_oversized_or_unknown_config_is_refused(tmp_path, text, reason):
    write_config(tmp_path, text)
    with pytest.raises(config.BadConfig) as caught:
        config.read(str(tmp_path))
    assert str(caught.value) == reason


def test_a_linked_group_readable_or_hard_linked_config_is_refused(tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    os.chmod(outside, 0o600)
    folder = tmp_path / "ws"
    folder.mkdir(mode=0o700)
    (folder / "config.json").symlink_to(outside)
    with pytest.raises(config.BadConfig):
        config.read(str(folder))
    (folder / "config.json").unlink()
    write_config(folder, "{}")
    os.chmod(folder / "config.json", 0o644)
    with pytest.raises(config.BadConfig):
        config.read(str(folder))
    os.chmod(folder / "config.json", 0o600)
    os.link(folder / "config.json", tmp_path / "second-name.json")
    with pytest.raises(config.BadConfig):
        config.read(str(folder))


def test_routes_and_profiles_both_refuse_an_unsafe_config(tmp_path):
    write_config(tmp_path, "{}")
    os.chmod(tmp_path / "config.json", 0o666)
    with pytest.raises(routes.BadRoutes):
        routes.load(str(tmp_path))
    with pytest.raises(profiles.BadProfiles):
        profiles.load(str(tmp_path))
