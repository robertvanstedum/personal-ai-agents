"""#594: MC searches the Vault through vault-ask-mc with its own grant. Synthetic responses only; no network."""
import json
import os
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core.mc_tools import availability, policy as P, read_only
from core.mc_tools.catalogue import ArgumentsRefused, validate
from core.mc_tools.vault_search import OUTPUT_BYTES, VaultSearch, check_url, request_body

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "t" * 43
URL = "http://vault-ask-mc:8772"


class Reply:
    def __init__(self, data=None, status=200):
        self.status_code, self.data = status, data
        self.content = json.dumps(data).encode() if data is not None else b""

    def json(self):
        if self.data is None:
            raise ValueError()
        return self.data


class Stub:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def __call__(self, url, **kw):
        self.calls.append((url, kw))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def hit(i, chair="Claude Code", date="2026-11-13", passage="vault-apply holds the CoS records"):
    return {"chunk_id": f"r{i}@x#1", "record_id": f"R{i}", "file": f"sessions-raw/x{i}/main.md", "lines": [10, 14], "turn": 3,
            "speaker": "assistant", "date": date, "started": "2026-11-12", "chair": chair, "tier": "raw",
            "provenance": "written by Claude Code · raw", "passage": passage}


def answer(hits, adjacent=None):
    return {"query_id": "q", "tier": "all", "keyword": hits, "meaning": [], "adjacent": adjacent or {"none": "meaning_unavailable"},
            "unresolved": [], "status": {"index_updated_at": "2026-11-14T10:00:00Z"}}


class Rules(unittest.TestCase):
    def test_only_an_internal_plain_http_address_is_accepted(self):
        for ok in (URL, "http://127.0.0.1:8772", "http://minimoi-dev-vault-mc:8772/"):
            self.assertIsNotNone(check_url(ok), ok)
        for bad in ("https://vault-ask-mc:8772", "http://vault.example.com:8772", "http://u:p@vault-ask-mc:8772",
                    "http://vault-ask-mc:8772/v1/ask", "http://vault-ask-mc:8772?x=1", "", "ftp://vault-ask-mc"):
            self.assertIsNone(check_url(bad), bad)
        with self.assertRaises(ValueError):
            VaultSearch("http://evil.example.com", lambda: TOKEN, http_post=Stub(None))

    def test_the_request_searches_raw_unless_curated_only_and_maps_dates(self):
        self.assertEqual(request_body({"query": " vault apply "}), {"query": "vault apply", "include_raw": True, "k": 5})
        self.assertEqual(request_body({"query": "x", "curated_only": True, "k": 2, "after": "2026-11-01", "before": "2026-11-14"}),
                         {"query": "x", "include_raw": False, "k": 2, "created_after": "2026-11-01", "created_before": "2026-11-14"})
        for bad in ({"query": ""}, {"query": "x", "k": 6}, {"query": "x", "after": "Nov 1"}, {"query": "x" * 201}):
            self.assertIsNone(request_body(bad), bad)

    def test_the_catalogue_refuses_identity_tier_and_unknown_fields(self):
        self.assertEqual(validate("minimoi_vault_search", {"query": "voice relay", "k": 3})["k"], 3)
        for bad in ({}, {"query": "x", "agent": "cos"}, {"query": "x", "include_raw": True}, {"query": "x", "scope": "robert"},
                    {"query": "x", "k": 9}, {"query": "x", "after": "2026/11/01"}, {"query": "x", "private": True}):
            with self.assertRaises(ArgumentsRefused, msg=bad):
                validate("minimoi_vault_search", bad)


