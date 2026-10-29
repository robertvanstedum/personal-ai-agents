"""The launch contract on the runtime's path (spec v0.5 R2.2-c G5, R6.7-a to R6.7-l; LCH-1 to LCH-6; T19a to T19e):
the one policy file and its reader, pinned binaries, checks before start, the route on the item, an API route's key by
reference to one child, and the launch record. Plus S1-D's carried item N-F2.

T19 runs real children: ``fakecli`` (tests/mc_delegation_runner/fakecli.py), a fakework/1 build installed like a vendor
CLI, started by a version 2 runtime against the in-memory authority of test_host_runtime.py. The runtime's parent
environment holds synthetic test values, made here, under the seven planted names (§9.3). A provider tripwire is first
on PATH. At the end of each scenario every file the run wrote, and every answer it returned, is searched for each
planted value (LCH-6). No provider, no network, no Keychain read."""
import hashlib
import json
import os
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

import fakecli
from core.mc_delegation_runner import cli, commands, cost, host as host_mod, launch, launch_env as L
from test_host_runtime import FakeAuthority, _reap, git, make_rt, mirror, program, run_pass  # noqa: F401  (fixtures)

PLANTED = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY", "GH_TOKEN", "AWS_SECRET_ACCESS_KEY",
           "TELEGRAM_BOT_TOKEN", "SSH_AUTH_SOCK")
TRIPWIRE = ("claude", "codex", "grok", "openclaw", "litellm")

# PR #436's lists, frozen as they were in launch_env.py before it read the policy file (978e9341): the file must give
# exactly these (no behaviour change for today's lists).
PR436_BASE = ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "TMPDIR", "USER", "SHELL")
PR436_EXTRAS = {"MINIMOI_AGENT_REQUESTS_DIR", "DISABLE_AUTOUPDATER", "LANG", "GIT_TERMINAL_PROMPT"}
PR436_DENY = (
    r"^GH_", r"^GITHUB_", r"^AWS_", r"^MINIMOI_", r"_API_KEY$", r"_TOKEN$", r"^DATABASE_URL", r"^SSH_AUTH_SOCK$",
    r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?)$",
    r"API_KEY", r"AUTH_TOKEN", r"OAUTH", r"FILE_DESCRIPTOR", r"^ANTHROPIC_", r"^CLAUDE_CODE_USE_",
    r"^OPENAI_", r"^XAI_", r"TOKEN_FILE$",
    r"_BASE_URL$", r"_ENDPOINT$", r"^CLOUD_ML_", r"_DSN$", r"^REDIS_URL",
    r"PROXY$", r"^NODE_EXTRA_CA_CERTS$", r"^NODE_TLS_", r"^SSL_CERT_", r"^REQUESTS_CA_BUNDLE$", r"^CURL_CA_BUNDLE$",
    r"^CLAUDE_CONFIG_DIR$", r"^CODEX_HOME$", r"^GROK_HOME$", r"^HOME$",
    r"^NODE_OPTIONS$", r"^PYTHON", r"^DYLD_", r"^LD_", r"^BASH_ENV$", r"^ENV$", r"^ZDOTDIR$",
    r"^GIT_SSH", r"^GIT_CONFIG",
)


# ── the policy file and its reader (R6.7-d) ───────────────────────────────────────────────────────────────────────────
def test_the_policy_file_gives_exactly_pr_436s_lists_and_its_digest_is_the_files():
    assert L.BASE_ALLOW == PR436_BASE and set(L.EXTRA_ALLOW) == PR436_EXTRAS and L.DENY_PATTERNS == PR436_DENY
    assert L.DENY_OVERRIDES == {"MINIMOI_AGENT_REQUESTS_DIR"}
    raw = L.POLICY_PATH.read_bytes()
    assert L.POLICY_DIGEST == "sha256:" + hashlib.sha256(raw).hexdigest() and L.POLICY_VERSION == 1
    assert L.POLICY_PATH.parent == Path(commands.__file__).resolve().parent     # in the runner's bundle (installer)
    # The same environment as before for the runner's child: base names copied, the one extra, nothing else.
    parent = {**{n: "x" for n in PLANTED}, "PATH": "/b", "HOME": "/h", "USER": "u", "SHELL": "/bin/zsh", "TMPDIR": "/t",
              "LOGNAME": "u", "XPC_SERVICE_NAME": "0", "__CF_USER_TEXT_ENCODING": "0x0"}
    assert L.child_env({"GIT_TERMINAL_PROMPT": "0"}, environ=parent) == {
        "PATH": "/b", "HOME": "/h", "USER": "u", "SHELL": "/bin/zsh", "TMPDIR": "/t", "GIT_TERMINAL_PROMPT": "0"}


