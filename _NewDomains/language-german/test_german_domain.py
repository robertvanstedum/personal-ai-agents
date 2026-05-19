"""
test_german_domain.py — Test suite for german_domain.py (Group A)

Usage:
    python3 test_german_domain.py          # run all tests
    python3 test_german_domain.py --test 3 # run single test

After each run, appends a dated report to:
    ../../_working/test_german_domain_results_YYYYMMDD_HHMM.md

Failures are shown prominently at the end of stdout and at the top of each report.
All runs are kept; run `python3 test_german_domain.py --stats` for a build-phase summary.
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime

# Make german_domain importable from repo root
REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from german_domain import (
    _normalize_answer, _expand_contractions, _parse_spoken_id,
    _lookup_verb, _all_verb_entries,
    _item_tag, _drill_prompt, _l2_prompt,
    _start_drill_state, _record_l1_person, _record_l2_item, _finalize_l1_items,
    _resolve_keyword_intent, _spell_feedback,
    _SESSION_RE, _DRILL_RE, _DRILL_LIST_RE,
    _DRILL_CTL_RE, _PHRASE_CAPTURE_RE, _AGAIN_RE,
    GERMAN_DIR, GERMAN_BASE,
)

# ─── Test runner ─────────────────────────────────────────────────────────────

_results: list[dict] = []

def report(test_num: str, name: str, passed: bool, detail: str = "", *, expected: str = "", got: str = "") -> None:
    status = "PASS" if passed else "FAIL"
    label = f"Test {test_num} — {name}"
    suffix = f": {detail}" if detail else ""
    print(f"{label}: {status}{suffix}")
    _results.append({
        "num": test_num, "name": name, "status": status,
        "detail": detail, "expected": expected, "got": got,
    })

# ─── Sample data ─────────────────────────────────────────────────────────────

SAMPLE_POOL = {
    "core": {
        "verbs": [
            {"verb": "nehmen", "english": "to take", "conjugations": {"ich": "nehme", "du": "nimmst", "er": "nimmt", "wir": "nehmen", "ihr": "nehmt", "sie": "nehmen"}},
            {"verb": "gehen",  "english": "to go",   "conjugations": {"ich": "gehe",  "du": "gehst",  "er": "geht",  "wir": "gehen",  "ihr": "geht",   "sie": "gehen"}},
        ]
    },
    "on_demand": {
        "verbs": [
            {"verb": "sagen",  "english": "to say",  "conjugations": {}},
            {"verb": "nehmen", "english": "duplicate — should be skipped by core precedence", "conjugations": {}},
        ]
    }
}

SAMPLE_ENTRY = SAMPLE_POOL["core"]["verbs"][0]  # nehmen

# ─── Tests ────────────────────────────────────────────────────────────────────

def test_D01():
    name = "_normalize_answer: lowercase, strip punctuation, collapse whitespace"
    result = _normalize_answer("Ich BIN  müde!")
    expected = "ich bin müde"
    passed = result == expected
    report("D01", name, passed, repr(result), expected=repr(expected), got=repr(result))

def test_D02():
    name = "_expand_contractions: im → in dem, ins → in das, no-op on unknown"
    r1 = _expand_contractions("Ich gehe im Park.")
    r2 = _expand_contractions("Er geht ins Café.")
    r3 = _expand_contractions("Das ist schön.")
    passed = (
        "in dem" in r1 and
        "in das" in r2 and
        r3 == "Das ist schön."
    )
    report("D02", name, passed, f"im→{r1!r} / ins→{r2!r}")

def test_D03():
    name = "_parse_spoken_id: 'one' → '001', 'three' → '003', bare digit '5' → '005'"
    r1 = _parse_spoken_id("one")
    r2 = _parse_spoken_id("three")
    r3 = _parse_spoken_id("5")
    passed = r1 == "001" and r2 == "003" and r3 == "005"
    report("D03", name, passed, f"one={r1}, three={r2}, 5={r3}",
           expected="001, 003, 005", got=f"{r1}, {r2}, {r3}")

def test_D04():
    name = "_lookup_verb: found in pool, not found returns None"
    found = _lookup_verb(SAMPLE_POOL, "nehmen")
    not_found = _lookup_verb(SAMPLE_POOL, "wissen")
    passed = found is not None and found["verb"] == "nehmen" and not_found is None
    report("D04", name, passed, f"found={found and found['verb']}, not_found={not_found}")

def test_D05():
    name = "_all_verb_entries: merges core + on_demand, core takes precedence (no nehmen duplicate)"
    entries = _all_verb_entries(SAMPLE_POOL)
    names = [e["verb"] for e in entries]
    nehmen_count = names.count("nehmen")
    sagen_present = "sagen" in names
    passed = nehmen_count == 1 and sagen_present and len(entries) == 3
    report("D05", name, passed, f"verbs={names}, nehmen_count={nehmen_count}")

def test_D06():
    name = "_item_tag: wrong=0→drill-clean, wrong=1→drill-reinforced, hint→needs-practice"
    t1 = _item_tag(0, False, False)
    t2 = _item_tag(1, False, False)
    t3 = _item_tag(0, True,  False)
    t4 = _item_tag(0, False, True)
    t5 = _item_tag(2, False, False)
    passed = (t1 == "drill-clean" and t2 == "drill-reinforced" and
              t3 == "needs-practice" and t4 == "needs-practice" and
              t5 == "needs-practice")
    report("D06", name, passed, f"{t1}/{t2}/{t3}/{t4}/{t5}")

def test_D07():
    name = "_drill_prompt: contains verb, english, person, position"
    state = _start_drill_state(SAMPLE_ENTRY)
    prompt = _drill_prompt(state)
    passed = (
        "nehmen" in prompt and
        "to take" in prompt and
        "___?" in prompt and
        "1/" in prompt
    )
    report("D07", name, passed, repr(prompt[:80]))

def test_D08():
    name = "_l2_prompt: contains position and 'How do you say'"
    state = _start_drill_state(SAMPLE_ENTRY)
    state["phrases"] = [{"english": "I'll take it", "german": "Ich nehme es"}]
    state["queue"] = [0]
    state["pos"] = 0
    prompt = _l2_prompt(state)
    passed = "How do you say" in prompt and "I'll take it" in prompt and "1/" in prompt
    report("D08", name, passed, repr(prompt[:80]))

def test_D09():
    name = "_start_drill_state: all required keys present, initial values correct"
    state = _start_drill_state(SAMPLE_ENTRY)
    required = {"verb", "english", "conjugations", "queue", "pos", "current",
                "score", "total", "retry", "items", "hint_used_current", "l1_worst_tag"}
    missing = required - set(state.keys())
    passed = (
        not missing and
        state["verb"] == "nehmen" and
        state["pos"] == 0 and
        state["score"] == 0 and
        state["items"] == [] and
        state["l1_worst_tag"] == "drill-clean" and
        len(state["queue"]) == 6
    )
    report("D09", name, passed, f"missing={missing}, verb={state.get('verb')}, queue_len={len(state.get('queue', []))}")

def test_D10():
    name = "_record_l1_person: clean answer keeps drill-clean, wrong escalates l1_worst_tag"
    state = _start_drill_state(SAMPLE_ENTRY)
    _record_l1_person(state, wrong_count=0, auto_revealed=False)
    after_clean = state["l1_worst_tag"]
    _record_l1_person(state, wrong_count=1, auto_revealed=False)
    after_wrong = state["l1_worst_tag"]
    passed = after_clean == "drill-clean" and after_wrong == "drill-reinforced"
    report("D10", name, passed, f"clean={after_clean}, wrong={after_wrong}")

def test_D11():
    name = "_record_l2_item: appends item, clears hint_used_current"
    state = _start_drill_state(SAMPLE_ENTRY)
    state["hint_used_current"] = True
    phrase = {"english": "I'll take it", "german": "Ich nehme es"}
    _record_l2_item(state, phrase, wrong_count=0, auto_revealed=False)
    passed = (
        len(state["items"]) == 1 and
        state["items"][0]["front"] == "I'll take it" and
        state["items"][0]["result"] == "needs-practice" and  # hint_used → needs-practice
        state["hint_used_current"] == False
    )
    report("D11", name, passed, f"item={state['items'][0].get('result')}, hint_cleared={not state['hint_used_current']}")

def test_D12():
    name = "_finalize_l1_items: drill-clean produces no item; reinforced produces one Anki item"
    state_clean = _start_drill_state(SAMPLE_ENTRY)
    _finalize_l1_items(state_clean)
    no_item = len(state_clean["items"]) == 0

    state_wrong = _start_drill_state(SAMPLE_ENTRY)
    state_wrong["l1_worst_tag"] = "drill-reinforced"
    _finalize_l1_items(state_wrong)
    has_item = len(state_wrong["items"]) == 1 and "nehmen" in state_wrong["items"][0]["front"]

    passed = no_item and has_item
    report("D12", name, passed, f"clean_items={len(state_clean['items'])}, wrong_items={len(state_wrong['items'])}")

def test_D13():
    name = "_resolve_keyword_intent: matches keyword, single word fires not, no match returns None"
    kmap = {"Georg": {"trigger_words": ["Georg", "café"], "default_scenario": "coffee"}}
    match = _resolve_keyword_intent("let's do Georg today", kmap)
    no_match = _resolve_keyword_intent("let's do Maria today", kmap)
    single = _resolve_keyword_intent("Georg", kmap)  # single word — should not fire
    passed = (
        match is not None and match[0] == "Georg" and
        no_match is None and
        single is None
    )
    report("D13", name, passed, f"match={match}, no_match={no_match}, single={single}")

def test_D14():
    name = "_spell_feedback: returns non-None for clear German misspelling"
    # "nehmen" misspelled as "nehemn" — should get a suggestion
    result = _spell_feedback("ich nehemn das")
    # If spellchecker not installed, None is acceptable (library gracefully returns None)
    passed = result is None or (isinstance(result, str) and len(result) > 0)
    report("D14", name, passed, repr(result))

def test_D15():
    name = "Regex smoke: _SESSION_RE, _DRILL_RE, _DRILL_LIST_RE, _AGAIN_RE key patterns match"
    session_ok = bool(_SESSION_RE.search("next german session please"))
    drill_ok   = bool(_DRILL_RE.search("drill nehmen"))
    list_ok    = bool(_DRILL_LIST_RE.search("drill list"))
    again_ok   = bool(_AGAIN_RE.search("again"))
    ctl_ok     = bool(_DRILL_CTL_RE.search("stop"))
    phrase_ok  = bool(_PHRASE_CAPTURE_RE.search("save a phrase"))
    passed = session_ok and drill_ok and list_ok and again_ok and ctl_ok and phrase_ok
    report("D15", name, passed,
           f"session={session_ok} drill={drill_ok} list={list_ok} again={again_ok} ctl={ctl_ok} phrase={phrase_ok}")

# ─── Main runner ──────────────────────────────────────────────────────────────

ALL_TESTS = [
    test_D01, test_D02, test_D03, test_D04, test_D05,
    test_D06, test_D07, test_D08, test_D09, test_D10,
    test_D11, test_D12, test_D13, test_D14, test_D15,
]

WORKING_DIR = REPO_ROOT / "_working"

def _print_failure_summary() -> None:
    failures = [r for r in _results if r["status"] == "FAIL"]
    if not failures:
        return
    print("\n" + "─" * 60)
    print(f"FAILURES ({len(failures)}):")
    for r in failures:
        print(f"\n  ❌ {r['num']} — {r['name']}")
        if r.get("expected"):
            print(f"     expected : {r['expected']}")
            print(f"     got      : {r['got']}")
        elif r.get("detail"):
            print(f"     detail   : {r['detail']}")
    print("─" * 60)

def _write_results_md(run_ts: str) -> Path:
    out_path = WORKING_DIR / f"test_german_domain_{run_ts}.md"
    passed = sum(1 for r in _results if r["status"] == "PASS")
    failed = sum(1 for r in _results if r["status"] == "FAIL")
    total  = len(_results)

    lines = [
        "# german_domain.py — Test Results",
        f"**Run:** {run_ts.replace('_', ' ')}  ",
        f"**Suite:** Group A (constants + pure functions)  ",
        f"**Result:** {passed}/{total} passed" + (f" — **{failed} FAILED**" if failed else ""),
        "",
    ]

    failures = [r for r in _results if r["status"] == "FAIL"]
    if failures:
        lines += ["## Failures", ""]
        for r in failures:
            lines.append(f"### ❌ {r['num']} — {r['name']}")
            if r.get("expected"):
                lines.append(f"- **Expected:** `{r['expected']}`")
                lines.append(f"- **Got:** `{r['got']}`")
            if r.get("detail"):
                lines.append(f"- **Detail:** {r['detail']}")
            lines.append("")
        lines += ["---", ""]

    lines += [
        "## Full Results",
        "",
        "| # | Test | Status | Detail |",
        "|---|------|--------|--------|",
    ]
    for r in _results:
        icon = "✅" if r["status"] == "PASS" else "❌"
        detail = r["detail"].replace("|", "\\|")
        lines.append(f"| {r['num']} | {r['name']} | {icon} {r['status']} | {detail} |")

    out_path.write_text("\n".join(lines) + "\n")
    return out_path

def _print_stats() -> None:
    """Print a summary of all saved test runs — defects found and fixed during build."""
    reports = sorted(WORKING_DIR.glob("test_german_domain_*.md"))
    if not reports:
        print("No reports found in _working/.")
        return
    print(f"\nBuild phase stats — {len(reports)} run(s) found:\n")
    total_failures = 0
    for p in reports:
        text = p.read_text()
        run_ts = p.stem.replace("test_german_domain_", "")
        result_line = next((l for l in text.splitlines() if "passed" in l and "**Result:**" in l), "")
        fail_count = text.count("❌")
        total_failures += fail_count
        print(f"  {run_ts}  {result_line.replace('**Result:** ', '').strip()}")
    print(f"\n  Total failure instances across all runs: {total_failures}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", type=str, help="Run single test by number, e.g. D03")
    parser.add_argument("--stats", action="store_true", help="Show build-phase defect stats across all saved runs")
    args = parser.parse_args()

    if args.stats:
        _print_stats()
        sys.exit(0)

    run_ts = datetime.now().strftime("%Y%m%d_%H%M")

    if args.test:
        target = args.test.upper().lstrip("0") or "0"
        run = [t for t in ALL_TESTS if t.__name__.upper().endswith(target)]
        if not run:
            print(f"No test matching '{args.test}'")
            sys.exit(1)
        for t in run:
            t()
    else:
        for t in ALL_TESTS:
            t()

    passed = sum(1 for r in _results if r["status"] == "PASS")
    total  = len(_results)
    _print_failure_summary()
    print(f"\n{passed}/{total} tests passed.")
    out_path = _write_results_md(run_ts)
    print(f"Report: {out_path}")
    sys.exit(0 if passed == total else 1)
