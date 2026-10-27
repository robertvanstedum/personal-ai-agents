"""The tool listener authenticates the plugin and passes only host context; the catalogue is strict and is the
same file the plugin ships."""
import hashlib
import json
import unittest
from pathlib import Path

from core.mc_tools import catalogue
from core.mc_tools.binding import NotBound, parse_session_key
from services.mc_tools.listener import create_app

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "t" * 40
PLUGIN = ROOT / "services/mc_operations/plugins/minimoi-tools"
RUN = "chatcmpl_12345678-1234-4234-8234-123456789abc"


class FakeService:
    def __init__(self, answer=None, boom=False):
        self.calls, self.answer, self.boom = [], answer, boom

    def call(self, name, arguments, *, session_key, agent_id, run_id=None, tool_call_id=None, instance_id=None,
             observation=None, offered_tools=None):
        self.calls.append((name, arguments, session_key, agent_id, run_id, tool_call_id, observation))
        self.instance = instance_id
        self.offered_tools = offered_tools
        if self.boom:
            raise RuntimeError("database went away")
        return self.answer or {"status": "done", "code": "listed", "message": "ok", "data": {}}


class ListenerTests(unittest.TestCase):
    def client(self, service):
        return create_app(service, token_sha256=hashlib.sha256(TOKEN.encode()).hexdigest()).test_client()

    def post(self, client, body, token=TOKEN, ctype="application/json", name="minimoi_guest_requests", run=True):
        headers = {"Authorization": f"Bearer {token}", "Content-Type": ctype,
                   "X-MC-Session": "agent:mc-chat:openai-user:guild-mc:" + "a" * 32, "X-MC-Agent": "mc-chat"}
        if run:
            headers |= {"X-MC-Run": RUN, "X-MC-Tool-Call": "call_1", "X-MC-Instance": "b" * 32}
        return client.post(f"/internal/mc/v1/tools/{name}", data=body if isinstance(body, (str, bytes)) else json.dumps(body),
                           headers=headers)

    def test_github_read_failure_is_retryable_without_weakening_mutation_unknown(self):
        service = FakeService(boom=True)
        client = self.client(service)
        read = self.post(client, {'arguments': {'number': 7}}, name='minimoi_github_issues').get_json()
        self.assertEqual(read['status'], 'unknown')
        self.assertIn('may retry this read', read['message'])
        self.assertNotIn('Do not repeat', read['message'])
        mutation = self.post(client, {'arguments': {}}, name='minimoi_data_repair_ticket').get_json()
        self.assertEqual(mutation['status'], 'unknown')
        self.assertIn('Do not repeat', mutation['message'])
        self.assertEqual(len(service.calls), 2)

    def test_credential_is_required(self):
        service = FakeService()
        c = self.client(service)
        for token in ("", "x" * 40, TOKEN + "x"):
            r = self.post(c, {"arguments": {}}, token=token)
            self.assertEqual((r.status_code, r.get_json()), (401, {"error": "tool_unauthorized"}))
        self.assertEqual(service.calls, [])

    def test_body_shape_is_strict(self):
        service = FakeService()
        c = self.client(service)
        self.assertEqual(self.post(c, {"arguments": {}}, ctype="text/plain").status_code, 415)
        for body in ({}, {"arguments": {}, "principal": "robert"}, [], '{"arguments":{},"arguments":{}}', "{"):
            r = self.post(c, body)
            self.assertEqual((r.status_code, r.get_json()["error"]), (400, "tool_request_invalid"), body)
        self.assertEqual(self.post(c, {"arguments": {"x": "y" * 20000}}).status_code, 413)
        self.assertEqual(service.calls, [])

    def test_host_context_comes_from_headers_and_reaches_the_service(self):
        service = FakeService()
        r = self.post(self.client(service), {"arguments": {}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["status"], "done")
        self.assertEqual(service.calls, [("minimoi_guest_requests", {}, "agent:mc-chat:openai-user:guild-mc:" + "a" * 32,
                                          "mc-chat", RUN, "call_1", None)])

    def test_the_instance_header_reaches_the_service(self):
        service = FakeService()
        self.post(self.client(service), {"arguments": {}})
        self.assertEqual(service.instance, "b" * 32)

    def test_plugin_description_metadata_is_bounded_and_separate_from_arguments(self):
        service = FakeService()
        c = self.client(service)
        body = {"arguments": {}, "offered_tools": ["minimoi_describe_house"]}
        self.assertEqual(self.post(c, body, name="minimoi_describe_house").status_code, 200)
        self.assertEqual(service.offered_tools, ["minimoi_describe_house"])
        self.assertEqual(service.calls[0][1], {})
        for bad in (None, "all", ["unknown_tool"], ["minimoi_describe_house"] * 2, [3]):
            self.assertEqual(self.post(c, {**body, "offered_tools": bad}, name="minimoi_describe_house").status_code, 400)
        self.assertEqual(self.post(c, body, name="minimoi_guest_requests").status_code, 400)

    def test_the_plugin_announces_its_process_with_the_same_credential(self):
        class Registry(FakeService):
            def register_instance(self, instance):
                self.registered = instance
                return True
        service, c = Registry(), None
        c = self.client(service)
        ok = c.post("/internal/mc/v1/instance", json={"instance": "c" * 32},
                    headers={"Authorization": f"Bearer {TOKEN}"})
        self.assertEqual((ok.status_code, service.registered), (200, "c" * 32))
        class Retired(FakeService):
            def register_instance(self, instance):
                from core.minimoi_actions.context import ActionContractError
                raise ActionContractError("tool_instance_retired")
        refused = self.client(Retired()).post("/internal/mc/v1/instance", json={"instance": "c" * 32},
                                             headers={"Authorization": f"Bearer {TOKEN}"})
        self.assertEqual((refused.status_code, refused.get_json()["error"]), (409, "tool_instance_retired"))
        for headers, body, code in (({"Authorization": "Bearer " + "x" * 40}, {"instance": "c" * 32}, 401),
                                    ({"Authorization": f"Bearer {TOKEN}"}, {"instance": "c" * 32, "x": 1}, 400),
                                    ({"Authorization": f"Bearer {TOKEN}"}, {"instance": 5}, 400)):
            self.assertEqual(c.post("/internal/mc/v1/instance", json=body, headers=headers).status_code, code)

    def test_the_handoff_route_has_its_own_credential_and_is_off_unless_configured(self):
        import hashlib as h
        cos = "c" * 40
        received = []

        def handoff(body):
            received.append(body)
            return {"transfer_id": "t", "state": "delivered"}
        app = create_app(FakeService(), token_sha256=h.sha256(TOKEN.encode()).hexdigest(), handoff=handoff,
                         handoff_token_sha256=h.sha256(cos.encode()).hexdigest()).test_client()
        ok = app.post("/internal/mc/v1/handoffs", json={"x": 1}, headers={"Authorization": f"Bearer {cos}"})
        self.assertEqual((ok.status_code, ok.get_json()["state"]), (200, "delivered"))
        mc = app.post("/internal/mc/v1/handoffs", json={"x": 1}, headers={"Authorization": f"Bearer {TOKEN}"})
        self.assertEqual(mc.status_code, 401)                           # MC's executor token cannot hand over
        self.assertEqual(len(received), 1)
        def unknown(body):
            from core.mc_tools.handoffs import HandoffUnknown
            raise HandoffUnknown("handoff_unconfirmed")
        u = create_app(FakeService(), token_sha256=h.sha256(TOKEN.encode()).hexdigest(), handoff=unknown,
                       handoff_token_sha256=h.sha256(cos.encode()).hexdigest()).test_client()
        r = u.post("/internal/mc/v1/handoffs", json={"x": 1}, headers={"Authorization": f"Bearer {cos}"})
        self.assertEqual((r.status_code, r.get_json()), (503, {"state": "unknown"}))     # never "refused" (H1)
        off = self.client(FakeService()).post("/internal/mc/v1/handoffs", json={},
                                              headers={"Authorization": f"Bearer {cos}"})
        self.assertEqual(off.status_code, 404)
        with self.assertRaises(ValueError):                              # the same credential for both is refused
            create_app(FakeService(), token_sha256=h.sha256(TOKEN.encode()).hexdigest(), handoff=handoff,
                       handoff_token_sha256=h.sha256(TOKEN.encode()).hexdigest())

    def test_missing_run_headers_reach_the_service_as_none_so_it_refuses(self):
        service = FakeService()
        self.post(self.client(service), {"arguments": {}}, run=False)
        self.assertEqual(service.calls[0][4:6], (None, None))

    def test_a_service_failure_is_unknown_never_done(self):
        r = self.post(self.client(FakeService(boom=True)), {"arguments": {}})
        self.assertEqual((r.status_code, r.get_json()["status"]), (200, "unknown"))


class CatalogueTests(unittest.TestCase):
    def test_plugin_ships_the_same_catalogue_and_declares_every_tool(self):
        self.assertEqual((PLUGIN / "tools.json").read_bytes(), (ROOT / "core/mc_tools/tools.json").read_bytes())
        manifest = json.loads((PLUGIN / "openclaw.plugin.json").read_text())
        self.assertEqual(manifest["contracts"]["tools"], list(catalogue.TOOLS))

    def test_every_schema_is_closed_and_names_no_authority(self):
        for name, tool in catalogue.TOOLS.items():
            p = tool["parameters"]
            self.assertIs(p["additionalProperties"], False, name)
            for key in p["properties"]:
                self.assertNotIn(key, ("principal", "owner", "turn", "grant", "approved", "session", "operation_id"))

    def test_validation(self):
        v = catalogue.validate
        self.assertEqual(v("minimoi_access_invite", {"name": "Sofia", "email": "s@x.test", "days": 30})["days"], 30)
        for args in ({"name": "Sofia", "email": "s@x.test", "days": 31}, {"name": "Sofia", "email": "nope", "days": 3},
                     {"name": "Sofia", "email": "s@x.test", "days": True}, {"name": " Sofia", "email": "s@x.test", "days": 3},
                     {"name": "So​fia", "email": "s@x.test", "days": 3},
                     {"name": "Sofia", "email": "s@x.test", "days": 3, "app": "german"}):
            with self.assertRaises(catalogue.ArgumentsRefused, msg=args):
                v("minimoi_access_invite", args)
        with self.assertRaises(catalogue.ArgumentsRefused):
            v("minimoi_delegate_research", {"agent": "claude", "title": "abc"})
        with self.assertRaises(catalogue.ArgumentsRefused):
            v("host_health", {})
        self.assertEqual(v("minimoi_data_repair_ticket", {"refs": ["guild:3", "guild:5"], "change": "reject"})["refs"],
                         ["guild:3", "guild:5"])
        for args in ({"refs": ["guild:3", "guild:3"], "change": "reject"}, {"refs": [], "change": "reject"},
                     {"refs": ["guild:%d" % i for i in range(1, 22)], "change": "reject"},
                     {"refs": [3], "change": "reject"}, {"refs": ["ana@x.test"], "change": "reject"}):
            with self.assertRaises(catalogue.ArgumentsRefused, msg=args):
                v("minimoi_data_repair_ticket", args)

    def test_an_issue_body_may_span_lines_and_nothing_else_may(self):
        """#395: every issue MC proposed in prod was refused for its line breaks."""
        v = catalogue.validate
        body = "## Symptom\n\nThe text flickered.\n\n- one\n\t- two"
        self.assertEqual(v("minimoi_issue_create", {"title": "MC chat flickers", "body": body})["body"], body)
        self.assertEqual(v("minimoi_issue_update", {"number": 402, "body": body})["body"], body)
        self.assertEqual(v("minimoi_issue_comment", {"number": 402, "summary": body, "refs": ["guild:3"]})["summary"], body)
        for args in ({"title": "MC chat\nflickers", "body": body},          # a title stays one line
                     {"title": "MC chat flickers", "body": "a\rb and more"},  # other control characters still refuse
                     {"title": "MC chat flickers", "body": "a\x00b and more"},
                     {"title": "MC chat flickers", "body": body + "\n"}):     # untrimmed still refuses
            with self.assertRaises(catalogue.ArgumentsRefused, msg=repr(args)):
                v("minimoi_issue_create", args)

    def test_session_key_parsing(self):
        user = "guild-mc:" + "0" * 32
        self.assertEqual(parse_session_key(f"agent:mc-chat:openai-user:{user}", "mc-chat", {"mc-chat"}), user)
        for key, agent in ((f"agent:main:openai-user:{user}", "main"), (f"agent:mc-chat:openai-user:{user}x", "mc-chat"),
                           (f"agent:mc-chat:openai-user:{user}", "mc-agent"), (None, "mc-chat"),
                           (f"agent:mc-chat:openai:{user}", "mc-chat")):
            with self.assertRaises(NotBound):
                parse_session_key(key, agent, {"mc-chat"})


class ParseTests(unittest.TestCase):
    def test_owner_replies(self):
        from core.mc_tools.proposals import parse
        self.assertEqual(parse("yes"), ("confirm", None))
        self.assertEqual(parse("Yes P-7K3M."), ("confirm", "P-7K3M"))
        self.assertEqual(parse("confirm p7k3m"), ("confirm", "P-7K3M"))
        self.assertEqual(parse("no P-7K3M"), ("cancel", "P-7K3M"))
        self.assertEqual(parse("No thanks"), ("cancel", None))
        for text in ("yes, but change it", "yes P-7K3M and also P-AAAA", "maybe", "", None, "y" * 70):
            self.assertIsNone(parse(text), text)


if __name__ == "__main__":
    unittest.main()