def test_the_two_profiles_unattended_has_no_credential_and_no_ssh_agent_supervised_adds_roberts_names():
    parent = {**{n: "x" for n in PLANTED}, "PATH": "/b", "HOME": "/h", "LOGNAME": "r", "TERM_PROGRAM": "Apple_Terminal",
              "TERM_PROGRAM_VERSION": "1", "COLORTERM": "truecolor", "LC_CTYPE": "UTF-8", "LC_TIME": "C",
              "__CF_USER_TEXT_ENCODING": "0x1", "EDITOR": "vi", "VISUAL": "vi", "BRAVE_API_KEY": "x",
              "OPENCLAW_GATEWAY_TOKEN": "x", "NODE_OPTIONS": "--x"}
    unattended = L.child_env(environ=parent)
    supervised = L.child_env(environ=parent, profile="supervised")
    assert set(unattended) == {"PATH", "HOME"}
    assert set(supervised) == {"PATH", "HOME", "SSH_AUTH_SOCK", "LOGNAME", "TERM_PROGRAM", "TERM_PROGRAM_VERSION",
                               "COLORTERM", "LC_CTYPE", "LC_TIME", "__CF_USER_TEXT_ENCODING", "EDITOR", "VISUAL"}
    for env in (unattended, supervised):                            # still no provider key and no token, in either
        assert not [n for n in env if L.credential_shaped(n)]
    with pytest.raises(L.EnvRefused):
        L.child_env({"SSH_AUTH_SOCK": "/s"}, environ={}, profile="supervised")   # inherited only, never set by a caller
    with pytest.raises(L.EnvRefused, match="launch_profile_unknown"):
        L.child_env(environ={}, profile="interactive")
    assert L.POLICY["doc"]["profiles"]["unattended"]["used_by"] == ["runtime", "connection", "probe"]
    assert L.POLICY["doc"]["profiles"]["supervised"]["used_by"] == ["fallback"]


def _policy(**change):
    doc = json.loads(L.POLICY_PATH.read_text())
    for path, value in change.items():
        node = doc
        keys = path.split("__")
        for k in keys[:-1]:
            node = node[int(k)] if isinstance(node, list) else node[k]
        if value is _DELETE:
            del node[keys[-1]]
        else:
            node[keys[-1] if not isinstance(node, list) else int(keys[-1])] = value
    return json.dumps(doc).encode()


_DELETE = object()


@pytest.mark.parametrize("raw, code", [
    (b"{", "launch_policy_unreadable"),
    (b'{"schema": 1, "schema": 2}', "launch_policy_duplicate_key"),
    (_policy(extra_key=1), "launch_policy_invalid"),
    (_policy(version="1"), "launch_policy_schema_invalid"),
    # an unattended profile with the ssh agent, or a credential name, is refused at load
    (_policy(profiles__unattended__inherit=[{"name": "SSH_AUTH_SOCK", "reason": "added by mistake"}]),
     "launch_policy_inherited_denied:unattended:SSH_AUTH_SOCK"),
    (_policy(profiles__unattended__inherit=[{"name": "GH_TOKEN", "reason": "added by mistake"}]),
     "launch_policy_inherited_denied:unattended:GH_TOKEN"),
    (_policy(extras=[{"name": "OPENAI_API_KEY", "reason": "added by mistake"}]), "launch_policy_extra_denied"),
    (_policy(deny_overrides=[{"name": "GH_TOKEN", "way": "extra", "profiles": ["unattended"], "reason": "a mistake!"}]),
     "launch_policy_override_credential:GH_TOKEN"),
    (_policy(deny_overrides=[{"name": "LOGNAME", "way": "extra", "profiles": ["unattended"], "reason": "vacuous one"}]),
     "launch_policy_override_vacuous:LOGNAME"),
    (_policy(deny_overrides=[{"name": "SSH_AUTH_SOCK", "way": "inherited", "profiles": ["unattended", "supervised"],
                              "reason": "added by mistake"},
                             {"name": "HOME", "way": "inherited", "profiles": ["unattended", "supervised"],
                              "reason": "the base name"},
                             {"name": "MINIMOI_AGENT_REQUESTS_DIR", "way": "extra",
                              "profiles": ["unattended", "supervised"], "reason": "the ledger folder"}]),
     "launch_policy_unattended_ssh_agent"),
    # a route's key name must be one every other child loses; a test route is one by its name, never by a flag
    (_policy(**{"routes__fakework.api__key_name": "FAKEWORK_ROUTE"}), "launch_policy_route_invalid:fakework.api"),
    (_policy(**{"routes__anthropic.api__test": True}), "launch_policy_route_invalid:anthropic.api"),
    (_policy(**{"routes__fakework.plan__test": _DELETE}), "launch_policy_route_invalid:fakework.plan"),
    (_policy(**{"tools__fakecli__config__0__file": "../.fakecli/settings.json"}), "launch_policy_tool_invalid:fakecli"),
])
def test_a_damaged_or_inconsistent_policy_is_refused_at_load_with_a_fixed_code(raw, code):
    with pytest.raises(L.PolicyRefused) as caught:
        L.parse(raw)
    assert str(caught.value).startswith(code)


