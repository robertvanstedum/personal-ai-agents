"""#543: MC reads GitHub directly with one generic, read-only GET tool. Synthetic responses only; no network."""
import json
import os
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core.mc_tools import availability, policy as P, read_only
from core.mc_tools.catalogue import ArgumentsRefused, validate
from core.mc_tools.github import API, DEFAULT_REPO
from core.mc_tools.github_api import DENIED_SEGMENTS, OUTPUT_BYTES, GitHubRepoReader, check_path

ROOT = Path(__file__).resolve().parents[2]
REPO = f"/repos/{DEFAULT_REPO}"
TOKEN = "github_pat_" + "z" * 40


class Reply:
    def __init__(self, data=None, status=200, headers=None, raw=None):
        self.status_code, self.data, self.headers = status, data, headers or {}
        self.content = raw if raw is not None else json.dumps(data).encode()

    def json(self):
        if self.data is None:
            raise ValueError()
        return self.data


def run(i, **extra):
    return {"id": 43332883700 + i, "name": "Deploy MC agent", "display_title": f"mc-agent-v0.1.{i}", "status": "completed",
            "conclusion": "success", "event": "push", "head_branch": f"mc-agent-v0.1.{i}", "head_sha": "a" * 40,
            "run_number": i, "created_at": "2026-11-12T10:00:00Z", "run_started_at": "2026-11-12T10:00:05Z",
            "updated_at": "2026-11-12T10:04:00Z", "html_url": f"https://github.com/{DEFAULT_REPO}/actions/runs/{i}",
            "repository": {"full_name": DEFAULT_REPO, "description": "x" * 2000}, "head_commit": {"message": "y" * 2000},
            **extra}


class Stub:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def __call__(self, url, **kw):
        self.calls.append((url, kw))
        return self.reply(url, kw) if callable(self.reply) else self.reply


class PathRules(unittest.TestCase):
    def test_the_repository_and_paths_under_it_are_allowed(self):
        for path in (REPO, REPO + "/actions/runs", REPO + "/actions/runs/43332883709", REPO + "/actions/runs/43332883709/jobs",
                     REPO + "/actions/workflows", REPO + "/deployments", REPO + "/pulls/541", REPO + "/commits/main",
                     REPO + "/branches/release/1.2-ff", REPO + "/contents/core/mc_tools/github.py"):
            self.assertIsNone(check_path(path), path)

    def test_other_repositories_hosts_absolute_urls_and_traversal_are_refused(self):
        cases = {
            "/repos/someone/else/actions/runs": "path_outside_repo",
            REPO + "-evil/actions/runs": "path_outside_repo",
            "/repos/robertvanstedum": "path_outside_repo",
            "/user": "path_outside_repo",
            "/orgs/robertvanstedum/members": "path_outside_repo",
            "https://evil.example/repos/" + DEFAULT_REPO: "path_invalid",
            "//evil.example" + REPO: "path_invalid",
            "api.github.com" + REPO: "path_outside_repo",
            REPO + "/../../other/repo": "path_invalid",
            REPO + "/actions/./runs": "path_invalid",
            REPO + "/actions//runs": "path_invalid",
            REPO + "/%2e%2e/x": "path_invalid",
            REPO + "/actions/runs?status=queued": "path_invalid",
            REPO + "/actions/runs#x": "path_invalid",
            REPO + "/actions/runs\\x": "path_invalid",
            REPO + "/a b": "path_invalid",
            REPO + "/x@evil.example": "path_invalid",
            "": "path_invalid",
            None: "path_invalid",
        }
        for path, code in cases.items():
            self.assertEqual(check_path(path), code, path)

    def test_the_denylist_refuses_secret_access_list_and_log_paths_anywhere(self):
        self.assertEqual(DENIED_SEGMENTS, {"secrets", "variables", "hooks", "keys", "collaborators", "invitations",
                                           "logs", "zipball", "tarball", "zip"})
        for rest in ("actions/secrets", "actions/secrets/public-key", "actions/variables", "environments/production/secrets",
                     "environments/production/variables", "dependabot/secrets", "codespaces/secrets", "hooks", "hooks/1",
                     "keys", "collaborators", "collaborators/someone/permission", "invitations", "actions/runs/1/logs",
                     "actions/jobs/2/logs", "actions/runs/1/attempts/2/logs", "zipball/main", "tarball",
                     "actions/artifacts/3/zip", "actions/Secrets"):
            self.assertEqual(check_path(f"{REPO}/{rest}"), "path_denied", rest)

    def test_a_refused_path_makes_no_github_call(self):
        stub = Stub(Reply({}))
        reader = GitHubRepoReader(stub, token=TOKEN)
        for path in ("/repos/someone/else", REPO + "/actions/secrets", REPO + "/../x", "https://evil.example/x"):
            state, message, data = reader.get(path)
            self.assertNotEqual(state, "ok")
            self.assertEqual(data, {})
            self.assertNotIn(TOKEN, message)
        self.assertEqual(stub.calls, [])

    def test_query_keys_outside_the_allow_list_are_refused_without_a_call(self):
        stub = Stub(Reply({}))
        self.assertEqual(GitHubRepoReader(stub).get(REPO + "/actions/runs", {"access_token": "x"})[0], "query_invalid")
        self.assertEqual(stub.calls, [])


