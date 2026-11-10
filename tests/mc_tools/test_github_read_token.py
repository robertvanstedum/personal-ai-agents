"""MC's optional read-only GitHub token: used when an owner-only file holds it, absent otherwise, and the rate-limit words
say which allowance was hit. Synthetic responses only; no token value is ever printed."""
import json
import os
import stat

from core.mc_tools.github import GitHubIssues
from minimoi_portal.guild_ui import mc_context
from minimoi_portal.guild_ui.mc_tools_wiring import github_reader

TOKEN = "github_pat_" + "x" * 40


class Reply:
    def __init__(self, data, status=200):
        self.status_code, self.data, self.content = status, data, json.dumps(data).encode()

    def json(self):
        return self.data


def _token_file(tmp_path, mode=0o600):
    path = tmp_path / "github-read-token"
    path.write_text(TOKEN + "\n")
    os.chmod(path, mode)
    return str(path)


def test_the_tool_reader_sends_the_token_and_says_the_limit_was_the_authenticated_one():
    seen = []
    reader = GitHubIssues(lambda url, **kw: seen.append(kw["headers"]) or Reply({}, 403), token=TOKEN)
    state, message, _ = reader.issue(5)
    assert seen[0]["Authorization"] == "Bearer " + TOKEN and reader.authenticated
    assert state == "rate_limited" and "even with MiniMoi's read token" in message and TOKEN not in message


def test_the_tool_reader_without_a_token_is_anonymous_and_says_so():
    seen = []
    reader = GitHubIssues(lambda url, **kw: seen.append(kw["headers"]) or Reply({}, 429))
    state, message, _ = reader.issue(5)
    assert "Authorization" not in seen[0] and not reader.authenticated
    assert state == "rate_limited" and "without a credential" in message


def test_wiring_reads_the_owner_only_file_and_refuses_a_shared_one(tmp_path):
    cfg = {"github_read": True, "repo": "", "read_token_file": _token_file(tmp_path)}
    assert github_reader(cfg, http_get=lambda *a, **k: None).authenticated
    os.chmod(cfg["read_token_file"], 0o644)                        # readable by others: not used
    assert not github_reader(cfg, http_get=lambda *a, **k: None).authenticated
    assert not github_reader({"github_read": True, "repo": ""}, http_get=lambda *a, **k: None).authenticated


def test_the_turn_context_uses_the_token_when_installed(tmp_path):
    mc_context._cache.clear()
    seen = []
    env = {"MINIMOI_GUILD_GITHUB_LOOKUP": "1", "MINIMOI_MC_GITHUB_READ_TOKEN_FILE": _token_file(tmp_path)}
    block = mc_context.github_block("what about #470", environ=env,
                                    http_get=lambda url, **kw: seen.append(kw["headers"]) or Reply({}, 403))
    assert seen[0]["Authorization"] == "Bearer " + TOKEN
    assert "even with MiniMoi's read token" in block and TOKEN not in block


def test_the_turn_context_without_the_file_is_anonymous_as_before(tmp_path):
    mc_context._cache.clear()
    seen = []
    env = {"MINIMOI_GUILD_GITHUB_LOOKUP": "1", "MINIMOI_MC_GITHUB_READ_TOKEN_FILE": str(tmp_path / "absent")}
    block = mc_context.github_block("what about #471", environ=env,
                                    http_get=lambda url, **kw: seen.append(kw["headers"]) or Reply({}, 429))
    assert "Authorization" not in seen[0] and "without a credential" in block


def test_production_reads_the_token_from_the_portals_mounted_mc_secrets_folder():
    """No compose change: the default path sits in the folder production already mounts at /run/secrets/mc."""
    text = open(os.path.join(os.path.dirname(__file__), "..", "..", "docker-compose.prod.yml")).read()
    assert "/mc/portal:/run/secrets/mc:ro" in text
    assert mc_context.READ_TOKEN_DEFAULT == "/run/secrets/mc/github-read-token"