# ── the contract's steps, one at a time ───────────────────────────────────────────────────────────────────────────────
def _exe(path, text="#!/bin/sh\nexit 0\n"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)
    return str(path), hashlib.sha256(text.encode()).hexdigest()


def test_a_binary_starts_only_when_it_is_exactly_its_pin(tmp_path):
    exe, sha = _exe(tmp_path / "bin" / "tool")
    assert launch.verify_binary(exe, sha) == "sha256:" + sha == launch.verify_binary(exe, "sha256:" + sha)
    for pinned, path, reason in ((None, exe, "no_pin"), ("abc", exe, "no_pin"), ("0" * 64, exe, "digest_mismatch"),
                                 (sha, str(tmp_path / "missing"), "binary_missing"), (sha, "tool", "binary_missing")):
        with pytest.raises(launch.LaunchRefused) as caught:
            launch.verify_binary(path, pinned)
        assert caught.value.code == "binary_unpinned" and caught.value.detail["reason"] == reason
    link = tmp_path / "bin" / "link"
    link.symlink_to(exe)
    with pytest.raises(launch.LaunchRefused, match="binary_unreadable"):        # a link could move between check and exec
        launch.verify_binary(str(link), sha)
    # Version 1 keeps its bare names (production unchanged); version 2 starts the pinned path.
    assert commands.argv("claude-code", "research", "b", tmp_path)[0] == "claude"
    assert commands.argv("codex", "research", "b", tmp_path)[0] == "codex"
    assert commands.argv("claude-code", "build", "b", tmp_path, binary=exe)[0] == exe
    assert commands.argv("codex", "build", "b", tmp_path, binary=exe)[:2] == [exe, "exec"]
    assert commands.argv("fakework", "build", "fakework/1", tmp_path, binary=exe) == [exe, "--program", "fakework/1"]


def _write(path, doc):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc))


SECRET = "value-never-printed-" + "7" * 16


@pytest.mark.parametrize("rel, doc, setting", [
    (".claude/settings.json", {"apiKeyHelper": "/bin/echo " + SECRET}, "apiKeyHelper"),
    (".claude/settings.json", {"env": {"ANTHROPIC_API_KEY": SECRET}}, "env.ANTHROPIC_API_KEY"),
    (".claude/settings.json", {"env": {"ANTHROPIC_AUTH_TOKEN": SECRET}}, "env.ANTHROPIC_AUTH_TOKEN"),
    (".claude/settings.json", {"env": {"CLAUDE_CODE_USE_BEDROCK": "1"}}, "env.CLAUDE_CODE_USE_BEDROCK"),
    (".claude/settings.json", {"awsCredentialExport": "/bin/x " + SECRET}, "awsCredentialExport"),
    (".claude/settings.local.json", {"apiKeyHelper": SECRET}, "apiKeyHelper"),
    (".claude.json", {"oauthAccount": {"accountUuid": "u"}, "primaryApiKey": SECRET}, "primaryApiKey"),
])
def test_claude_code_a_key_setting_in_user_configuration_refuses_naming_the_setting_never_its_value(tmp_path, rel, doc,
                                                                                                    setting):
    home = tmp_path / "home"
    _write(home / ".claude.json", {"oauthAccount": {"accountUuid": "u"}})
    _write(home / rel, doc)
    with pytest.raises(launch.LaunchRefused) as caught:
        launch.check_tool("claude-code", route_kind="subscription", home=str(home))
    assert caught.value.code == "key_config_present" and caught.value.detail["setting"] == setting
    assert caught.value.detail["file"].startswith("{home}/") and SECRET not in str(caught.value)