class Requests(unittest.TestCase):
    def test_it_is_a_plain_get_to_api_github_com_with_no_redirects_and_allowed_query(self):
        stub = Stub(Reply({"total_count": 0, "workflow_runs": []}))
        state, message, data = GitHubRepoReader(stub).get(REPO + "/actions/runs", {"status": "in_progress", "per_page": 5})
        self.assertEqual(state, "ok")
        url, kw = stub.calls[0]
        self.assertEqual(url, API + REPO + "/actions/runs")
        self.assertFalse(kw["allow_redirects"])
        self.assertEqual(kw["params"], {"status": "in_progress", "per_page": 5})
        self.assertNotIn("Authorization", kw["headers"])
        self.assertEqual(data["body"], {"total_count": 0, "workflow_runs": []})
        self.assertEqual(data["shape"], "complete")
        self.assertFalse(hasattr(GitHubRepoReader, "post") or hasattr(GitHubRepoReader, "request"))

    def test_the_token_is_sent_but_never_returned(self):
        stub = Stub(Reply({"id": 1, "name": "CI"}))
        reader = GitHubRepoReader(stub, token=TOKEN)
        state, message, data = reader.get(REPO + "/actions/workflows/1")
        self.assertEqual(stub.calls[0][1]["headers"]["Authorization"], "Bearer " + TOKEN)
        self.assertEqual(state, "ok")
        self.assertNotIn(TOKEN, message + json.dumps(data))
        echoed = GitHubRepoReader(Stub(Reply({"echo": TOKEN})), token=TOKEN).get(REPO)
        self.assertEqual(echoed[0], "unreadable")
        self.assertNotIn(TOKEN, json.dumps(echoed))

    def test_a_redirect_is_never_followed(self):
        stub = Stub(Reply(None, 302, {"Location": "https://pipelines.actions.githubusercontent.com/logs.zip"}, raw=b""))
        state, message, data = GitHubRepoReader(stub, token=TOKEN).get(REPO + "/actions/runs/1")
        self.assertEqual(state, "redirect_not_followed")
        self.assertEqual(len(stub.calls), 1)
        self.assertNotIn("githubusercontent", message)
        for code in (301, 307, 308):
            self.assertEqual(GitHubRepoReader(Stub(Reply(None, code, raw=b""))).get(REPO)[0], "redirect_not_followed")

    def test_errors_are_fixed_codes_never_raw_bodies(self):
        secret_body = {"message": "internal detail 7f3a", "documentation_url": "https://docs.github.com/x"}
        cases = [(Reply(secret_body, 401), "unauthenticated"), (Reply(secret_body, 403), "not_permitted"),
                 (Reply(secret_body, 404), "not_found"), (Reply(secret_body, 500), "unreadable"),
                 (Reply(secret_body, 422), "unreadable"), (Reply(None, 200, raw=b"<html>"), "unreadable")]
        for reply, code in cases:
            state, message, data = GitHubRepoReader(Stub(reply)).get(REPO + "/actions/runs")
            self.assertEqual(state, code)
            self.assertNotIn("7f3a", message + json.dumps(data))
        self.assertEqual(GitHubRepoReader(Stub(lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))).get(REPO)[0],
                         "unreachable")

    def test_rate_limit_says_when_it_resets_and_which_allowance(self):
        headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1794484800"}
        state, message, data = GitHubRepoReader(Stub(Reply({"message": "API rate limit exceeded"}, 403, headers))).get(REPO)
        self.assertEqual(state, "rate_limited")
        self.assertEqual(data["reset_at"], "2026-11-12T12:00:00Z")
        self.assertIn("2026-11-12T12:00:00Z", message)
        self.assertIn("without a credential", message)
        state, message, data = GitHubRepoReader(Stub(Reply({}, 429, {"Retry-After": "60"})), token=TOKEN).get(REPO)
        self.assertEqual((state, data["reset_at"], data["authenticated"]), ("rate_limited", None, True))
        self.assertIn("read token", message)
        self.assertNotIn(TOKEN, message)

    def test_an_oversized_download_is_refused(self):
        big = Reply(None, 200, raw=b"x" * 3_000_001)
        self.assertEqual(GitHubRepoReader(Stub(big)).get(REPO + "/actions/runs")[0], "too_large")


