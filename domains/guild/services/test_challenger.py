#!/usr/bin/env python3
"""
domains/guild/services/test_challenger.py — End-to-end test harness

Proves the cross-provider round-trip works before touching any domain.
Uses a canned first_pass with a deliberate weak claim to give the challenger
something to find.

Usage:
    venv/bin/python3 domains/guild/services/test_challenger.py
    venv/bin/python3 domains/guild/services/test_challenger.py --domain guild_career_assessment
    venv/bin/python3 domains/guild/services/test_challenger.py --dry-run

Exit 0 = passed. Exit 1 = failed.
"""

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BASE_DIR))

from domains.guild.services.challenger import ChallengerService

# ── Canned test inputs per domain ─────────────────────────────────────────────

CANNED = {
    "curator_deep_dive": {
        "first_pass": (
            "The US dollar's reserve currency status is essentially unchallenged. "
            "Recent BRICS expansion signals ambition but no credible alternative to "
            "dollar-denominated trade has emerged. Central bank gold buying is a "
            "hedging move, not a de-dollarization signal. The dollar's share of "
            "global reserves remains above 60% — structurally dominant."
        ),
        "context": {
            "topic_name": "Dollar Reserve Status",
            "related_threads": "brics-expansion, gold-reserves, cny-internationalization",
            "sources_summary": (
                "IMF COFER data (Q4 2022): USD 58.4% of allocated reserves. "
                "BIS 2023 quarterly: dollar invoicing unchanged at 88% of FX trades. "
                "Bloomberg 2021: central bank gold purchases at 50-year high."
            ),
        },
    },
    "german_writing": {
        "first_pass": (
            "Original: 'Ich möchte zahlen.' "
            "Correction: No correction needed — grammatically correct. "
            "Translation: 'I would like to pay.'"
        ),
        "context": {
            "original": "Zahlen bitte.",
            "corrected_german": "Zahlen bitte.",
            "english_translation": "Pay please.",
            "context": "At a restaurant, end of meal",
        },
    },
    "guild_career_assessment": {
        "first_pass": (
            "Strong fit: Robert's 30 years of telecom infrastructure experience maps "
            "well to this Principal Engineer role. Production agentic AI systems "
            "experience directly relevant. Score: 8.5/10. "
            "Recommend: apply, emphasize the IoT-at-scale background."
        ),
        "context": {
            "first_pass": (
                "Strong fit: Robert's 30 years of telecom infrastructure experience maps "
                "well to this Principal Engineer role. Production agentic AI systems "
                "experience directly relevant. Score: 8.5/10."
            ),
            "opportunity_details": (
                "Principal Engineer, Agentic AI Platform. "
                "Requirements: 10+ years distributed systems, experience with LLM agent "
                "orchestration, production ML systems. Preferred: startup experience, "
                "Python/Go. Location: Seattle or remote."
            ),
        },
    },
}

PASS = "✅"
FAIL = "❌"


