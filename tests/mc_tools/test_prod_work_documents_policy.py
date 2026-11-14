"""Production MC's working documents (#599): on 14 Nov MiniMoi refused MC's minimoi_work_save on production with
tool_not_in_policy, because prod's policy had no work rules while MC's container offered the tool. Prod's rules are the
example's, unchanged, and the production task service keeps the private store on."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from core.mc_tools import policy as P

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "services/mc_operations/policy"


def _rules(name):
    return {r["handler"]: r for r in json.loads((POLICY / name).read_text())["rules"]}


def test_prod_policy_carries_the_example_work_rules_unchanged():
    prod, example = _rules("mc-policy.prod.json"), _rules("mc-policy.example.json")
    for handler in ("work_read_v1", "work_save_v1"):
        assert prod[handler] == example[handler], handler
    assert prod["work_save_v1"]["tier"] == 2 and prod["work_read_v1"]["tier"] == 1
    assert P.HANDLERS["work_save_v1"].tool == "minimoi_work_save"


def test_prod_policy_compiles_and_offers_work_save_once_approved():
    raw = (POLICY / "mc-policy.prod.json").read_bytes()
    doc = json.loads(raw)
    authority = json.dumps({"schema": P.AUTHORITY_SCHEMA, "revision": doc["revision"], "digest": P.digest_of(doc),
                            "approved_by": "test", "approved_at": "2026-11-14T00:00:00Z", "ref": "test"}).encode()
    compiled = P.compile_policy(raw, authority)
    assert compiled.rule_for("minimoi_work_save").tier == 2
    assert compiled.rule_for("minimoi_work_read") is not None


def test_the_production_task_service_keeps_the_private_work_store_on():
    compose = yaml.safe_load((ROOT / "docker-compose.minimoi-1-2-production.yml").read_text())
    env = compose["services"]["partnership-tasks"]["environment"]
    assert env["PARTNERSHIP_WORK_ARTIFACTS"] == "1"
