"""
tests/test_portuguese_dev_launcher.py — domains/portuguese/dev_launcher.py
is the secret-free launchd entry point that reads DATABASE_URL from the
gitignored .env (Docker Compose form, host "postgres") and rewrites it to
"localhost" for the host-native dev process, without ever putting a
password in the plist or depending on the Keychain item that turned out
to be unreliable across process contexts.

These tests cover the pure functions directly (host rewrite, env-file
parsing, validation) and the module-level target-script selection, not
the final os.execve (which really does replace the process and can't be
observed from inside the same test process).
"""
import importlib.util
import sys
from pathlib import Path

import pytest

LAUNCHER_PATH = (
    Path(__file__).resolve().parents[1] / "domains" / "portuguese" / "dev_launcher.py"
)


def _load_launcher(monkeypatch, env_file: Path | None = None, argv: list[str] | None = None):
    if env_file is not None:
        monkeypatch.setenv("MINIMOI_ENV_FILE", str(env_file))
    monkeypatch.setattr(sys, "argv", ["dev_launcher.py", *(argv or [])])
    name = f"pt_dev_launcher_test_{id(monkeypatch)}"
    spec = importlib.util.spec_from_file_location(name, LAUNCHER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_rewrite_host_swaps_docker_hostname_for_localhost(monkeypatch):
    launcher = _load_launcher(monkeypatch)
    result = launcher._rewrite_host_to_native(
        "postgresql://minimoi:mypassword@postgres:5432/personal_agents"
    )
    assert result == "postgresql://minimoi:mypassword@localhost:5432/personal_agents"


def test_rewrite_host_is_idempotent_when_already_localhost(monkeypatch):
    launcher = _load_launcher(monkeypatch)
    dsn = "postgresql://minimoi:mypassword@localhost:5432/personal_agents"
    assert launcher._rewrite_host_to_native(dsn) == dsn


@pytest.mark.parametrize("password", [
    "p@ss:word!",           # '@' and ':' inside the password itself
    "sh$ell`quote'chars\"", # shell metacharacters
    "p%2Fslash",            # a properly percent-encoded '/' -- a literal,
                             # un-encoded '/' isn't valid DSN syntax in the
                             # first place (urlsplit would treat it as the
                             # start of the path, before this launcher ever
                             # sees it), so this is the realistic case.
])
def test_rewrite_host_preserves_special_characters_in_password_untouched(
    monkeypatch, password
):
    launcher = _load_launcher(monkeypatch)
    dsn = f"postgresql://minimoi:{password}@postgres:5432/personal_agents"
    result = launcher._rewrite_host_to_native(dsn)
    assert result == f"postgresql://minimoi:{password}@localhost:5432/personal_agents"
    # The password segment itself was never decomposed/re-encoded -- it
    # appears in the output byte-for-byte, not just "close enough".
    assert password in result


def test_rewrite_host_rejects_wrong_scheme(monkeypatch):
    launcher = _load_launcher(monkeypatch)
    with pytest.raises(SystemExit):
        launcher._rewrite_host_to_native("mysql://minimoi:pw@postgres:5432/personal_agents")


def test_rewrite_host_rejects_wrong_database(monkeypatch):
    launcher = _load_launcher(monkeypatch)
    with pytest.raises(SystemExit):
        launcher._rewrite_host_to_native("postgresql://minimoi:pw@postgres:5432/wrong_db")


def test_rewrite_host_rejects_unexpected_host(monkeypatch):
    launcher = _load_launcher(monkeypatch)
    with pytest.raises(SystemExit):
        launcher._rewrite_host_to_native(
            "postgresql://minimoi:pw@some-other-host:5432/personal_agents"
        )


def test_rewrite_host_rejects_missing_userinfo_separator(monkeypatch):
    launcher = _load_launcher(monkeypatch)
    with pytest.raises(SystemExit):
        launcher._rewrite_host_to_native("postgresql://postgres:5432/personal_agents")


def test_read_env_file_parses_quoted_and_unquoted_values(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# a comment\n"
        "\n"
        "DATABASE_URL=postgresql://minimoi:pw@postgres:5432/personal_agents\n"
        "QUOTED=\"has spaces\"\n"
        "SINGLE_QUOTED='also spaces'\n"
    )
    launcher = _load_launcher(monkeypatch, env_file=env_file)
    values = launcher._read_env_file(env_file)
    assert values["DATABASE_URL"] == "postgresql://minimoi:pw@postgres:5432/personal_agents"
    assert values["QUOTED"] == "has spaces"
    assert values["SINGLE_QUOTED"] == "also spaces"


def test_read_env_file_fails_clearly_when_file_missing(monkeypatch, tmp_path):
    launcher = _load_launcher(monkeypatch, env_file=tmp_path / "nonexistent.env")
    with pytest.raises(SystemExit):
        launcher._read_env_file(tmp_path / "nonexistent.env")


def test_default_target_script_is_html_server(monkeypatch):
    launcher = _load_launcher(monkeypatch, argv=[])
    assert launcher.APP_SCRIPT.name == "html_server.py"


def test_explicit_target_script_argument_selects_leitura_rss(monkeypatch):
    launcher = _load_launcher(monkeypatch, argv=["leitura_rss.py"])
    assert launcher.APP_SCRIPT.name == "leitura_rss.py"


def test_both_approved_target_scripts_actually_exist_on_disk(monkeypatch):
    """A launcher pointed at a script that doesn't exist would fail with
    a confusing error deep inside os.execve -- both approved targets must
    resolve to real files before that ever matters."""
    for target in ("html_server.py", "leitura_rss.py"):
        launcher = _load_launcher(monkeypatch, argv=[target])
        assert launcher.APP_SCRIPT.is_file(), f"{target} not found at {launcher.APP_SCRIPT}"