class Bounds(unittest.TestCase):
    def test_a_large_run_list_is_projected_under_the_cap_with_a_next_page_hint(self):
        payload = {"total_count": 4000, "workflow_runs": [run(i) for i in range(100)]}
        link = (f'<https://api.github.com/repositories/1/actions/runs?status=completed&per_page=100&page=2>; rel="next", '
                f'<https://api.github.com/repositories/1/actions/runs?per_page=100&page=40>; rel="last"')
        stub = Stub(Reply(payload, headers={"Link": link}))
        state, message, data = GitHubRepoReader(stub).get(REPO + "/actions/runs", {"status": "completed", "per_page": 100})
        self.assertEqual(state, "ok")
        envelope = json.dumps({"status": "done", "code": "read", "message": message, "data": data}, ensure_ascii=False)
        self.assertLess(len(json.dumps(data["body"], separators=(",", ":")).encode()), OUTPUT_BYTES + 1)
        self.assertLess(len(envelope.encode()), 16 * 1024)
        first = data["body"]["workflow_runs"][0]
        self.assertEqual(first["id"], 43332883700)
        for key in ("name", "status", "conclusion", "head_branch", "event", "created_at", "run_started_at",
                    "updated_at", "html_url"):
            self.assertIn(key, first)
        self.assertNotIn("repository", first)
        self.assertNotIn("head_commit", first)
        self.assertEqual(data["body"]["total_count"], 4000)
        self.assertLess(data["body"]["items_shown"], 100)
        self.assertEqual(data["next"], {"path": REPO + "/actions/runs", "status": "completed", "per_page": 100, "page": 2})
        self.assertIn("page=2", message)

    def test_a_jobs_list_keeps_each_steps_state(self):
        jobs = {"total_count": 3, "jobs": [{"id": j, "name": f"job {j}", "status": "in_progress", "conclusion": None,
                                            "started_at": "2026-11-12T10:00:00Z", "html_url": "https://github.com/x",
                                            "runner_name": "r" * 3000,
                                            "steps": [{"number": n, "name": f"step {n}", "status": "completed",
                                                       "conclusion": "success", "started_at": "t"} for n in range(20)]}
                                           for j in range(3)]}
        state, _, data = GitHubRepoReader(Stub(Reply(jobs))).get(REPO + "/actions/runs/1/jobs")
        self.assertEqual(data["shape"], "projected")
        step = data["body"]["jobs"][0]["steps"][3]
        self.assertEqual(step, {"number": 3, "name": "step 3", "status": "completed", "conclusion": "success"})

    def test_a_large_single_object_keeps_its_common_fields(self):
        state, _, data = GitHubRepoReader(Stub(Reply(run(7, pull_requests=[{"x": "p" * 9000}])))).get(REPO + "/actions/runs/7")
        self.assertEqual(data["shape"], "projected")
        self.assertEqual(data["body"]["display_title"], "mc-agent-v0.1.7")
        self.assertNotIn("repository", data["body"])

    def test_a_plain_list_payload_is_cut_to_the_cap(self):
        rows = [{"number": n, "title": "t" * 290, "state": "open", "html_url": "https://github.com/x", "body": "b" * 4000}
                for n in range(100)]
        state, message, data = GitHubRepoReader(Stub(Reply(rows))).get(REPO + "/pulls")
        self.assertLessEqual(len(json.dumps(data["body"], separators=(",", ":")).encode()), OUTPUT_BYTES)
        self.assertEqual(data["body"]["items_on_page"], 100)
        self.assertNotIn("body", data["body"]["items"][0])