def test_claude_code_sign_in_project_configuration_and_the_live_macs_shape(tmp_path):
    home, project = tmp_path / "home", tmp_path / "project"
    with pytest.raises(launch.LaunchRefused) as caught:
        launch.check_tool("claude-code", route_kind="subscription", home=str(home))
    assert caught.value.code == "signin_missing" and caught.value.detail["login"] == "claude /login"
    # Not a subscription route: the sign-in is not required (an API or test route), the key checks still are.
    assert launch.check_tool("claude-code", route_kind="api", home=str(home))["signin"] == "not_required"
    # The live Mac's configuration, by its key names only (read 29 Oct, values never read): it passes.
    _write(home / ".claude.json", {"oauthAccount": {k: "x" for k in ("accountUuid", "emailAddress", "billingType")},
                                   "customApiKeyResponses": {"approved": ["x"], "rejected": []},
                                   "claudeCodeFirstTokenDate": "x", "optionAsMetaKeyInstalled": True, "projects": {}})
    _write(home / ".claude" / "settings.json", {"agentPushNotifEnabled": True, "cleanupPeriodDays": 30,
                                                "inputNeededNotifEnabled": True, "theme": "dark"})
    got = launch.check_tool("claude-code", route_kind="subscription", home=str(home), project=str(project))
    assert got == {"tool": "claude-code", "signin": "present", "user_config": "checked", "project_config": "checked"}
    _write(project / ".claude" / "settings.json", {"env": {"OPENAI_API_KEY": SECRET}})
    with pytest.raises(launch.LaunchRefused) as caught:
        launch.check_tool("claude-code", route_kind="subscription", home=str(home), project=str(project))
    assert caught.value.detail == {"tool": "claude-code", "file": "{project}/.claude/settings.json",
                                   "setting": "env.OPENAI_API_KEY"}
    _write(project / ".claude" / "settings.json", "{not json")
    with pytest.raises(launch.LaunchRefused, match="key_config_unreadable"):
        launch.check_tool("claude-code", route_kind="subscription", home=str(home), project=str(project))


ROBERTS_CODEX_TOML = """
notify = ["x"]
model = "x"
model_reasoning_effort = "x"
service_tier = "x"
[features]
js_repl = true
[mcp_servers.openaiDeveloperDocs]
url = "x"
[mcp_servers.node_repl]
command = "x"
args = ["x"]
env_vars = ["x"]
startup_timeout_sec = 1
[mcp_servers.node_repl.env]
CODEX_HOME = "x"
CODEX_CLI_PATH = "x"
BROWSER_USE_AVAILABLE_BACKENDS = "x"
[mcp_servers.computer-use]
command = "x"
[projects."/Users/someone/repo"]
trust_level = "trusted"
[shell_environment_policy.set]
NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S = "x"
[desktop]
codeFontSize = 1
[plugins."browser@openai-bundled"]
enabled = true
[marketplaces.openai-bundled]
source = "x"
"""


@pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib (Python 3.11+); 3.9 refuses a TOML file instead")
def test_codex_chatgpt_sign_in_no_stored_key_and_no_key_setting_and_the_live_macs_shape(tmp_path):
    home = tmp_path / "home"
    _write(home / ".codex" / "auth.json", {"auth_mode": "apikey", "OPENAI_API_KEY": None})
    with pytest.raises(launch.LaunchRefused, match="signin_missing"):
        launch.check_tool("codex", route_kind="subscription", home=str(home))
    # The live Mac's auth.json and config.toml, by key names only: they pass.
    _write(home / ".codex" / "auth.json", {"OPENAI_API_KEY": None, "auth_mode": "chatgpt", "last_refresh": "x",
                                           "tokens": {"id_token": "x"}})
    _write(home / ".codex" / "config.toml", ROBERTS_CODEX_TOML)
    assert launch.check_tool("codex", route_kind="subscription", home=str(home))["signin"] == "present"
    for toml, setting in (('[model_providers.p]\nenv_key = "OPENAI_API_KEY"\n', "model_providers.p.env_key"),
                          ('model_provider = "' + SECRET + '"\n', "model_provider"),
                          ('preferred_auth_method = "apikey"\n', "preferred_auth_method"),
                          ('[shell_environment_policy.set]\nOPENAI_API_KEY = "' + SECRET + '"\n',
                           "shell_environment_policy.set.OPENAI_API_KEY")):
        _write(home / ".codex" / "config.toml", toml)
        with pytest.raises(launch.LaunchRefused) as caught:
            launch.check_tool("codex", route_kind="subscription", home=str(home))
        assert caught.value.code == "key_config_present" and caught.value.detail["setting"] == setting
        assert SECRET not in str(caught.value)
        # The runner's codex starts with --ignore-user-config: the CLI does not read that file, so it is not checked.
        assert launch.check_tool("codex", route_kind="subscription", home=str(home),
                                 argv=commands.argv("codex", "build", "b", tmp_path))["user_config"].startswith("ignored")
    _write(home / ".codex" / "auth.json", {"auth_mode": "chatgpt", "OPENAI_API_KEY": SECRET})     # a stored key
    with pytest.raises(launch.LaunchRefused) as caught:
        launch.check_tool("codex", route_kind="subscription", home=str(home),
                          argv=commands.argv("codex", "build", "b", tmp_path))
    assert caught.value.detail["setting"] == "OPENAI_API_KEY" and SECRET not in str(caught.value)