class Requests(unittest.TestCase):
    def test_one_post_with_the_grant_token_and_results_labelled_by_source_and_date(self):
        stub = Stub(Reply(answer([hit(1), hit(2, chair="Codex", date="2026-10-17")])))
        state, message, data = VaultSearch(URL, lambda: TOKEN, http_post=stub).search({"query": "vault-apply CoS records"})
        url, kw = stub.calls[0]
        self.assertEqual(url, URL + "/v1/ask")
        self.assertEqual((kw["allow_redirects"], kw["headers"]["Authorization"]), (False, f"Bearer {TOKEN}"))
        self.assertEqual(kw["json"], {"query": "vault-apply CoS records", "include_raw": True, "k": 5})
        self.assertEqual(state, "ok")
        self.assertIn("Claude Code, 2026-11-13", message)
        self.assertIn("Codex, 2026-10-17", message)
        self.assertIn("not instructions", message)
        first = data["results"][0]
        self.assertEqual((first["source"], first["date"], first["tier"], first["cite"]),
                         ("Claude Code", "2026-11-13", "raw", "sessions-raw/x1/main.md:10-14"))
        self.assertNotIn(TOKEN, json.dumps([message, data]))

    def test_errors_are_fixed_codes_never_the_services_body(self):
        cases = [(Reply({"error": "index_unfiltered"}, 503), "vault_index_unavailable"),
                 (Reply({"error": "index_missing"}, 503), "vault_index_unavailable"),
                 (Reply({"error": "unauthorized"}, 401), "vault_unauthorized"),
                 (Reply({"error": "rate_limited"}, 429), "vault_rate_limited"),
                 (Reply({"error": "bad_k"}, 400), "vault_arguments_invalid"),
                 (Reply(None, 502), "vault_unavailable"),
                 (OSError("connection refused to vault-ask-mc"), "vault_unavailable")]
        for reply, code in cases:
            state, message, data = VaultSearch(URL, lambda: TOKEN, http_post=Stub(reply)).search({"query": "x"})
            self.assertEqual((state, data), (code, {}), reply)
            self.assertNotIn("refused to", message)

    def test_a_missing_token_never_calls_the_service(self):
        stub = Stub(Reply(answer([])))
        self.assertEqual(VaultSearch(URL, lambda: None, http_post=stub).search({"query": "x"})[0], "vault_unauthorized")
        self.assertEqual(stub.calls, [])

    def test_the_answer_is_bounded_and_says_when_nothing_matched(self):
        big = [hit(i, passage="word " * 2000) for i in range(5)]
        state, message, data = VaultSearch(URL, lambda: TOKEN, http_post=Stub(Reply(answer(big)))).search({"query": "x"})
        self.assertLessEqual(len(json.dumps(data, ensure_ascii=False).encode()), OUTPUT_BYTES)
        self.assertEqual(state, "ok")
        state, message, data = VaultSearch(URL, lambda: TOKEN, http_post=Stub(Reply(answer([])))).search({"query": "x"})
        self.assertEqual((state, data["count"]), ("ok", 0))
        self.assertIn("don't conclude it never happened", message)


