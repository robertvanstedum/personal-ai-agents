"""MC tool policy v0 (spec v1.2 §8.1): strict file, code-owned handlers and floors, an approval record for the exact
file, and no permissive fallback. Pure unit tests; no database."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from core.mc_tools import policy as P

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = json.loads((ROOT / "services/mc_operations/policy/mc-policy.example.json").read_text())


def authority(doc, **change):
    return {"schema": P.AUTHORITY_SCHEMA, "revision": doc["revision"], "digest": P.digest_of(doc),
            "approved_by": "robert", "approved_at": "2026-10-22T18:00:00-05:00", "ref": "change MC-POLICY-1", **change}


def compiled(doc, auth=None):
    return P.compile_policy(json.dumps(doc).encode(), json.dumps(auth or authority(doc)).encode())


def rule(doc, tool):
    return next(r for r in doc["rules"] if P.HANDLERS[r["handler"]].tool == tool)


class CompileTests(unittest.TestCase):
    def test_the_example_compiles_and_names_every_tool_at_its_floor(self):
        c = compiled(EXAMPLE)
        self.assertEqual(c.version, f"mc-policy:dev-example-1:{P.digest_of(EXAMPLE)}")
        self.assertEqual(set(c.tools), {h.tool for h in P.HANDLERS.values()})
        self.assertEqual(c.rule_for("minimoi_guest_request_reject").tier, 3)
        self.assertEqual(c.rule_for("minimoi_host_health").tier, 1)

    def test_formatting_does_not_change_the_digest_content_does(self):
        spaced = json.dumps(EXAMPLE, indent=7).encode()
        self.assertEqual(P.compile_policy(spaced, json.dumps(authority(EXAMPLE)).encode()).digest, P.digest_of(EXAMPLE))
        edited = copy.deepcopy(EXAMPLE)
        rule(edited, "minimoi_host_health")["max_operations_per_turn"] = 11
        with self.assertRaisesRegex(P.PolicyInvalid, "policy_unapproved"):  # editing a row approves nothing
            compiled(edited, authority(EXAMPLE))

    def test_no_tier_below_the_code_floor(self):
        for tool, tier in (("minimoi_guest_request_reject", 2), ("minimoi_guest_request_reject", 1),
                           ("minimoi_issue_comment", 1), ("minimoi_access_invite", 2),
                           ("minimoi_delegate_research", 2), ("minimoi_host_health", 3)):
            doc = copy.deepcopy(EXAMPLE)
            r = rule(doc, tool)
            r["tier"], r["approval"] = tier, P._APPROVAL_FOR_TIER[tier]
            with self.assertRaisesRegex(P.PolicyInvalid, "policy_below_floor", msg=(tool, tier)):
                compiled(doc)

    def test_strict_shape(self):
        cases = {
            "policy_rule_fields": lambda d: rule(d, "minimoi_host_health").update(command="rm -rf /"),
            "policy_unknown_handler": lambda d: rule(d, "minimoi_host_health").update(handler="os.system"),
            "policy_rule_mismatch": lambda d: rule(d, "minimoi_host_health").update(capability="guest.list"),
            "policy_verifier_mismatch": lambda d: rule(d, "minimoi_issue_comment").update(verify=None),
            "policy_quota_invalid": lambda d: rule(d, "minimoi_host_health").update(max_operations_per_turn=0),
            "policy_revision_invalid": lambda d: d.update(revision="Robert approved this"),
            "policy_schema": lambda d: d.update(extra=True),
        }
        for code, change in cases.items():
            doc = copy.deepcopy(EXAMPLE)
            change(doc)
            with self.assertRaisesRegex(P.PolicyInvalid, code, msg=code):
                compiled(doc)
        doc = copy.deepcopy(EXAMPLE)
        doc["rules"].append(copy.deepcopy(doc["rules"][0]))
        with self.assertRaisesRegex(P.PolicyInvalid, "policy_duplicate_rule"):
            compiled(doc)
        raw = json.dumps(EXAMPLE)[:-1] + ', "revision": "other"}'
        with self.assertRaisesRegex(P.PolicyInvalid, "policy_duplicate_key"):
            P.compile_policy(raw.encode(), json.dumps(authority(EXAMPLE)).encode())

    def test_the_authority_record_must_name_this_exact_file(self):
        for change, code in (({"digest": "0" * 64}, "policy_unapproved"), ({"revision": "other"}, "policy_unapproved"),
                             ({"approved_by": " "}, "policy_authority_invalid"),
                             ({"schema": "something-else"}, "policy_authority_invalid")):
            with self.assertRaisesRegex(P.PolicyInvalid, code, msg=change):
                compiled(EXAMPLE, authority(EXAMPLE, **change))
        with self.assertRaisesRegex(P.PolicyInvalid, "policy_authority_invalid"):
            compiled(EXAMPLE, {k: v for k, v in authority(EXAMPLE).items() if k != "ref"})


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.policy_path, self.auth_path = (os.path.join(self.dir.name, n) for n in ("p.json", "a.json"))

    def tearDown(self):
        self.dir.cleanup()

    def install(self, doc, auth=None):
        for path, value in ((self.policy_path, doc), (self.auth_path, auth or authority(doc))):
            tmp = path + ".tmp"
            Path(tmp).write_text(json.dumps(value))
            os.replace(tmp, path)

    def test_a_change_is_swapped_in_whole_and_an_invalid_one_never_leaves_the_old_in_force(self):
        source = P.PolicySource(self.policy_path, self.auth_path)
        self.assertEqual(source.current(), (None, "policy_unreadable"))
        self.install(EXAMPLE)
        first, problem = source.current()
        self.assertIsNone(problem)
        newer = copy.deepcopy(EXAMPLE)
        newer["revision"] = "dev-example-2"
        self.install(newer)
        second, _ = source.current()
        self.assertNotEqual(first.version, second.version)
        broken = copy.deepcopy(newer)
        broken["revision"] = "dev-example-3"
        self.install(broken, authority(newer))
        self.assertEqual(source.current(), (None, "policy_unapproved"))   # not the previous policy: nothing
        self.assertEqual(P.PolicySource("relative.json", self.auth_path).current(), (None, "policy_path_invalid"))

    def test_the_operator_check(self):
        self.install(EXAMPLE)
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(P._main(["check", self.policy_path, self.auth_path]), 0)
        self.assertIn("minimoi_guest_request_reject", json.loads(out.getvalue())["tools"])
        with contextlib.redirect_stdout(io.StringIO()) as bad:
            self.install(EXAMPLE, authority(EXAMPLE, digest="1" * 64))
            self.assertEqual(P._main(["check", self.policy_path, self.auth_path]), 1)
        self.assertIn("INVALID: policy_unapproved", bad.getvalue())


if __name__ == "__main__":
    unittest.main()
