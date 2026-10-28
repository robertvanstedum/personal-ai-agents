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
        self._saved = (mm.BINARIES, mm.CLAUDE_MANAGED_SETTINGS)
        mm.BINARIES = {tool: (path,) for tool, path in self.bins.items()}
        mm.CLAUDE_MANAGED_SETTINGS = self.root / "managed-settings.json"
        self.calls = []
        self.environ = {"HOME": str(self.home), "PATH": "/usr/bin:/bin", "TERM": "xterm-256color",
                        "LANG": "en_US.UTF-8", "LC_CTYPE": "UTF-8", "SSH_AUTH_SOCK": "/tmp/agent.sock",
                        "PWD": str(self.project), "XPC_SERVICE_NAME": "0", **SECRET_VALUES}

    def tearDown(self):
        mm.BINARIES, mm.CLAUDE_MANAGED_SETTINGS = self._saved
        self._tmp.cleanup()

    def write(self, rel, content):
        path = self.home / rel
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

    def test_the_launcher_never_starts_itself(self):
        me = self.root / "mm-agent"
        me.write_text("#!/bin/sh\n")
        me.chmod(0o755)
        mm.BINARIES = {**mm.BINARIES, "grok": (str(me),)}
        self.assertIsNone(mm.resolve_binary("grok", self.home, str(me)))


if __name__ == "__main__":
    unittest.main()