def test_routes_come_from_the_item_and_an_unqualified_api_route_is_refused():
    assert launch.route("fakework.plan", "fakecli")["kind"] == "subscription"
    for name, tool, code in (("anthropic.api", "claude-code", "api_route_unqualified"),
                             ("openai.api", "codex", "api_route_unqualified"),
                             ("xai.api", "grok", "api_route_unqualified"),
                             ("claude-code.subscription", "codex", "route_tool_mismatch"),
                             ("unheard.of", "fakework", "route_not_in_launch_policy"), (None, "fakework",
                                                                                          "route_not_in_launch_policy")):
        with pytest.raises(launch.LaunchRefused) as caught:
            launch.route(name, tool)
        assert caught.value.code == code
    assert launch.route("fakework.api", "fakecli")["key_name"] == "FAKEWORK_API_KEY"


def test_an_api_key_is_read_by_reference_for_one_child_and_never_through_get_secret(tmp_path):
    key = "test-" + secrets.token_hex(16)
    stores = {"test": launch.TestCredentialStore({"fakework-api": key})}
    assert launch.api_key("fakework.api", "test:fakework-api", stores) == ("FAKEWORK_API_KEY", key)
    for route, ref, store, code in (("fakework.plan", "test:fakework-api", stores, "route_not_api"),
                                    ("fakework.api", None, stores, "route_key_unbound"),
                                    ("fakework.api", "keychain:svc/acct", stores, "route_key_store_missing"),
                                    ("fakework.api", "test:other", stores, "route_key_unavailable")):
        with pytest.raises(launch.LaunchRefused) as caught:
            launch.api_key(route, ref, store)
        assert caught.value.code == code and key not in str(caught.value)
    # The Keychain store is built for the Mac and never run here: a stand-in for `security` shows what it would ask.
    seen = []
    kc = launch.KeychainCredentialStore(run=lambda argv, **kw: (seen.append((argv, kw["env"])),
                                                                 subprocess.CompletedProcess(argv, 0, "v\n", ""))[1])
    assert kc.read("keychain:minimoi_route/fakework.api") == "v"
    assert seen == [(["security", "find-generic-password", "-s", "minimoi_route", "-a", "fakework.api", "-w"],
                     {"PATH": "/usr/bin:/bin"})]
    source = Path(launch.__file__).read_text()
    assert "get_secret" not in source.replace("core/get_secret.py", "")         # named in the docstring, never used
    # The file form of the test store is private (0600) or refused.
    f = tmp_path / "store.json"
    f.write_text(json.dumps({"fakework-api": key}))
    f.chmod(0o644)
    with pytest.raises(launch.LaunchRefused, match="route_key_store_unsafe"):
        launch.TestCredentialStore.from_file(f)
    f.chmod(0o600)
    assert launch.TestCredentialStore.from_file(f).read("test:fakework-api") == key


def test_host_yaml_pins_tools_and_binds_api_keys_by_reference_and_production_refuses_the_test_ones(tmp_path, mirror):
    exe, sha = _exe(tmp_path / "tools" / "fakecli")
    (tmp_path / "store.json").write_text("{}")
    (tmp_path / "store.json").chmod(0o600)
    h = make_rt(tmp_path, mirror[0], FakeAuthority(mirror[1]), tools={"fakework": {"path": exe, "sha256": sha}},
                route_keys={"fakework.api": "test:fakework-api"}, test_credentials=str(tmp_path / "store.json")).host
    assert h.tools["fakework"] == host_mod.ToolPin(exe, "sha256:" + sha) and h.route_keys == {"fakework.api":
                                                                                              "test:fakework-api"}
    from test_host_runtime import host_doc
    for over, code in (({"tools": {"grok": {"path": exe, "sha256": sha}}}, "host_tools_invalid"),
                       ({"tools": {"codex": {"path": "codex", "sha256": sha}}}, "host_tools_invalid"),
                       ({"tools": {"codex": {"path": exe, "sha256": "abc"}}}, "host_tools_invalid"),
                       ({"route_keys": {"fakework.plan": "test:x"}}, "host_route_keys_invalid"),
                       ({"route_keys": {"fakework.api": "env:FAKEWORK_API_KEY"}}, "host_route_keys_invalid"),
                       ({"route_keys": {"fakework.api": "test:x"}}, "host_test_credentials_missing")):
        with pytest.raises(host_mod.HostRefused, match=code):
            host_mod.parse(host_doc(tmp_path, mirror[0], **over))
    prod = {**host_doc(tmp_path, mirror[0]), "environment": "prod"}
    prod.pop("effect_sink")
    prod["authority"] = {"endpoint_id": "prod", "url": "https://minimoi.ai/internal/mc/v1/delegation-runner",
                         "credential_ref": "keychain:minimoi_delegation_runner/prod",
                         "values": str(tmp_path / "authority.json")}
    prod["roles"], prod["interpreter"] = ["worker"], {"path": "/usr/bin/python3", "version": "3.9"}
    assert host_mod.parse({**prod, "tools": {"claude-code": {"path": exe, "sha256": sha}}}).tools["claude-code"].path == exe
    for over in ({"tools": {"fakework": {"path": exe, "sha256": sha}}}, {"test_credentials": str(tmp_path / "s")},
                 {"route_keys": {"fakework.api": "keychain:a/b"}}, {"route_keys": {"anthropic.api": "test:x"}}):
        with pytest.raises(host_mod.HostRefused, match="host_test_instrument_in_production"):
            host_mod.parse({**prod, **over})


