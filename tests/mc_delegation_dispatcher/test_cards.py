"""Cards without a database (host runtime spec v0.5 R4.3-c, R4.3-d, R4.3-f, R6.2, R6.2-b, R6.8, R7.4-b): the class
catalogue as rows, compiling a card into its enumerated steps and their template digests, cross-vendor steps off the
card, inputs from the authority's records only, the step that follows, the card grant's lifetime, recompiling from
the authority's record, and the pinned A2A mapping note. The PostgreSQL half is test_cards_postgres.py."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import copy
import json
from pathlib import Path
import sys
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.mc_delegation_dispatcher import cards as C  # noqa: E402
from core.mc_delegation_dispatcher import store as S  # noqa: E402
from core.minimoi_actions import grants as G  # noqa: E402

CAT = C.Catalogue.load()
NOW = datetime(2026, 10, 28, 20, 0, tzinfo=timezone.utc)


def sha(text):
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()


def art(kind, task):
    return {"kind": kind, "ref": f"{task}.{kind}", "sha256": sha(task + kind), "bytes": 10, "author": "fakework",
            "review_status": "unreviewed"}


def card(**over):
    doc = {"schema": "minimoi.card.v1", "card_id": "C-17", "class": "build", "class_version": 1,
           "repository": "personal-ai-agents", "ref": "a" * 40,
           "workers": {"builder": {"actor": "fakework", "route": "fakework", "amount": 0.25, "cost_cap_usd": 1},
                       "reviewer": {"actor": "fakework", "route": "fakework", "amount": 0.25, "cost_cap_usd": 1}},
           "briefs": {"build": "fakework/1\ncheckpoint s1\ncheckpoint s2\nresult ok",
                      "review": "fakework/1\nfindings\nresult ok", "fix": "fakework/1\nresult ok"},
           "attempts": 2, "rounds": {"fix": 1}, "step_deadline_s": 3600}
    doc.update(over)
    return doc


def record(compiled):
    """What admit_card writes into card.admitted, as the database hands it back (JSON)."""
    return json.loads(json.dumps({"card": compiled.card, "card_digest": compiled.digest, "class": compiled.class_name,
                                  "class_row": compiled.class_row, "families": compiled.families,
                                  "class_digest": compiled.class_digest,
                                  "steps": [{"step": s.name, "digest": s.digest} for s in compiled.steps]}))


class Catalogue(unittest.TestCase):
    def test_the_shipped_catalogue_is_rows_and_names_the_build_class(self):
        self.assertEqual(set(CAT.classes), {"build"})
        self.assertEqual([s["kind"] for s in CAT.classes["build"]["steps"]], ["build", "review", "fix"])
        self.assertEqual(set(CAT.families) & set(S.V2_ACTORS), set(S.V2_ACTORS))
        self.assertEqual(G.CARD_CAPABILITY, C.CARD_CAPABILITY)
        rows = C.grant_rows()
        self.assertEqual((rows[C.CARD_CAPABILITY].exact_approval_required, rows[C.CARD_CAPABILITY].zero_spend),
                         (True, True))

    def test_a_new_card_class_is_a_row_not_code(self):
        doc = json.loads(C.CLASSES_PATH.read_text())
        doc["classes"]["research"] = {"version": 1, "roles": ["reader"], "steps": [
            {"kind": "read", "role": "reader", "scope": "research", "brief": "Read only. {brief}",
             "required_capabilities": ["host:mac-local"], "inputs": "card", "after": None, "repeat": False}]}
        cat = C.Catalogue.parse(doc)
        compiled = cat.compile({"schema": "minimoi.card.v1", "card_id": "R-1", "class": "research",
                                "class_version": 1, "repository": "personal-ai-agents", "ref": "b" * 40,
                                "workers": {"reader": {"actor": "fakework", "route": "fakework", "amount": 1,
                                                       "cost_cap_usd": 1}},
                                "briefs": {"read": "Summarise the spec."}, "attempts": 1, "step_deadline_s": 600})
        self.assertEqual([(s.name, s.brief, s.required_capabilities) for s in compiled.steps],
                         [("read", "Read only. Summarise the spec.", ("host:mac-local",))])

    def test_a_malformed_class_row_is_refused(self):
        row = CAT.classes["build"]
        cases = {"card_class_inputs_invalid": lambda r: r["steps"][1].update(inputs="card"),
                 "card_class_steps_invalid": lambda r: r["steps"][0].update(repeat=True),
                 "card_class_brief_invalid": lambda r: r["steps"][0].update(brief="no placeholder"),
                 "card_class_after_invalid": lambda r: r["steps"][0].update(after="findings")}
        for code, change in cases.items():
            bad = copy.deepcopy(row)
            change(bad)
            with self.assertRaisesRegex(C.CardRefused, code):
                C.check_class("build", bad)
        bad = copy.deepcopy(row)
        bad["steps"][2]["role"] = "nobody"
        with self.assertRaises(C.CardRefused):
            C.check_class("build", bad)


class Compile(unittest.TestCase):
    def test_the_reference_card_enumerates_build_review_and_one_fix(self):
        c = CAT.compile(card())
        self.assertEqual([(s.index, s.name, s.kind, s.scope, s.on_card) for s in c.steps],
                         [(0, "build", "build", "build", True), (1, "review", "review", "research", True),
                          (2, "fix-1", "fix", "build", True)])
        self.assertEqual(len(set(c.grant_digests)), 3)
        self.assertEqual(c.card["workers"]["builder"], {"actor": "fakework", "route": "fakework", "amount": "0.2500",
                                                         "cost_cap_usd": "1.00"})
        self.assertEqual(c.card["lifetime_s"], None)
        self.assertEqual(CAT.compile(card()).digest, c.digest)                  # pure: same card, same digests
        self.assertEqual(CAT.compile(card(lifetime_s=None, input_artifacts=[], paths_allowed=[])).digest, c.digest)
        with self.assertRaisesRegex(C.CardRefused, "card_attempts_invalid"):
            CAT.compile(card(attempts=True))

    def test_every_approved_field_is_in_the_digest(self):
        base = CAT.compile(card())
        for change in ({"attempts": 3}, {"rounds": {"fix": 2}}, {"lifetime_s": 3 * 86400}, {"ref": "c" * 40},
                       {"briefs": {**card()["briefs"], "review": "another checklist"}}, {"estimate_s": 600},
                       {"paths_allowed": ["core/"]}, {"input_artifacts": [{"ref": "spec.md", "sha256": "d" * 64}]}):
            other = CAT.compile(card(**change))
            self.assertNotEqual(other.digest, base.digest, change)
        moved = CAT.compile(card(workers={**card()["workers"], "reviewer": {"actor": "fakework", "route": "fakework",
                                                                            "amount": 0.5, "cost_cap_usd": 1}}))
        self.assertEqual(moved.steps[0].digest != base.steps[0].digest, True)   # the card digest is in every step
        self.assertNotEqual(moved.steps[1].digest, base.steps[1].digest)

    def test_rounds_say_how_many_fix_steps_the_card_enumerates(self):
        self.assertEqual([s.name for s in CAT.compile(card(rounds={"fix": 0})).steps], ["build", "review"])
        self.assertEqual([s.name for s in CAT.compile(card(rounds={"fix": 3})).steps],
                         ["build", "review", "fix-1", "fix-2", "fix-3"])
        with self.assertRaisesRegex(C.CardRefused, "card_too_many_steps"):
            CAT.compile(card(rounds={"fix": 99}))
        self.assertEqual(len(CAT.compile(card(rounds={"fix": 98})).grant_digests), 100)   # the grant's bound

    def test_a_cross_vendor_review_is_never_a_card_step(self):
        c = CAT.compile(card(workers={**card()["workers"], "reviewer": {"actor": "claude-code", "route": "anthropic.api",
                                                                         "amount": 1, "cost_cap_usd": 1}}))
        self.assertEqual([(s.name, s.actor, s.on_card) for s in c.steps],
                         [("build", "fakework", True), ("review", "claude-code", False), ("fix-1", "fakework", True)])
        self.assertEqual(c.grant_digests, (c.steps[0].digest, c.steps[2].digest))   # the grant never names it

    def test_a_malformed_card_is_refused_by_code(self):
        cases = {"card_class_unknown": card(class_version=2), "card_id_invalid": card(card_id="C" * 41),
                 "card_ref_invalid": card(ref="main"), "card_attempts_invalid": card(attempts=0),
                 "card_rounds_invalid": card(rounds={}), "card_workers_invalid": card(workers={}),
                 "card_briefs_invalid": card(briefs={**card()["briefs"], "fix": " "}),
                 "card_lifetime_invalid": card(lifetime_s=31 * 86400), "card_amount_invalid": card(
                     workers={**card()["workers"], "builder": {"actor": "fakework", "route": "fakework",
                                                               "amount": 0.123456, "cost_cap_usd": 1}}),
                 "card_fields_invalid": {**card(), "grant": "yes"},
                 "card_input_artifacts_invalid": card(input_artifacts=[{"ref": "x", "sha256": "nope"}])}
        for code, doc in cases.items():
            with self.assertRaisesRegex(C.CardRefused, code, msg=code):
                CAT.compile(doc)
        with self.assertRaisesRegex(C.CardRefused, "card_workers_invalid"):
            CAT.compile(card(workers={**card()["workers"], "reviewer": {"actor": "grok", "route": "x",
                                                                        "amount": 1, "cost_cap_usd": 1}}))


class Inputs(unittest.TestCase):
    def test_inputs_are_only_digests_recorded_for_earlier_steps_of_the_card(self):
        own = [{"ref": "spec.md", "sha256": "d" * 64}]
        c = CAT.compile(card(rounds={"fix": 2}, input_artifacts=own))
        build, review, fix1, fix2 = c.steps
        self.assertEqual(c.inputs(build, []), (own, [{"from": "card", **own[0]}]))
        done = [{"step": "build", "item_id": "i-build", "artifacts": [art("output", "b"), art("patch", "b")]}]
        arts, sources = c.inputs(review, done)
        self.assertEqual(arts, [{"ref": "b.patch", "sha256": sha("bpatch")}])       # never the builder's output
        self.assertEqual(sources[0]["from"], "build")
        done.append({"step": "review", "item_id": "i-review", "artifacts": [art("output", "r"), art("findings", "r")]})
        arts, sources = c.inputs(fix1, done)
        self.assertEqual([a["ref"] for a in arts], ["b.patch", "r.findings"])
        self.assertEqual([(s["from"], s["kind"]) for s in sources], [("build", "patch"), ("review", "findings")])
        done.append({"step": "fix-1", "item_id": "i-fix1", "artifacts": [art("patch", "f"), art("findings", "f")]})
        self.assertEqual([a["ref"] for a in c.inputs(fix2, done)[0]], ["f.patch", "f.findings"])   # the latest
        # A malformed recorded artifact is not a digest, and a later step's outputs never feed an earlier one.
        done[0]["artifacts"] = [{"kind": "patch", "ref": "b.patch", "sha256": "not-a-digest"}]
        self.assertEqual(c.inputs(review, done), ([], []))

    def test_the_next_step_follows_the_template_rule(self):
        c = CAT.compile(card())
        build, review, fix = c.steps
        self.assertEqual(c.following(build, [art("patch", "b")]), review)
        self.assertEqual(c.following(build, []), review)                                # a review always follows
        self.assertEqual(c.following(review, [art("findings", "r")]), fix)
        self.assertIsNone(c.following(review, [art("output", "r")]))                     # no findings: done
        self.assertIsNone(c.following(fix, [art("findings", "f")]))                      # no rounds left: done


class StepDigest(unittest.TestCase):
    def row(self, compiled, st, **over):
        """An item row as the store writes it (json types back from PostgreSQL)."""
        r = {"card_id": compiled.card_id, "step": st.name, "actor": st.actor, "scope": st.scope,
             "repository": compiled.card["repository"], "ref": compiled.card["ref"], "brief": st.brief,
             "route": st.route, "reservation": {"requested": float(st.amount), "reserved": float(st.amount)},
             "cost_cap_usd": Decimal(str(st.cost_cap_usd)), "required_capabilities": list(st.required_capabilities),
             "facts": {"card": {"digest": compiled.digest, "class_digest": compiled.class_digest, "kind": st.kind,
                                "on_card": True}}}
        r.update(over)
        return r

    def test_a_row_has_its_compiled_digest_until_a_fixed_field_differs(self):
        c = CAT.compile(card())
        for step in c.steps:
            self.assertEqual(C.row_step_digest(self.row(c, step)), step.digest)
            self.assertEqual(S._row_subject(self.row(c, step)), step.digest)
        build = c.steps[0]
        for change in ({"brief": "rm -rf"}, {"route": "fakework.api"}, {"actor": "claude-code"}, {"ref": "b" * 40},
                       {"reservation": {"requested": 0.5}}, {"cost_cap_usd": Decimal("2.00")}, {"step": "review"},
                       {"required_capabilities": ["host:mac-local"]}, {"scope": "research"}):
            self.assertNotEqual(C.row_step_digest(self.row(c, build, **change)), build.digest, change)
        self.assertIsNone(C.row_step_digest(self.row(c, build, facts={})))
        off = self.row(c, build, input_artifacts=[], job_id="C-17.review", amount=None)
        off["facts"]["card"]["on_card"] = False
        self.assertNotEqual(S._row_subject(off), build.digest)                   # its own approval names its work


class Record(unittest.TestCase):
    def test_a_card_is_recompiled_from_its_record_whatever_the_catalogue_says_now(self):
        c = CAT.compile(card())
        again = C.recompile(record(c))
        self.assertEqual(([s.digest for s in again.steps], again.digest), ([s.digest for s in c.steps], c.digest))
        edited = copy.deepcopy(CAT.classes["build"])
        edited["steps"][1]["brief"] = "Review strictly. {brief}"
        later = C.Catalogue(dict(CAT.families), {"build": edited})
        self.assertNotEqual(later.compile(card()).steps[1].digest, c.steps[1].digest)      # new cards: the new row
        self.assertEqual(C.recompile(record(c)).steps[1].digest, c.steps[1].digest)        # admitted: as pinned
        for tamper in (lambda r: r["card"].update(attempts=9), lambda r: r["class_row"]["steps"][1].update(
                brief="x {brief}"), lambda r: r["families"].update(fakework="anthropic"),
                lambda r: r["steps"].pop()):
            bad = record(c)
            tamper(bad)
            with self.assertRaisesRegex(C.CardRefused, "card_record_invalid"):
                C.recompile(bad)


class StepOperation(unittest.TestCase):
    def test_each_step_of_a_grant_has_its_own_operation_id(self):
        grant, c = str(uuid4()), CAT.compile(card())
        ids = [C.step_operation_id(grant, d) for d in c.grant_digests]
        self.assertEqual(len(set(ids)), 3)
        self.assertEqual(ids[0], C.step_operation_id(grant, c.grant_digests[0]))
        self.assertNotEqual(ids[0], C.step_operation_id(str(uuid4()), c.grant_digests[0]))


class Lifetime(unittest.TestCase):
    def grant(self, compiled):
        return C.card_grant(compiled, grant_id=str(uuid4()), principal="robert", environment="dev",
                            owner_turn_ids=("owner-turn",), proposal_id=str(uuid4()),
                            approved_operation_id=str(uuid4()), approval_expires_at=NOW + timedelta(minutes=5),
                            now=NOW)

    def test_a_card_lives_a_day_unless_the_owner_approved_a_longer_lifetime(self):
        day = self.grant(CAT.compile(card()))
        self.assertEqual((day.expires_at - NOW, day.work_id, day.capability, day.max_operations),
                         (timedelta(days=1), "C-17", "delegate.card", 3))
        self.assertEqual(day.argument_digests, CAT.compile(card()).grant_digests)
        three = self.grant(CAT.compile(card(lifetime_s=3 * 86400)))
        self.assertEqual(three.expires_at - NOW, timedelta(days=3))
        short = self.grant(CAT.compile(card(lifetime_s=3600)))
        self.assertEqual(short.expires_at - NOW, timedelta(hours=1))
        with self.assertRaisesRegex(Exception, "grant_approval_invalid"):        # a card grant names its card
            G.GrantDefinition(str(uuid4()), "robert", "dev", "C-17", ("t",), ("authority",), C.CARD_POLICY,
                              C.CARD_CAPABILITY, 1, day.argument_digests, 3, NOW + timedelta(days=3),
                              proposal_id=str(uuid4()), approved_operation_id=str(uuid4()),
                              approval_expires_at=NOW + timedelta(minutes=5))


class A2AMappingNote(unittest.TestCase):
    NOTE = ROOT / "core" / "mc_delegation_dispatcher" / "mappings" / "a2a.md"

    def test_the_note_is_pinned_and_names_every_card_field_item_state_and_result_field(self):
        text = self.NOTE.read_text()
        self.assertIn("A2A version: 0.3.0", text)
        self.assertIn("not a dependency", text)
        for field in sorted(C._REQUIRED | set(C._OPTIONAL)):
            self.assertIn(f"`{field}`", text, field)
        for state in ("proposed", "queued", "claimed", "running", "lapsed", "paused", "unknown") + S.TERMINAL:
            self.assertIn(f"`{state}`", text, state)
        for field in ("status", "artifacts", "detail", "output_text", "route", "usage"):
            self.assertIn(f"`{field}`", text, field)
        # Review N7: no bare file name as a URI, artifact ids distinct per step, and where a messageId comes from.
        self.assertNotIn("`file.uri` = its `ref`", text)
        self.assertNotIn("`artifactId` = `sha256`", text)
        self.assertIn("`artifactId` = `<request_id>/<ref>`", text)
        self.assertIn("**`messageId`** is the `event_id`", text)


if __name__ == "__main__":
    unittest.main()
