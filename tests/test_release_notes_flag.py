"""A release that changes code and not the authored notes is flagged."""
from scripts.ci.check_release_notes import NOTES, stale


def test_unchanged_notes_beside_other_files_are_flagged():
    message = stale(["minimoi_portal/guild_ui/api.py"], "{}", "{}")
    assert message and NOTES in message


def test_notes_changed_in_the_commit_are_not_flagged():
    assert stale([NOTES, "minimoi_portal/guild_ui/api.py"], "{new}", "{old}") is None


def test_a_commit_that_changes_nothing_else_is_not_flagged():
    assert stale([], "{}", "{}") is None


def test_different_bytes_are_not_called_unchanged():
    assert stale(["a.py"], "{new}", "{old}") is None


def test_a_missing_parent_copy_is_not_called_stale():
    assert stale(["a.py"], "{}", None) is None