# ── T19 with fakecli, the planted credentials and the tripwire ─────────────────────────────────────────────────────────
class T19:
    pass


@pytest.fixture
def t19(tmp_path, mirror, monkeypatch):
    t = T19()
    t.tmp, t.mirror = tmp_path, mirror
    t.planted = {n: "test-planted-" + secrets.token_hex(20) for n in PLANTED}     # synthetic, made for this run only
    t.key = "test-route-key-" + secrets.token_hex(20)                           # fakework.api's, held in the test store
    for name, value in t.planted.items():
        monkeypatch.setenv(name, value)
    t.trip = tmp_path / "tripwire"
    t.trip.mkdir()
    for name in TRIPWIRE:                                                           # provider-tripwire (§9.3), first on PATH
        _exe(t.trip / name, f'#!/bin/sh\necho "{name}" >> "{t.trip}/tripwire.jsonl"\nexit 97\n')
    monkeypatch.setenv("PATH", f"{t.trip}:{os.environ['PATH']}")
    t.home = Path(os.environ["HOME"])                                               # the test's own (test_host_runtime._reap)
    fakecli.sign_in(t.home)
    t.cli_root = tmp_path / "fakecli"
    t.exe, t.sha = fakecli.install(t.cli_root, planted=[*t.planted.values(), t.key])
    t.store = tmp_path / "store" / "test-credentials.json"                          # the store itself, like the Keychain
    t.store.parent.mkdir()
    t.store.write_text(json.dumps({"fakework-api": t.key}))
    t.store.chmod(0o600)
    t.fake = FakeAuthority(mirror[1])
    t.rt = make_rt(tmp_path, mirror[0], t.fake, tools={"fakework": {"path": t.exe, "sha256": t.sha}},
                   route_keys={"fakework.api": "test:fakework-api"}, test_credentials=str(t.store))
    t.outs = []
    return t


def _run(t, brief="fakework/1\nreport_usage 0.5\nresult ok", **item):
    rid = t.fake.add(brief, **item)
    out = run_pass(t.rt)
    t.outs.append(out)
    return rid, out


def _record(t, rid, g=1):
    path = t.tmp / "artifacts" / f"{rid}-g{g}.launch.json"
    return json.loads(path.read_text()) if path.exists() else None


def _result_detail(t, rid, g=1):
    return [c for c in t.rt.journal(f"{rid}-g{g}").calls() if c["kind"] == "result"][-1]["args"]["detail"]


def lch6(t, extra=()):
    """No planted value (nor the route key) in any file this run wrote, nor in any answer it returned; the store file
    is the store itself and is left out, as the Keychain would be. The digests fakecli recorded are digests."""
    values = [v.encode() for v in (*t.planted.values(), t.key, *extra)]
    searched = 0
    for p in t.tmp.rglob("*"):
        if p.is_file() and not p.is_symlink() and t.store.parent not in p.parents:
            data = p.read_bytes()
            searched += 1
            for v in values:
                assert v not in data, p
    blob = json.dumps(t.outs, default=str).encode()
    assert not [v for v in values if v in blob]
    return searched


def lch1(t):
    """No child's environment holds a deny-pattern name, except the API item's own key in its child only: from every
    launch record and from fakecli-env.jsonl."""
    reviewed = L.DENY_OVERRIDES | L.INHERIT_OVERRIDES["unattended"]               # the policy's reviewed overrides only
    records = [json.loads(p.read_text()) for p in (t.tmp / "artifacts").glob("*.launch.json")]
    for rec in records:
        allowed = reviewed | ({rec["key"]["name"]} if rec["key"] else set())
        assert [n for n in rec["env_names"] if L.denied(n) and n not in allowed] == []
        assert rec["policy"] == {"version": L.POLICY_VERSION, "sha256": L.POLICY_DIGEST}    # LCH-6: one digest
    keyed = [start for start in fakecli.starts(t.cli_root) if "FAKEWORK_API_KEY" in start["names"]]
    assert len(keyed) == len([r for r in records if r["key"]])                    # the key in the API children only
    for start in fakecli.starts(t.cli_root):
        assert [n for n in start["names"] if L.denied(n) and n not in reviewed | {"FAKEWORK_API_KEY"}] == []
    return records


