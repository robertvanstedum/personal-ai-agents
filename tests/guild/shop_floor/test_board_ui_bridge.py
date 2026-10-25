"""Run the existing UI contract suite with the domain bridge enabled."""
from test_board_api import *  # noqa: F401,F403  (same API acceptance contract)

@pytest.fixture(autouse=True)
def enable_domain_ui(request, monkeypatch):
    if "board" not in request.fixturenames:
        return
    board = request.getfixturevalue("board")
    # The mount's environ is the mapping the test portal was built from; set and undo, never leak into later tests.
    environ = board.app.extensions["guild_ui_next"]["feature_environ"]
    monkeypatch.setitem(environ, "MINIMOI_BOARD_ACTIONS", "1")
    monkeypatch.setitem(environ, "MINIMOI_BOARD_ENV", "dev")
