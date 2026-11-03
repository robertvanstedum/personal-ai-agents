"""Exercise the real startup selector against retained runtime state, without credentials."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class SeparateRoleStartup(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.image = self.root / 'image'
        self.image.mkdir()
        self.state = self.root / 'state'
        self.workspace = self.state / 'workspace-cos-agent-a'
        self.workspace.mkdir(parents=True)
        self.config = b'{"synthetic":"configuration"}\n'
        (self.image / 'openclaw.json').write_bytes(self.config)
        (self.image / 'AGENTS.md').write_text('legacy shared runtime policy\n')
        self.policy = (ROOT / 'docker/cos-agent-a/COS_AGENTS.md').read_bytes()
        (self.image / 'COS_AGENTS.md').write_bytes(self.policy)
        self.preserved = {}
        for name in ('SOUL.md', 'USER.md', 'MEMORY.md', 'session.jsonl'):
            self.preserved[name] = ('retained adaptation ' + name).encode()
            (self.workspace / name).write_bytes(self.preserved[name])
        script = (ROOT / 'docker/cos-agent-a/apply-config.sh').read_text()
        self.script = self.root / 'apply-config.sh'
        self.script.write_text(script.replace('/opt/minimoi/cos-agent-a/', str(self.image) + '/'))
        self.marker = self.root / 'gateway-started'

    def run_startup(self, role=None):
        env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
               'OPENCLAW_CONFIG_PATH': str(self.state / 'openclaw.json'),
               'COS_GATEWAY_KEY_FILE': str(self.root / 'absent-key'),
               'MINIMOI_MODEL_GATEWAY_KEY': 'synthetic-test-value'}
        if role is not None:
            env['COS_AGENT_ROLE'] = role
        return subprocess.run(['sh', str(self.script), 'touch', str(self.marker)],
                              env=env, capture_output=True, timeout=10)

    def test_separate_policy_replaces_stale_shared_policy_and_preserves_adaptations(self):
        old = b'legacy shared runtime policy\n'
        (self.workspace / 'AGENTS.md').write_bytes(old)
        result = self.run_startup('cos')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.marker.exists())
        self.assertEqual((self.workspace / 'AGENTS.md').read_bytes(), self.policy)
        self.assertEqual((self.workspace / 'AGENTS.md.replaced-by-image').read_bytes(), old)
        for name, value in self.preserved.items():
            self.assertEqual((self.workspace / name).read_bytes(), value)
        self.assertEqual((self.state / 'openclaw.json').read_bytes(), self.config)

    def test_missing_separate_policy_fails_before_gateway_without_shared_fallback(self):
        (self.image / 'COS_AGENTS.md').unlink()
        result = self.run_startup('cos')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.marker.exists())
        self.assertFalse((self.workspace / 'AGENTS.md').exists())

    def test_invalid_selection_fails_before_gateway(self):
        result = self.run_startup('coS')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.marker.exists())

    def test_legacy_default_and_explicit_rollback_use_retained_shared_policy(self):
        for role in (None, 'shared'):
            with self.subTest(role=role):
                self.assertEqual(self.run_startup(role).returncode, 0)
                self.assertEqual((self.workspace / 'AGENTS.md').read_bytes(),
                                 (self.image / 'AGENTS.md').read_bytes())
        self.assertEqual(self.run_startup('cos').returncode, 0)
        self.assertEqual(self.run_startup('shared').returncode, 0)
        self.assertEqual((self.workspace / 'AGENTS.md').read_bytes(),
                         (self.image / 'AGENTS.md').read_bytes())


if __name__ == '__main__':
    unittest.main()