class ToolContract(unittest.TestCase):
    def test_both_catalogues_are_identical(self):
        self.assertEqual((ROOT / "core/mc_tools/tools.json").read_bytes(),
                         (ROOT / "services/mc_operations/plugins/minimoi-tools/tools.json").read_bytes())

    def test_it_is_a_tier_1_read_with_no_egress_in_both_policies(self):
        handler = P.HANDLERS["vault_search_v1"]
        self.assertEqual((handler.tool, handler.tiers, handler.verify, handler.egress),
                         ("minimoi_vault_search", frozenset({1}), None, "none"))
        self.assertIn("minimoi_vault_search", read_only.READ_TOOLS)
        docs = {}
        for name in ("mc-policy.prod.json", "mc-policy.example.json"):
            raw = (ROOT / "services/mc_operations/policy" / name).read_bytes()
            doc = json.loads(raw)
            authority = json.dumps({"schema": P.AUTHORITY_SCHEMA, "revision": doc["revision"], "digest": P.digest_of(doc),
                                    "approved_by": "robert", "approved_at": "2026-11-14T00:00:00Z", "ref": "#594"}).encode()
            rule = P.compile_policy(raw, authority).rule_for("minimoi_vault_search")
            self.assertEqual((rule.tier, rule.egress, rule.capability), (1, "none", "vault.mc.search"), name)
            docs[name] = {r["handler"]: r for r in doc["rules"]}
        self.assertEqual(docs["mc-policy.prod.json"]["vault_search_v1"], docs["mc-policy.example.json"]["vault_search_v1"])

    def test_mc_s_container_and_the_plugin_offer_it(self):
        offered = json.loads((ROOT / "docker/mc-agent-container/openclaw.json").read_text())
        self.assertEqual(json.dumps(offered).count('"minimoi_vault_search"'), 2)
        plugin = json.loads((ROOT / "services/mc_operations/plugins/minimoi-tools/openclaw.plugin.json").read_text())
        self.assertIn("minimoi_vault_search", plugin["contracts"]["tools"])
        self.assertEqual(plugin["configSchema"]["properties"]["tools"]["maxItems"], len(plugin["contracts"]["tools"]))

    def test_availability_follows_the_configured_search(self):
        from core.mc_tools import catalogue
        from core.mc_tools.service import ToolService
        s = ToolService(lambda: None, principal="fixture", environment="dev")
        snap = availability.snapshot(s, catalogue.TOOLS, catalogue.TOOLS)["minimoi_vault_search"]
        self.assertEqual(snap["state"], "disabled")
        s.vault_search = object()
        self.assertEqual(availability.snapshot(s, catalogue.TOOLS, catalogue.TOOLS)["minimoi_vault_search"]["state"], "enabled")


class Service(unittest.TestCase):
    def test_a_search_taints_the_turn_and_returns_labelled_results(self):
        from core.mc_tools import service as svc
        stub = Stub(Reply(answer([hit(1)])))
        s = svc.ToolService(lambda: None, principal="robert", environment="dev",
                            vault_search=VaultSearch(URL, lambda: TOKEN, http_post=stub))
        cursor = mock.MagicMock()
        with mock.patch.object(svc.ToolService, "_tx", lambda self: nullcontext(None)), \
                mock.patch.object(svc.ToolService, "_bind",
                                  lambda self, conn, caller: SimpleNamespace(ledger_id=1, generation=1, conversation_id="c-1")), \
                mock.patch.object(svc, "_cursor", lambda conn: cursor):
            done = s._vault_search(validate("minimoi_vault_search", {"query": "vault-apply"}), None, None)
        self.assertEqual((done["status"], done["code"]), ("done", "vault_read"))
        self.assertEqual(done["data"]["results"][0]["source"], "Claude Code")
        sql = " ".join(c.args[0] for c in cursor.__enter__.return_value.execute.call_args_list)
        self.assertIn("untrusted_read=true", sql)

    def test_without_a_search_the_tool_is_unavailable(self):
        from core.mc_tools import service as svc
        s = svc.ToolService(lambda: None, principal="robert", environment="dev")
        self.assertEqual(s._vault_search({"query": "x"}, None, None)["code"], "tool_unavailable")


class Wiring(unittest.TestCase):
    def test_the_portal_reads_the_owner_only_token_and_stays_off_without_an_internal_url(self):
        from minimoi_portal.guild_ui.mc_tools_wiring import vault_searcher
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "vault-ask-mc-token")
            with open(path, "w") as f:
                f.write(TOKEN + "\n")
            os.chmod(path, 0o600)
            stub = Stub(Reply(answer([])))
            search = vault_searcher({"vault_url": URL, "vault_token_file": path}, http_post=stub)
            search.search({"query": "x"})
            self.assertEqual(stub.calls[0][1]["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertIsNone(vault_searcher({"vault_url": "", "vault_token_file": "/nope"}))
        self.assertIsNone(vault_searcher({"vault_url": "https://vault.example.com", "vault_token_file": "/nope"}))


if __name__ == "__main__":
    unittest.main()