def test_t19a_a_subscription_item_starts_with_only_reviewed_names_and_its_record_lists_the_planted_ones_removed(t19):
    rid, out = _run(t19, route="fakework.plan")
    assert out["state"] == "completed", out
    (start,) = fakecli.starts(t19.cli_root)
    given = set(start["names"])
    # Exactly the reviewed names: the base list the runtime holds, and the runner's one extra. Nothing the barrier's
    # interpreter adds (LC_CTYPE, __CF_USER_TEXT_ENCODING) reaches it.
    assert start["source"] in ("kern.procargs2", "proc")
    assert given == {n for n in L.BASE_ALLOW if n in os.environ} | {"GIT_TERMINAL_PROMPT"}, given
    assert not given & set(PLANTED) and start["planted"] == []                    # no planted name, no planted value
    rec = _record(t19, rid)
    assert rec["schema"] == "minimoi.launch.v1" and rec["start_path"] == "runtime" and rec["profile"] == "unattended"
    assert rec["route"] == "fakework.plan" and rec["route_kind"] == "subscription" and rec["tool"] == "fakecli"
    assert rec["binary"] == {"path": t19.exe, "sha256": "sha256:" + t19.sha, "pinned_by": "host_config"}
    assert set(PLANTED) <= set(rec["removed"]) and rec["key"] is None
    assert set(rec["env_names"]) == given and rec["checks"]["signin"] == "present"
    assert rec["item_id"] == rid and rec["generation"] == 1 and rec["host_id"] == "mac-test-1"
    lch1(t19)
    assert not (t19.trip / "tripwire.jsonl").exists()                             # MC-0: no provider binary ran
    assert lch6(t19) > 10


def test_t19b_with_the_sign_in_missing_the_item_is_refused_before_any_spawn_and_never_on_a_key(t19):
    fakecli.sign_out(t19.home)
    rid, out = _run(t19, route="fakework.plan")
    assert out["state"] == "refused" and out["code"] == "signin_missing"
    assert _result_detail(t19, rid) == {"code": "signin_missing", "tool": "fakecli", "login": "fakecli login"}
    assert fakecli.starts(t19.cli_root) == [] and _record(t19, rid) is None      # nothing started, no launch record
    assert not (t19.tmp / "work" / f"{rid}-g1").exists()                         # refused before the workspace
    assert not (t19.trip / "tripwire.jsonl").exists()
    lch6(t19)


def test_t19c_a_key_helper_in_user_then_in_project_configuration_is_refused_naming_the_setting(t19):
    secret = "helper-value-" + secrets.token_hex(12)
    (t19.home / ".fakecli" / "settings.json").write_text(json.dumps({"apiKeyHelper": "/bin/echo " + secret}))
    rid, out = _run(t19, route="fakework.plan")
    assert out["state"] == "refused" and out["code"] == "key_config_present"
    assert _result_detail(t19, rid) == {"code": "key_config_present", "tool": "fakecli",
                                        "file": "{home}/.fakecli/settings.json", "setting": "apiKeyHelper"}
    (t19.home / ".fakecli" / "settings.json").unlink()
    # Separately, in the project's own configuration, committed at the item's commit: refused once the checkout exists.
    repo = t19.mirror[0]
    (repo / ".fakecli").mkdir()
    (repo / ".fakecli" / "settings.json").write_text(json.dumps({"apiKeyHelper": "/bin/echo " + secret}))
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "project helper")
    t19.fake.ref = git(repo, "rev-parse", "HEAD")
    rid2, out2 = _run(t19, route="fakework.plan")
    assert out2["state"] == "refused" and out2["code"] == "key_config_present"
    assert _result_detail(t19, rid2)["file"] == "{project}/.fakecli/settings.json"
    assert not (t19.tmp / "work" / f"{rid2}-g1").exists()                        # the fresh checkout went: nothing ran
    assert fakecli.starts(t19.cli_root) == []
    artifacts = b"".join(p.read_bytes() for p in (t19.tmp / "artifacts").rglob("*") if p.is_file())
    assert secret.encode() not in artifacts                                       # the setting is named, its value never
    lch6(t19)


def test_t19d_a_binary_changed_after_pinning_is_refused_before_any_spawn(t19, capsys):
    with open(t19.exe, "a") as fh:
        fh.write("# changed after pinning\n")
    rid, out = _run(t19, route="fakework.plan")
    assert out["state"] == "refused" and out["code"] == "binary_unpinned"
    assert _result_detail(t19, rid) == {"code": "binary_unpinned", "reason": "digest_mismatch"}
    assert fakecli.starts(t19.cli_root) == [] and _record(t19, rid) is None
    assert not (t19.tmp / "work" / f"{rid}-g1").exists()
    # doctor says so too, by name.
    assert cli.main(["doctor", "--host-config", str(t19.tmp / "host.yaml")]) == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["tools"]["fakework"]["matches"] is False and "binary_unpinned:fakework" in doc["problems"]
    lch6(t19)


