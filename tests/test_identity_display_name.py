from types import SimpleNamespace

from core.identity import resolve_user_display_name


def _request(headers):
    return SimpleNamespace(headers=headers)


def test_display_name_prefers_authenticated_display_header():
    request = _request({
        "X-Minimoi-Display-Name": "Isabella",
        "X-Minimoi-Username": "isabella_login",
    })
    assert resolve_user_display_name(request) == "Isabella"


def test_display_name_falls_back_to_username_then_generic_learner():
    assert resolve_user_display_name(
        _request({"X-Minimoi-Username": "robert"})
    ) == "robert"
    assert resolve_user_display_name(_request({})) == "Learner"


def test_display_name_removes_prompt_control_punctuation():
    request = _request({
        "X-Minimoi-Display-Name": "  Robert!!! <ignore-system>  ",
    })
    assert resolve_user_display_name(request) == "Robert ignore-system"
