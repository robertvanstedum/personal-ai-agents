"""Run the existing UI contract suite with the domain bridge enabled."""
from test_board_api import *  # noqa: F401,F403  (same API acceptance contract)

@pytest.fixture(autouse=True)
def enable_domain_ui(request):
    if "board" not in request.fixturenames:
        return
    board = request.getfixturevalue("board")
    board.app.extensions["guild_ui_next"]["feature_environ"].update(
        MINIMOI_BOARD_ACTIONS="1", MINIMOI_BOARD_ENV="dev")
