"""mm-claude / mm-codex / mm-grok: clean environment, plan sign-in checks, names only (scripts/agents/mm_agent.py).

Every test uses a temporary home, fake tool binaries, a fake Keychain probe and a fake execve: no real tool starts,
the real Keychain is never read, and nothing outside the temporary folder is written.
"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("mm_agent", ROOT / "scripts" / "agents" / "mm_agent.py")
mm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mm)

SECRET_VALUES = {"ANTHROPIC_API_KEY": "sk-ant-test-value-1111", "OPENAI_API_KEY": "sk-test-value-2222",
                 "XAI_API_KEY": "xai-test-value-3333", "TELEGRAM_BOT_TOKEN": "123:test-value-4444",
                 "AWS_SECRET_ACCESS_KEY": "aws-test-value-5555", "GH_TOKEN": "gh-test-value-6666"}


class Harness(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.home = self.root / "home"
        self.project = self.home / "Projects" / "demo"
        self.project.mkdir(parents=True)
        self.bins = {}
        for tool in mm.TOOLS:
            path = self.root / "bin" / tool
            path.parent.mkdir(exist_ok=True)
            path.write_text("#!/bin/sh\nexit 0\n")
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
            self.bins[tool] = str(path)
        self._saved = (mm.BINARIES, mm.CLAUDE_MANAGED_SETTINGS, mm.ETC, mm.MANAGED_PREFS)
        mm.BINARIES = {tool: (path,) for tool, path in self.bins.items()}
        mm.CLAUDE_MANAGED_SETTINGS = self.root / "managed-settings.json"
        mm.ETC = self.root / "etc"
        mm.MANAGED_PREFS = self.root / "managed-prefs"
        self.calls = []
        self.environ = {"HOME": str(self.home), "PATH": "/usr/bin:/bin", "TERM": "xterm-256color",
                        "LANG": "en_US.UTF-8", "LC_CTYPE": "UTF-8", "SSH_AUTH_SOCK": "/tmp/agent.sock",
                        "PWD": str(self.project), "XPC_SERVICE_NAME": "0", **SECRET_VALUES}

    def tearDown(self):
        mm.BINARIES, mm.CLAUDE_MANAGED_SETTINGS, mm.ETC, mm.MANAGED_PREFS = self._saved
        self._tmp.cleanup()

    def write(self, rel, content):
        path = Path(rel) if Path(rel).is_absolute() else self.home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if isinstance(content, str) else json.dumps(content))
        return path

    def sign_in(self, tool):
        if tool == "grok":
            self.write(".grok/auth.json", {"https://auth.x.ai::id": {"auth_mode": "oidc", "key": "session"}})
        elif tool == "codex":
            self.write(".codex/auth.json", {"auth_mode": "chatgpt", "OPENAI_API_KEY": None, "tokens": {}})

    def run_main(self, argv, probe=lambda service: True, cwd=None):
        err, out = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            code = mm.main(argv, environ=self.environ, home=self.home, cwd=cwd or self.project,
                           execve=lambda path, args, env: self.calls.append((path, args, env)),
                           probe=probe, self_path=str(self.root / "mm-agent"))
        self.stderr = err.getvalue()
        for value in SECRET_VALUES.values():
            self.assertNotIn(value, self.stderr + out.getvalue(), "a secret value was printed")
        return code

    def sessions(self):
        path = self.home / ".minimoi" / "agents" / "fallback_sessions.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


class CleanEnvironment(Harness):
    def test_only_allowlisted_names_reach_the_tool(self):
        env, removed = mm.clean_env(self.environ, "grok")
        self.assertEqual(set(env), {"HOME", "PATH", "PWD", "TERM", "LANG", "LC_CTYPE", "SSH_AUTH_SOCK"})
        for name in SECRET_VALUES:
            self.assertIn(name, removed)
        self.assertIn("XPC_SERVICE_NAME", removed)

    def test_a_proxy_base_url_from_a_parent_session_is_dropped(self):
        self.environ.update(ANTHROPIC_BASE_URL="http://127.0.0.1:1", CLAUDECODE="1", CLAUDE_CODE_SESSION_ID="x")
        env, removed = mm.clean_env(self.environ, "claude")
        self.assertFalse({"ANTHROPIC_BASE_URL", "CLAUDECODE", "CLAUDE_CODE_SESSION_ID"} & set(env))

    def test_only_posix_locale_names_pass(self):
        self.environ.update(LC_ALL="C", LC_XAI_API_KEY="xai-test-value-3333")
        env, removed = mm.clean_env(self.environ, "grok")
        self.assertIn("LC_ALL", env)
        self.assertNotIn("LC_XAI_API_KEY", env)
        self.assertIn("LC_XAI_API_KEY", removed)

    def test_the_tool_home_variable_passes_only_for_its_own_tool(self):
        self.environ["GROK_HOME"] = str(self.home / ".grok")
        self.assertIn("GROK_HOME", mm.clean_env(self.environ, "grok")[0])
        self.assertNotIn("GROK_HOME", mm.clean_env(self.environ, "claude")[0])

    def test_result_line_names_credentials_and_counts_the_rest(self):
        line = mm.summary("grok", "plan sign-in (oidc)", ["PWD", "XAI_API_KEY", "XPC_SERVICE_NAME"])
        self.assertEqual(line, "mm-grok: plan sign-in (oidc) · removed: XAI_API_KEY · +2 other names")


class Grok(Harness):
    def test_starts_on_the_plan_with_a_clean_environment_and_passes_arguments(self):
        self.sign_in("grok")
        self.assertEqual(self.run_main(["mm-grok", "-p", "hello"]), 0)
        (path, args, env), = self.calls
        self.assertEqual((path, args), (self.bins["grok"], [self.bins["grok"], "-p", "hello"]))
        self.assertNotIn("XAI_API_KEY", env)
        self.assertIn("plan sign-in (oidc)", self.stderr)
        self.assertIn("XAI_API_KEY", self.stderr)

    def test_without_a_sign_in_it_refuses_and_never_uses_the_key(self):
        self.assertEqual(self.run_main(["mm-grok"]), mm.EX_CONFIG)
        self.assertEqual(self.calls, [])
        self.assertIn("grok login", self.stderr)

    def test_a_user_config_api_key_is_refused(self):
        self.sign_in("grok")
        self.write(".grok/config.toml", '[models.fast]\napi_key = "literal"\n')
        self.assertEqual(self.run_main(["mm-grok"]), mm.EX_CONFIG)
        self.assertIn("api_key", self.stderr)
        self.assertNotIn("literal", self.stderr)

    def test_a_project_config_env_key_is_refused(self):
        self.sign_in("grok")
        self.write("Projects/demo/.grok/config.toml", '[models.x]\nenv_key = "XAI_API_KEY"\n')
        self.assertEqual(self.run_main(["mm-grok"]), mm.EX_CONFIG)
        self.assertEqual(self.calls, [])

    def test_every_documented_layer_is_checked(self):
        layers = [".grok/managed_config.toml", ".grok/requirements.toml",
                  str(self.root / "etc/grok/managed_config.toml"), str(self.root / "etc/grok/requirements.toml")]
        self.sign_in("grok")
        self.assertEqual(self.run_main(["mm-grok", "--mm-check"]), 0)
        for layer in layers:
            with self.subTest(layer=layer):
                path = self.write(layer, '[model.fast]\napi_key = "literal"\n')
                self.assertEqual(self.run_main(["mm-grok"]), mm.EX_CONFIG)
                self.assertIn(str(path), self.stderr)
                path.unlink()

    def test_toml_spellings_that_hide_a_key_are_caught(self):
        for text in ('"api_key" = "literal"\n', 'model.fast.api_key = "literal"\n',
                     'fast = { api_key = "literal" }\n', '[auth]\nauth_provider_command = "/bin/echo"\n',
                     '[model.x]\nauth_provider = "acme"\n'):
            with self.subTest(text=text):
                self.sign_in("grok")
                self.write(".grok/config.toml", text)
                self.assertEqual(self.run_main(["mm-grok"]), mm.EX_CONFIG)

    def test_harmless_settings_pass(self):
        self.sign_in("grok")
        self.write(".grok/config.toml", '[models]\ndefault = "grok-build"\n[ui]\nauth_provider_label = "x"\n'
                                        '[mcp_servers.s]\nbearer_token_env_var = "S"\n')
        self.assertEqual(self.run_main(["mm-grok", "--mm-check"]), 0)

    def test_managed_preferences_are_refused(self):
        self.sign_in("grok")
        self.write(str(self.root / "managed-prefs/ai.x.grok.plist"), "")
        self.assertEqual(self.run_main(["mm-grok"]), mm.EX_CONFIG)


class Claude(Harness):
    def test_starts_when_the_keychain_sign_in_exists(self):
        self.assertEqual(self.run_main(["mm-claude", "--resume"]), 0)
        (path, args, env), = self.calls
        self.assertEqual(args, [self.bins["claude"], "--resume"])
        self.assertNotIn("ANTHROPIC_API_KEY", env)

    def test_the_keychain_probe_asks_for_the_claude_service_only(self):
        asked = []
        self.run_main(["mm-claude", "--mm-check"], probe=lambda service: asked.append(service) or True)
        self.assertEqual(asked, ["Claude Code-credentials"])

    def test_without_a_sign_in_it_refuses(self):
        self.assertEqual(self.run_main(["mm-claude"], probe=lambda service: False), mm.EX_CONFIG)
        self.assertEqual(self.calls, [])

    def test_a_key_in_a_settings_env_block_is_refused(self):
        self.write(".claude/settings.json", {"env": {"ANTHROPIC_API_KEY": "literal"}})
        self.assertEqual(self.run_main(["mm-claude"]), mm.EX_CONFIG)
        self.assertIn("ANTHROPIC_API_KEY", self.stderr)
        self.assertNotIn("literal", self.stderr)

    def test_a_project_api_key_helper_is_refused(self):
        self.write("Projects/demo/.claude/settings.local.json", {"apiKeyHelper": "/bin/echo"})
        self.assertEqual(self.run_main(["mm-claude"]), mm.EX_CONFIG)
        self.assertIn("apiKeyHelper", self.stderr)

    def test_settings_env_names_that_reroute_or_carry_auth_are_refused(self):
        for name in ("ANTHROPIC_BASE_URL", "ANTHROPIC_UNIX_SOCKET", "CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR",
                     "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "AWS_PROFILE"):
            with self.subTest(name=name):
                self.write(".claude/settings.json", {"env": {name: "literal"}})
                self.assertEqual(self.run_main(["mm-claude"]), mm.EX_CONFIG)
                self.assertIn(name, self.stderr)

    def test_managed_settings_file_is_checked(self):
        self.write(str(mm.CLAUDE_MANAGED_SETTINGS), {"apiKeyHelper": "/bin/echo"})
        self.assertEqual(self.run_main(["mm-claude"]), mm.EX_CONFIG)
        self.assertIn("managed-settings.json", self.stderr)

    def test_managed_preferences_plist_is_checked(self):
        import plistlib
        path = self.root / "managed-prefs" / "com.anthropic.claudecode.plist"
        path.parent.mkdir(parents=True)
        path.write_bytes(plistlib.dumps({"env": {"ANTHROPIC_API_KEY": "literal"}}))
        self.assertEqual(self.run_main(["mm-claude"]), mm.EX_CONFIG)
        self.assertIn("com.anthropic.claudecode.plist", self.stderr)

    def test_remote_settings_are_checked(self):
        self.write(".claude/remote-settings.json", {"apiKeyHelper": "/bin/echo"})
        self.assertEqual(self.run_main(["mm-claude"]), mm.EX_CONFIG)

    def test_a_settings_argument_is_checked_as_json_or_file(self):
        self.assertEqual(self.run_main(["mm-claude", "--settings", '{"apiKeyHelper": "/bin/echo"}']), mm.EX_CONFIG)
        self.write("Projects/demo/extra.json", {"env": {"ANTHROPIC_API_KEY": "literal"}})
        self.assertEqual(self.run_main(["mm-claude", "--settings=extra.json"]), mm.EX_CONFIG)
        self.write("Projects/demo/ok.json", {"model": "opus"})
        self.assertEqual(self.run_main(["mm-claude", "--settings", "ok.json"]), 0)
        self.assertEqual(self.calls[-1][1], [self.bins["claude"], "--settings", "ok.json"])

    def test_a_custom_config_dir_probes_its_own_keychain_item(self):
        import hashlib
        folder = str(self.home / "alt-claude")
        self.environ["CLAUDE_CONFIG_DIR"] = folder
        asked = []
        self.run_main(["mm-claude", "--mm-check"], probe=lambda service: asked.append(service) or True)
        self.assertEqual(asked, ["Claude Code-credentials-" + hashlib.sha256(folder.encode()).hexdigest()[:8]])

    def test_permission_rules_that_mention_key_names_are_fine(self):
        self.write("Projects/demo/.claude/settings.local.json",
                   {"permissions": {"allow": ["Bash(grep -c ^XAI_API_KEY= .env)"]}})
        self.assertEqual(self.run_main(["mm-claude"]), 0)


class Codex(Harness):
    def test_starts_on_the_chatgpt_sign_in(self):
        self.sign_in("codex")
        self.assertEqual(self.run_main(["mm-codex"]), 0)
        self.assertNotIn("OPENAI_API_KEY", self.calls[0][2])
        self.assertIn("plan sign-in (chatgpt)", self.stderr)

    def test_an_api_key_sign_in_is_refused(self):
        self.write(".codex/auth.json", {"auth_mode": "apikey", "OPENAI_API_KEY": "literal"})
        self.assertEqual(self.run_main(["mm-codex"]), mm.EX_CONFIG)
        self.assertNotIn("literal", self.stderr)

    def test_a_stored_api_key_beside_chatgpt_is_refused(self):
        self.write(".codex/auth.json", {"auth_mode": "chatgpt", "OPENAI_API_KEY": "literal"})
        self.assertEqual(self.run_main(["mm-codex"]), mm.EX_CONFIG)

    def test_a_config_that_prefers_the_api_key_is_refused(self):
        self.sign_in("codex")
        self.write(".codex/config.toml", 'preferred_auth_method = "apikey"\n')
        self.assertEqual(self.run_main(["mm-codex"]), mm.EX_CONFIG)

    def test_an_env_file_codex_loads_itself_is_refused_by_name(self):
        self.sign_in("codex")
        self.write(".codex/.env", "# comment\n\nexport OPENAI_API_KEY=literal\nOTHER=1\n")
        self.assertEqual(self.run_main(["mm-codex"]), mm.EX_CONFIG)
        self.assertIn("OPENAI_API_KEY, OTHER", self.stderr)
        self.assertNotIn("literal", self.stderr)

    def test_an_empty_or_comment_only_env_file_passes(self):
        self.sign_in("codex")
        self.write(".codex/.env", "# nothing here\n\n")
        self.assertEqual(self.run_main(["mm-codex", "--mm-check"]), 0)

    def test_provider_routes_in_any_layer_are_refused(self):
        cases = [(".codex/config.toml", 'model_provider = "k"\n[model_providers.k]\nbase_url = "http://x"\n'),
                 (".codex/config.toml", '[model_providers.openai]\nexperimental_bearer_token = "literal"\n'),
                 (".codex/managed_config.toml", '[model_providers.k]\nenv_key = "OPENAI_API_KEY"\n'),
                 (".codex/requirements.toml", 'profiles.w.forced_login_method = "api"\n'),
                 (str(self.root / "etc/codex/config.toml"), 'forced_login_method = "api"\n'),
                 (str(self.root / "etc/codex/managed_config.toml"), 'p = { model_provider = "k" }\n')]
        self.sign_in("codex")
        for layer, text in cases:
            with self.subTest(layer=layer, text=text):
                path = self.write(layer, text)
                self.assertEqual(self.run_main(["mm-codex"]), mm.EX_CONFIG)
                self.assertNotIn("literal", self.stderr)
                path.unlink()

    def test_the_openai_provider_and_chatgpt_login_pass(self):
        self.sign_in("codex")
        self.write(".codex/config.toml", 'model = "gpt-5.5"\nmodel_provider = "openai"\nforced_login_method = "chatgpt"\n')
        self.assertEqual(self.run_main(["mm-codex", "--mm-check"]), 0)

    def test_arguments_that_route_to_a_key_are_refused(self):
        self.sign_in("codex")
        for args in (["-c", "model_provider=oss"], ["--config=model_providers.k.env_key=X"], ["-cenv_key=X"],
                     ["-c", "profiles.w.forced_login_method=api"], ["--remote-auth-token-env", "T"]):
            with self.subTest(args=args):
                self.assertEqual(self.run_main(["mm-codex", *args]), mm.EX_CONFIG)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.run_main(["mm-codex", "-c", 'model="gpt-5.5"', "-c", "model_provider=openai"]), 0)

    def test_codex_managed_preferences_are_refused(self):
        self.sign_in("codex")
        self.write(str(self.root / "managed-prefs" / "vanstedum" / "com.openai.codex.plist"), "")
        self.environ["USER"] = "vanstedum"
        self.assertEqual(self.run_main(["mm-codex"]), mm.EX_CONFIG)


class Door(Harness):
    def test_check_only_starts_nothing_and_records_nothing(self):
        self.sign_in("grok")
        self.assertEqual(self.run_main(["mm-grok", "--mm-check"]), 0)
        self.assertEqual((self.calls, self.sessions()), ([], []))

    def test_session_record_holds_names_only(self):
        self.sign_in("grok")
        self.run_main(["mm-grok"])
        (row,) = self.sessions()
        self.assertEqual((row["schema"], row["tool"], row["cwd"]),
                         ("minimoi.fallback_session.v0", "grok", str(self.project)))
        self.assertIn("XAI_API_KEY", row["removed_credential_names"])
        text = (self.home / ".minimoi" / "agents" / "fallback_sessions.jsonl").read_text()
        for value in SECRET_VALUES.values():
            self.assertNotIn(value, text)
        mode = (self.home / ".minimoi" / "agents" / "fallback_sessions.jsonl").stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_tool_named_as_first_argument(self):
        self.sign_in("grok")
        self.assertEqual(self.run_main(["mm-agent", "grok", "--version"]), 0)
        self.assertEqual(self.calls[0][1], [self.bins["grok"], "--version"])

    def test_no_tool_is_a_usage_error(self):
        self.assertEqual(self.run_main(["mm-agent"]), mm.EX_USAGE)

    def test_missing_binary(self):
        self.sign_in("grok")
        mm.BINARIES = {**mm.BINARIES, "grok": (str(self.root / "absent"),)}
        self.assertEqual(self.run_main(["mm-grok"]), mm.EX_UNAVAILABLE)

    RUNTIME_CLAUDE = "~/minimoi-runtime/tools/claude-code-*/claude.app/Contents/MacOS/claude"

    def runtime_claude(self, version, executable=True):
        path = self.home / "minimoi-runtime" / "tools" / f"claude-code-{version}" / "claude.app" / "Contents" / "MacOS" / "claude"
        path.parent.mkdir(parents=True)
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o755 if executable else 0o644)
        return str(path)

    def test_the_pinned_runtime_claude_comes_before_homebrew(self):
        real = self._saved[0]["claude"]
        self.assertEqual(real[0], self.RUNTIME_CLAUDE)
        self.assertLess(real.index(self.RUNTIME_CLAUDE), real.index("/opt/homebrew/bin/claude"))

    def test_the_newest_runtime_claude_starts_by_version_not_by_name(self):
        for version in ("2.1.9", "2.1.40"):
            self.runtime_claude(version)
        newest = self.runtime_claude("2.1.308")
        mm.BINARIES = {**mm.BINARIES, "claude": (self.RUNTIME_CLAUDE, self.bins["claude"])}
        self.assertEqual(mm.resolve_binary("claude", self.home, str(self.root / "mm-agent")), newest)
        self.assertEqual(self.run_main(["mm-claude", "--version"]), 0)
        self.assertEqual(self.calls[0][0], newest)

    def test_a_runtime_copy_that_cannot_run_is_skipped(self):
        older = self.runtime_claude("2.1.40")
        self.runtime_claude("2.1.308", executable=False)
        mm.BINARIES = {**mm.BINARIES, "claude": (self.RUNTIME_CLAUDE, self.bins["claude"])}
        self.assertEqual(mm.resolve_binary("claude", self.home, str(self.root / "mm-agent")), older)

    def test_without_a_runtime_copy_the_next_candidate_starts(self):
        mm.BINARIES = {**mm.BINARIES, "claude": (self.RUNTIME_CLAUDE, self.bins["claude"])}
        self.assertEqual(mm.resolve_binary("claude", self.home, str(self.root / "mm-agent")), self.bins["claude"])

    def test_the_launcher_never_starts_itself(self):
        me = self.root / "mm-agent"
        me.write_text("#!/bin/sh\n")
        me.chmod(0o755)
        mm.BINARIES = {**mm.BINARIES, "grok": (str(me),)}
        self.assertIsNone(mm.resolve_binary("grok", self.home, str(me)))

    def test_the_launcher_never_starts_itself_through_a_symlink(self):
        me = self.root / "mm-agent"
        me.write_text("#!/bin/sh\n")
        me.chmod(0o755)
        (self.root / "mm-grok").symlink_to(me)
        (self.root / "grok").symlink_to(me)
        mm.BINARIES = {**mm.BINARIES, "grok": (str(self.root / "grok"),)}
        self.assertIsNone(mm.resolve_binary("grok", self.home, str(self.root / "mm-grok")))

    def test_the_keychain_probe_never_asks_for_the_secret(self):
        seen = []

        class Done:
            returncode = 0
        saved = mm.subprocess.run
        mm.subprocess.run = lambda argv, **kw: seen.append(argv) or Done()
        try:
            self.assertTrue(mm.keychain_has("Claude Code-credentials"))
        finally:
            mm.subprocess.run = saved
        (argv,) = seen
        self.assertEqual(argv, ["/usr/bin/security", "find-generic-password", "-s", "Claude Code-credentials"])
        self.assertFalse({"-w", "-g"} & set(argv))

    def test_an_existing_record_file_is_tightened_and_a_symlink_is_not_followed(self):
        self.sign_in("grok")
        folder = self.home / ".minimoi" / "agents"
        folder.mkdir(parents=True)
        record = folder / "fallback_sessions.jsonl"
        record.write_text("")
        record.chmod(0o644)
        self.run_main(["mm-grok"])
        self.assertEqual(record.stat().st_mode & 0o777, 0o600)
        record.unlink()
        target = self.root / "elsewhere.txt"
        target.write_text("")
        record.symlink_to(target)
        self.run_main(["mm-grok"])
        self.assertEqual(target.read_text(), "")
        self.assertIn("session not recorded", self.stderr)

    def test_created_folders_are_private(self):
        self.sign_in("grok")
        self.run_main(["mm-grok"])
        for folder in (self.home / ".minimoi", self.home / ".minimoi" / "agents"):
            self.assertEqual(folder.stat().st_mode & 0o777, 0o700)

    def test_a_start_failure_is_reported_not_raised(self):
        self.sign_in("grok")
        err = io.StringIO()

        def fail(path, args, env):
            raise PermissionError(13, "denied")
        with contextlib.redirect_stderr(err):
            code = mm.main(["mm-grok"], environ=self.environ, home=self.home, cwd=self.project,
                           execve=fail, probe=lambda s: True, self_path=str(self.root / "mm-agent"))
        self.assertEqual(code, mm.EX_UNAVAILABLE)
        self.assertIn("could not be started", err.getvalue())


if __name__ == "__main__":
    unittest.main()