class ToolContract(unittest.TestCase):
    def test_the_catalogue_takes_a_path_and_allow_listed_query_fields_only(self):
        ok = validate("minimoi_github_get", {"path": REPO + "/actions/runs", "status": "in_progress", "per_page": 10})
        self.assertEqual(ok["status"], "in_progress")
        for bad in ({}, {"path": REPO, "method": "POST"}, {"path": REPO, "body": "x"}, {"path": "https://x/y"},
                    {"path": REPO, "status": "deleted"}, {"path": REPO, "head_sha": "abc"}, {"path": REPO, "per_page": 101},
                    {"path": REPO, "branch": "a b"}, {"path": REPO, "query": {"page": 2}}, {"path": REPO + "?page=2"}):
            with self.assertRaises(ArgumentsRefused, msg=bad):
                validate("minimoi_github_get", bad)

    def test_both_catalogues_are_identical(self):
        self.assertEqual((ROOT / "core/mc_tools/tools.json").read_bytes(),
                         (ROOT / "services/mc_operations/plugins/minimoi-tools/tools.json").read_bytes())

    def test_it_is_a_tier_1_read_with_github_egress_in_both_policies(self):
        handler = P.HANDLERS["github_get_v1"]
        self.assertEqual((handler.tool, handler.tiers, handler.verify, handler.egress),
                         ("minimoi_github_get", frozenset({1}), None, "github_read"))
        self.assertIn("minimoi_github_get", read_only.READ_TOOLS)
        docs = {}
        for name in ("mc-policy.prod.json", "mc-policy.example.json"):
            raw = (ROOT / "services/mc_operations/policy" / name).read_bytes()
            doc = json.loads(raw)
            authority = json.dumps({"schema": P.AUTHORITY_SCHEMA, "revision": doc["revision"], "digest": P.digest_of(doc),
                                    "approved_by": "robert", "approved_at": "2026-11-12T00:00:00Z", "ref": "#543"}).encode()
            rule = P.compile_policy(raw, authority).rule_for("minimoi_github_get")
            self.assertEqual((rule.tier, rule.egress, rule.capability), (1, "github_read", "github.repo.read"), name)
            docs[name] = {r["handler"]: r for r in doc["rules"]}
        self.assertEqual(docs["mc-policy.prod.json"]["github_get_v1"], docs["mc-policy.example.json"]["github_get_v1"])

    def test_mc_s_container_and_the_plugin_offer_it(self):
        offered = json.loads((ROOT / "docker/mc-agent-container/openclaw.json").read_text())
        self.assertIn('"minimoi_github_get"', json.dumps(offered))
        plugin = json.loads((ROOT / "services/mc_operations/plugins/minimoi-tools/openclaw.plugin.json").read_text())
        self.assertIn("minimoi_github_get", plugin["contracts"]["tools"])
        self.assertEqual(plugin["configSchema"]["properties"]["tools"]["maxItems"], len(plugin["contracts"]["tools"]))

    def test_availability_follows_the_reader(self):
        from core.mc_tools import catalogue
        from core.mc_tools.service import ToolService
        s = ToolService(lambda: None, principal="fixture", environment="dev")
        self.assertEqual(availability.snapshot(s, catalogue.TOOLS, catalogue.TOOLS)["minimoi_github_get"]["state"], "disabled")
        s.github_api = object()
        self.assertEqual(availability.snapshot(s, catalogue.TOOLS, catalogue.TOOLS)["minimoi_github_get"]["state"], "enabled")