def run_test(domain: str, dry_run: bool) -> bool:
    print(f"\n══ Challenger Test — {domain} {'(DRY RUN)' if dry_run else ''} ════════════════")

    canned = CANNED.get(domain)
    if not canned:
        available = ", ".join(CANNED.keys())
        print(f"\n{FAIL}  No canned input for domain '{domain}'")
        print(f"   Available: {available}")
        return False

    first_pass = canned["first_pass"]
    context    = canned["context"]

    if dry_run:
        print("\n── Config check ─────────────────────────────────────────────")
        svc = ChallengerService()
        dcfg = svc._domain_cfg(domain)
        print(f"  enabled:      {dcfg.get('enabled', False)}")
        print(f"  show_process: {dcfg.get('show_process', False)}")
        print(f"  challenger_prompt:   {dcfg.get('challenger_prompt', 'N/A')}")
        print(f"  final_review_prompt: {dcfg.get('final_review_prompt', 'N/A')}")

        print("\n── Round 2 prompt (what Grok would receive) ─────────────────")
        try:
            prompt = svc._get_prompt(dcfg.get("challenger_prompt", ""), {**context, "first_pass": first_pass})
            print(prompt[:600] + ("..." if len(prompt) > 600 else ""))
        except Exception as e:
            print(f"  Error building prompt: {e}")

        print(f"\n{PASS}  Dry run complete — no API calls made")
        return True

    # Live run
    svc = ChallengerService()
    print(f"\n── Input ─────────────────────────────────────────────────────")
    print(f"  First pass ({len(first_pass)} chars):")
    print(f"  {first_pass[:150]}{'...' if len(first_pass) > 150 else ''}")

    print(f"\n── Running exchange ──────────────────────────────────────────")
    print(f"  Round 2: Grok ({svc._cfg.get('challenger_model', '?')}) → challenge pass")
    print(f"  Round 3: Claude ({svc._cfg.get('primary_model', '?')}) → final review")

    result = svc.run(
        domain=domain,
        feature="test",
        first_pass=first_pass,
        context=context,
        entity_description=f"test-harness-{domain}",
    )

    print(f"\n── Result ────────────────────────────────────────────────────")
    print(f"  enabled:          {result.enabled}")
    print(f"  show_process:     {result.show_process}")
    print(f"  challenged_count: {result.challenged_count}")
    print(f"  accepted_count:   {result.accepted_count}")
    print(f"  rejected_count:   {result.rejected_count}")
    print(f"  outputs_differ:   {result.outputs_differ}")
    print(f"  exchange_id:      {result.exchange_id} ({'DB stored' if result.exchange_id else 'DB unavailable'})")

    if result.error:
        print(f"\n{FAIL}  Exchange error: {result.error}")

    print(f"\n── Challenge points ──────────────────────────────────────────")
    if result.challenge_points:
        for i, p in enumerate(result.challenge_points):
            mark = PASS if p.get("accepted") else "  "
            print(f"  [{i}] {mark} [{p['type']}] ({p['impact']}) {p['description'][:80]}")
    else:
        print("  (none parsed)")

    print(f"\n── Key change ────────────────────────────────────────────────")
    print(f"  {result.key_change or '(none)'}")

    print(f"\n── Final (first 200 chars) ───────────────────────────────────")
    print(f"  {result.final[:200]}{'...' if len(result.final) > 200 else ''}")

    # ── Gate checks ───────────────────────────────────────────────────────────
    print(f"\n── Gate checks ───────────────────────────────────────────────")
    checks = {
        "enabled = True":           result.enabled,
        "no error":                 result.error is None,
        "challenge_points parsed":  result.challenged_count > 0,
        "accepted_count >= 1":      result.accepted_count >= 1,
        "outputs_differ = True":    result.outputs_differ,
        "final text non-empty":     len(result.final) > 20,
    }
    all_passed = True
    for label, ok in checks.items():
        print(f"  {'✅' if ok else '❌'}  {label}")
        if not ok:
            all_passed = False

    print(f"\n{'═' * 60}")
    print(f"  {'✅ PASSED' if all_passed else '❌ FAILED'} — {sum(checks.values())}/{len(checks)} checks")
    print()
    return all_passed


def main():
    parser = argparse.ArgumentParser(description="Challenger service end-to-end test")
    parser.add_argument("--domain", default="curator_deep_dive",
                        help="Domain to test (default: curator_deep_dive)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print prompts only — no API calls")
    parser.add_argument("--all", action="store_true",
                        help="Test all canned domains")
    args = parser.parse_args()

    if args.all:
        results = {}
        for domain in CANNED:
            results[domain] = run_test(domain, args.dry_run)
        print("\n══ All domains summary ═══════════════════════════════════")
        all_ok = True
        for domain, ok in results.items():
            print(f"  {'✅' if ok else '❌'}  {domain}")
            if not ok:
                all_ok = False
        sys.exit(0 if all_ok else 1)
    else:
        ok = run_test(args.domain, args.dry_run)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
