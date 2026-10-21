#!/usr/bin/env python3
"""CI hygiene for alert rules (spec 161 §12.2). Fails (exit 1) unless every rule:
  * has labels.severity in {critical, major, warning, none} and labels.minimoi_alert_key;
  * has annotations.summary and annotations.runbook_url pointing at an existing docs/runbooks file;
  * appears in the promtool tests with at least one firing and one non-firing case;
  * uses only metric names registered in config/observability/vocabulary.yaml (minimoi_*),
    or the Collector's/Prometheus's own self-metrics.
Prints one line per problem; never prints rule values.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONF = ROOT / "config" / "observability"
SEVERITIES = {"critical", "major", "warning", "none"}
SELF_METRICS = re.compile(r"^(otelcol_|prometheus_)")


def main() -> int:
    vocab = yaml.safe_load((CONF / "vocabulary.yaml").read_text())
    known = set(vocab["metrics"])
    problems = []
    rules = []
    for f in sorted((CONF / "rules").glob("*.yaml")):
        for group in (yaml.safe_load(f.read_text()) or {}).get("groups", []):
            rules += group.get("rules", [])
    tested_firing, tested_quiet = set(), set()
    for f in sorted((CONF / "rules" / "tests").glob("*.yaml")):
        for test in (yaml.safe_load(f.read_text()) or {}).get("tests", []):
            for case in test.get("alert_rule_test", []):
                (tested_firing if case.get("exp_alerts") else tested_quiet).add(case["alertname"])
    for rule in rules:
        name = rule.get("alert", "<unnamed>")
        labels, ann = rule.get("labels", {}), rule.get("annotations", {})
        if labels.get("severity") not in SEVERITIES:
            problems.append(f"{name}: severity missing or invalid")
        if not labels.get("minimoi_alert_key"):
            problems.append(f"{name}: minimoi_alert_key missing")
        if not ann.get("summary"):
            problems.append(f"{name}: summary missing")
        runbook = ann.get("runbook_url", "")
        if not runbook or not (ROOT / runbook).is_file():
            problems.append(f"{name}: runbook_url missing or file not found")
        if name not in tested_firing:
            problems.append(f"{name}: no firing test")
        if name not in tested_quiet and name != "Watchdog":
            problems.append(f"{name}: no non-firing test")
        for metric in re.findall(r"\b([a-z_][a-z0-9_]*_(?:seconds|ratio|total|code|up|running|readable|outcome|pending))\b",
                                 rule.get("expr", "")):
            if metric.startswith("minimoi_") and metric not in known:
                problems.append(f"{name}: metric not in vocabulary: {metric}")
            elif not metric.startswith("minimoi_") and not SELF_METRICS.match(metric):
                problems.append(f"{name}: unexpected metric family: {metric}")
    for p in problems:
        print(p)
    print(f"{len(rules)} rules checked, {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