class Service(unittest.TestCase):
    def service(self, reply):
        from core.mc_tools import service as svc
        self.stub = Stub(reply)
        return svc, svc.ToolService(lambda: None, principal="robert", environment="dev",
                                    github_api=GitHubRepoReader(self.stub, token=TOKEN))

    def test_a_read_taints_the_turn_and_passes_the_query(self):
        svc, s = self.service(Reply({"total_count": 1, "workflow_runs": [run(1)]}))
        cursor = mock.MagicMock()
        with mock.patch.object(svc.ToolService, "_tx", lambda self: nullcontext(None)), \
                mock.patch.object(svc.ToolService, "_bind",
                                  lambda self, conn, caller: SimpleNamespace(ledger_id=1, generation=1, conversation_id="c-1")), \
                mock.patch.object(svc, "_cursor", lambda conn: cursor):
            done = s._github_get(validate("minimoi_github_get", {"path": REPO + "/actions/runs", "status": "queued"}), None, None)
            self.assertEqual((done["status"], done["code"]), ("done", "read"))
            self.assertEqual(self.stub.calls[0][1]["params"], {"status": "queued"})
            sql = " ".join(c.args[0] for c in cursor.__enter__.return_value.execute.call_args_list)
            self.assertIn("untrusted_read=true", sql)
            self.assertNotIn(TOKEN, json.dumps(done))
            cursor.reset_mock()
            refused = s._github_get(validate("minimoi_github_get", {"path": REPO + "/actions/secrets"}), None, None)
        self.assertEqual((refused["status"], refused["code"]), ("refused", "path_denied"))
        self.assertEqual(len(self.stub.calls), 1)
        cursor.__enter__.assert_not_called()

    def test_without_a_reader_the_tool_is_unavailable(self):
        from core.mc_tools import service as svc
        s = svc.ToolService(lambda: None, principal="robert", environment="dev")
        self.assertEqual(s._github_get({"path": REPO}, None, None)["code"], "tool_unavailable")


class Wiring(unittest.TestCase):
    def test_the_portal_uses_the_owner_only_read_token_or_reads_anonymously(self):
        import tempfile
        from minimoi_portal.guild_ui.mc_tools_wiring import github_api_reader
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "github-read-token")
            with open(path, "w") as f:
                f.write(TOKEN + "\n")
            os.chmod(path, 0o600)
            cfg = {"github_read": True, "repo": "", "read_token_file": path}
            reader = github_api_reader(cfg, http_get=lambda *a, **k: None)
            self.assertTrue(reader.authenticated)
            self.assertEqual(reader.repo, DEFAULT_REPO)
            self.assertNotIn(TOKEN, repr(vars(reader).get("repo")))
        self.assertFalse(github_api_reader({"github_read": True, "repo": ""}, http_get=lambda *a, **k: None).authenticated)
        self.assertIsNone(github_api_reader({"github_read": False, "repo": ""}))


if __name__ == "__main__":
    unittest.main()



class SecretBearingSegments(unittest.TestCase):
    def test_secret_and_variable_bearing_segments_are_refused(self):
        from core.mc_tools import github_api
        for seg in ("secret-scanning", "organization-variables", "dependabot-secrets"):
            with self.subTest(seg=seg):
                self.assertEqual(github_api.check_path(f"/repos/{DEFAULT_REPO}/{seg}/alerts"), "path_denied")
