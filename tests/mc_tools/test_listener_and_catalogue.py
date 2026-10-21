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


class FakeService:
    def __init__(self, answer=None, boom=False):
        self.calls, self.answer, self.boom = [], answer, boom

    def call(self, name, arguments, *, session_key, agent_id, observation=None):
        self.calls.append((name, arguments, session_key, agent_id, observation))
        if self.boom:
            raise RuntimeError("database went away")
        return self.answer or {"status": "done", "code": "listed", "message": "ok", "data": {}}


class ListenerTests(unittest.TestCase):
    def client(self, service):
        return create_app(service, token_sha256=hashlib.sha256(TOKEN.encode()).hexdigest()).test_client()

    def post(self, client, body, token=TOKEN, ctype="application/json", name="minimoi_guest_requests"):
        return client.post(f"/internal/mc/v1/tools/{name}", data=body if isinstance(body, (str, bytes)) else json.dumps(body),
                           headers={"Authorization": f"Bearer {token}", "Content-Type": ctype,
                                    "X-MC-Session": "agent:mc-chat:openai-user:guild-mc:" + "a" * 32,
                                    "X-MC-Agent": "mc-chat"})

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
                                          "mc-chat", None)])

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

    def test_session_key_parsing(self):
        user = "guild-mc:" + "0" * 32
        self.assertEqual(parse_session_key(f"agent:mc-chat:openai-user:{user}", "mc-chat", {"mc-chat"}), user)
        for key, agent in ((f"agent:main:openai-user:{user}", "main"), (f"agent:mc-chat:openai-user:{user}x", "mc-chat"),
                           (f"agent:mc-chat:openai-user:{user}", "mc-agent"), (None, "mc-chat"),
                           (f"agent:mc-chat:openai:{user}", "mc-chat")):
            with self.assertRaises(NotBound):
                parse_session_key(key, agent, {"mc-chat"})


if __name__ == "__main__":
    unittest.main()