def test_t19e_the_api_key_reaches_the_api_items_child_only_under_the_routes_name_with_the_per_item_cap(t19):
    rid_api, out_api = _run(t19, brief="fakework/1\nreport_usage 0.5\nresult ok", route="fakework.api", unit="usd",
                            reserved=3.0)
    rid_sub, out_sub = _run(t19, route="fakework.plan")
    assert out_api["state"] == "completed" and out_sub["state"] == "completed", (out_api, out_sub)
    api_start, sub_start = fakecli.starts(t19.cli_root)
    key_sha = hashlib.sha256(t19.key.encode()).hexdigest()
    assert api_start["planted"] == [{"name": "FAKEWORK_API_KEY", "sha256": key_sha}]     # the key, under its route's name
    assert "FAKEWORK_API_KEY" not in sub_start["names"] and sub_start["planted"] == []   # never the subscription child
    rec = _record(t19, rid_api)
    assert rec["key"] == {"name": "FAKEWORK_API_KEY", "ref": "test:fakework-api"} and rec["route_kind"] == "api"
    assert "FAKEWORK_API_KEY" in rec["env_names"] and _record(t19, rid_sub)["key"] is None
    assert "FAKEWORK_API_KEY" not in os.environ                                  # never the runtime's own environment
    # The reservation is in the route's unit: usd, reported back in usd.
    assert t19.fake.items[rid_api]["result"]["usage"] == {"amount": 0.5, "unit": "usd", "children_stopped": True}
    # The per-item cap (S1-D's seam): a child that reports past what remains is stopped at the cap.
    rid_over, out_over = _run(t19, brief="fakework/1\nreport_usage 5\nresult ok", route="fakework.api", unit="usd",
                              reserved=3.0)
    assert out_over["state"] == "failed" and t19.fake.items[rid_over]["result"]["usage"]["amount"] == 3.0
    assert (t19.tmp / "artifacts" / f"{rid_over}-g1.log").read_text().splitlines()[-1] == "fakework: cap reached: 3.0 usd"
    records = lch1(t19)
    assert len(records) == 3 and len({r["policy"]["sha256"] for r in records}) == 1       # LCH-6: one policy digest
    assert not (t19.trip / "tripwire.jsonl").exists()
    lch6(t19)


def test_mc0_doctor_env_names_the_runtime_holds_the_planted_names_its_children_none(t19, capsys):
    cli.main(["doctor", "--host-config", str(t19.tmp / "host.yaml"), "--env-names"])
    doc = json.loads(capsys.readouterr().out)
    assert set(PLANTED) <= set(doc["env_names"]["runtime_credential_shaped"])      # by design, in T19 (§9.3)
    assert doc["env_names"]["child_credential_shaped"] == []
    assert doc["launch_policy"] == {"version": L.POLICY_VERSION, "sha256": L.POLICY_DIGEST}
    assert doc["tools"]["fakework"]["matches"] is True and doc["route_keys"] == {"fakework.api": "test:fakework-api"}
    assert not [v for v in t19.planted.values() if v in json.dumps(doc)]


def test_a_real_cli_on_a_version_2_host_is_never_started_unpinned_and_stays_activation_gated(t19):
    """claude-code and codex stay gated (no unit enforcer, R4.5-d); the launch contract around them is built: with no
    pin there is nothing to start, and a pinned one starts only by its path."""
    from core.mc_delegation_runner import envelope
    e = envelope.parse({**t19.fake.items[t19.fake.add("x")]["env"], "actor": "claude-code"},
                       allowed_repositories={"personal-ai-agents": "x"}, max_cost_usd=3.0, now=datetime.now(timezone.utc),
                       actors=envelope.V2_ACTORS)
    for route in ("claude-code.subscription", "anthropic.api"):
        with pytest.raises(launch.LaunchRefused, match="binary_unpinned"):
            t19.rt._contract(e, {"route": route})
    assert "claude-code" not in cost.UNIT_ENFORCERS and "codex" not in cost.UNIT_ENFORCERS


# ── N-F2 (S1-D's final closure): a blocked second readback after "continue" never raises ─────────────────────────────
def test_n_f2_continue_with_no_child_then_a_blocked_readback_returns_none_and_keeps_the_task(tmp_path, mirror):
    fake = FakeAuthority(mirror[1])
    rt = make_rt(tmp_path, mirror[0], fake)
    rt.cfg.artifact_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    fake.add(program("result ok"))
    claim = rt._record_claim(fake.claim(rt.runner_id, []))
    task = claim["task"]

    def blocked_readback(t, c):
        rt._block(t, "authority_error:test")              # what NC1's readback does when an outcome is on file
        return []
    rt._readback = blocked_readback
    assert rt.child is None
    assert rt._workspace(task, {"answer": "continue", "generation": 1, "lease_until": claim["lease_until"]}) is None
    assert task in rt.blocked and task in rt.owned()       # kept, blocked under its reason; never closed, never raised
