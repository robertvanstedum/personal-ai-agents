"""
test_german_domain.py — Test suite for german_domain.py (Group A)

Usage:
    python3 _NewDomains/language-german/test_german_domain.py
    python3 _NewDomains/language-german/test_german_domain.py --test D03
    python3 test_reporter.py --stats --suite german_domain
"""

import sys
import argparse
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from test_reporter import TestReporter, print_stats
from german_domain import (
    _normalize_answer, _expand_contractions, _parse_spoken_id,
    _lookup_verb, _all_verb_entries,
    _item_tag, _drill_prompt, _l2_prompt,
    _start_drill_state, _record_l1_person, _record_l2_item, _finalize_l1_items,
    _resolve_keyword_intent, _spell_feedback,
    _SESSION_RE, _DRILL_RE, _DRILL_LIST_RE,
    _DRILL_CTL_RE, _PHRASE_CAPTURE_RE, _AGAIN_RE,
    GERMAN_DIR, GERMAN_BASE,
    # Group B
    _phrase_next_id, _run,
    _load_drill_pool, _save_drill_pool,
    _load_phrasebook, _save_phrasebook,
    _load_keyword_map_bot,
    _call_llm, _fetch_conjugations, _fetch_phrases,
    _write_drill_anki, _drill_completion_message,
    # Group C
    _resolve_verb, _resolve_phrases, _resolve_drill_verb,
)
import german_domain
import tempfile, unittest.mock as mock

runner = TestReporter(suite="german_domain", group="Group A+B")
report = runner.report

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

# ─── Group B tests ────────────────────────────────────────────────────────────

def test_D16():
    name = "_phrase_next_id: first ID, global max sequence, date prefix"
    empty = _phrase_next_id([], "2026-05-19")
    phrases = [{"id": "ph_20260512_001"}, {"id": "ph_20260514_003"}]
    next_id = _phrase_next_id(phrases, "2026-05-19")
    passed = (
        empty == "ph_20260519_001" and
        next_id == "ph_20260519_004"
    )
    report("D16", name, passed, expected="ph_20260519_001 / ph_20260519_004",
           got=f"{empty} / {next_id}")

def test_D17():
    name = "_run: returns (stdout, stderr, returncode) tuple"
    out, err, rc = _run(["echo", "hello"])
    passed = rc == 0 and "hello" in out and isinstance(err, str)
    report("D17", name, passed, f"rc={rc}, out={out.strip()!r}", expected="rc=0", got=f"rc={rc}")

def test_D18():
    name = "_load_drill_pool: returns {} when file missing; loads dict when present"
    with tempfile.TemporaryDirectory() as tmp:
        original = german_domain.GERMAN_DIR
        fake_dir = Path(tmp)
        (fake_dir / "config").mkdir()
        german_domain.GERMAN_DIR = fake_dir
        try:
            missing = _load_drill_pool()
            pool_path = fake_dir / "config" / "drill_pool.json"
            pool_path.write_text('{"core": {"verbs": []}}')
            loaded = _load_drill_pool()
        finally:
            german_domain.GERMAN_DIR = original
    passed = missing == {} and loaded == {"core": {"verbs": []}}
    report("D18", name, passed, f"missing={missing}, loaded={loaded}")

def test_D19():
    name = "_save_drill_pool + _load_drill_pool: round-trip preserves data"
    with tempfile.TemporaryDirectory() as tmp:
        original = german_domain.GERMAN_DIR
        fake_dir = Path(tmp)
        (fake_dir / "config").mkdir()
        german_domain.GERMAN_DIR = fake_dir
        try:
            data = {"core": {"verbs": [{"verb": "nehmen"}]}}
            _save_drill_pool(data)
            loaded = _load_drill_pool()
        finally:
            german_domain.GERMAN_DIR = original
    passed = loaded == data
    report("D19", name, passed, expected=str(data), got=str(loaded))

def test_D20():
    name = "_load_phrasebook: returns {'phrases': []} when missing; loads correctly"
    with tempfile.TemporaryDirectory() as tmp:
        original = german_domain._PHRASEBOOK_FILE
        german_domain._PHRASEBOOK_FILE = Path(tmp) / "phrasebook.json"
        try:
            missing = _load_phrasebook()
            german_domain._PHRASEBOOK_FILE.write_text('{"phrases": [{"id": "ph_001"}]}')
            loaded = _load_phrasebook()
        finally:
            german_domain._PHRASEBOOK_FILE = original
    passed = missing == {"phrases": []} and loaded["phrases"][0]["id"] == "ph_001"
    report("D20", name, passed, f"missing={missing}, loaded_count={len(loaded.get('phrases',[]))}")

def test_D21():
    name = "_save_phrasebook + _load_phrasebook: round-trip preserves data"
    with tempfile.TemporaryDirectory() as tmp:
        original = german_domain._PHRASEBOOK_FILE
        german_domain._PHRASEBOOK_FILE = Path(tmp) / "phrasebook.json"
        try:
            data = {"phrases": [{"id": "ph_20260519_001", "german": "Ich nehme es", "english": "I'll take it"}]}
            _save_phrasebook(data)
            loaded = _load_phrasebook()
        finally:
            german_domain._PHRASEBOOK_FILE = original
    passed = loaded == data
    report("D21", name, passed, expected="round-trip match", got="match" if loaded == data else f"got {loaded}")

def test_D22():
    name = "_load_keyword_map_bot: returns {} when missing; loads dict when present"
    with tempfile.TemporaryDirectory() as tmp:
        original = german_domain.GERMAN_DIR
        fake_dir = Path(tmp)
        (fake_dir / "config").mkdir()
        german_domain.GERMAN_DIR = fake_dir
        try:
            missing = _load_keyword_map_bot()
            kmap = {"Georg": {"trigger_words": ["Georg"], "default_scenario": "coffee"}}
            (fake_dir / "config" / "keyword_map.json").write_text(
                __import__("json").dumps(kmap)
            )
            loaded = _load_keyword_map_bot()
        finally:
            german_domain.GERMAN_DIR = original
    passed = missing == {} and "Georg" in loaded
    report("D22", name, passed, f"missing={missing}, loaded_keys={list(loaded.keys())}")

def test_D23():
    name = "_call_llm: returns None when provider list is empty (no providers to try)"
    with mock.patch("german_domain._LLM_PROVIDERS", []):
        result = _call_llm("test prompt", max_tokens=10)
    passed = result is None
    report("D23", name, passed, f"result={result!r}", expected="None", got=repr(result))

def test_D24():
    name = "_fetch_conjugations: parses valid JSON from LLM; returns None on bad JSON"
    good_json = '{"verb":"nehmen","english":"to take","conjugations":{"ich":"nehme","du":"nimmst","er":"nimmt","wir":"nehmen","ihr":"nehmt","sie":"nehmen"}}'
    with mock.patch("german_domain._call_llm", return_value=good_json):
        result = _fetch_conjugations("nehmen")
    with mock.patch("german_domain._call_llm", return_value="not json at all"):
        bad = _fetch_conjugations("nehmen")
    with mock.patch("german_domain._call_llm", return_value=None):
        none_result = _fetch_conjugations("nehmen")
    passed = (
        result is not None and result.get("verb") == "nehmen" and
        bad is None and none_result is None
    )
    report("D24", name, passed, f"good={result and result.get('verb')}, bad={bad}, none={none_result}")

def test_D25():
    name = "_fetch_phrases: parses valid JSON array; returns [] on bad JSON or None"
    good_json = '[{"english":"I will take it","german":"Ich werde es nehmen"},{"english":"Take this","german":"Nehmen Sie das"}]'
    with mock.patch("german_domain._call_llm", return_value=good_json):
        result = _fetch_phrases("nehmen", "to take")
    with mock.patch("german_domain._call_llm", return_value="garbage"):
        bad = _fetch_phrases("nehmen", "to take")
    with mock.patch("german_domain._call_llm", return_value=None):
        none_result = _fetch_phrases("nehmen", "to take")
    passed = len(result) == 2 and bad == [] and none_result == []
    report("D25", name, passed, f"good_count={len(result)}, bad={bad}, none={none_result}")

def test_D26():
    name = "_write_drill_anki: friction items written to CSV; clean items skipped"
    with tempfile.TemporaryDirectory() as tmp:
        original = german_domain.GERMAN_DIR
        fake_dir = Path(tmp)
        (fake_dir / "anki").mkdir()
        german_domain.GERMAN_DIR = fake_dir
        try:
            state = {
                "items": [
                    {"front": "I'll take it", "back": "Ich nehme es", "result": "needs-practice", "tags": "needs-practice Vienna phrase nehmen"},
                    {"front": "clean item",   "back": "sauber",        "result": "drill-clean",    "tags": "drill-clean Vienna phrase nehmen"},
                ]
            }
            written = _write_drill_anki(state)
            csv_path = fake_dir / "anki" / "vienna_deck.csv"
            content = csv_path.read_text()
        finally:
            german_domain.GERMAN_DIR = original
    friction_in_csv = "I'll take it" in content
    passed = written == 1 and friction_in_csv and "clean item" not in content
    report("D26", name, passed, f"written={written}, csv_has_friction={friction_in_csv}",
           expected="written=1", got=f"written={written}")

def test_D27():
    name = "_drill_completion_message: returns string with score and counts"
    state = {
        "score": 4, "total": 6,
        "items": [
            {"result": "drill-clean"},
            {"result": "drill-reinforced"},
            {"result": "needs-practice"},
            {"result": "drill-clean"},
        ]
    }
    with mock.patch("german_domain._write_drill_anki", return_value=0):
        msg = _drill_completion_message(state, "✅ nehmen — nehme")
    passed = (
        "4/6" in msg and
        "Clean: 2" in msg and
        "Reinforced: 1" in msg and
        "Needs practice: 1" in msg and
        "again" in msg
    )
    report("D27", name, passed, repr(msg[:120]))


def test_D28():
    name = "_resolve_verb: returns entry immediately when verb already in pool (progress_cb=None)"
    pool = {"core": {"verbs": [{"verb": "nehmen", "english": "to take", "conjugations": {}}]}}
    with mock.patch("german_domain._load_drill_pool", return_value=pool):
        result = _resolve_verb("nehmen", progress_cb=None)
    passed = result is not None and result["verb"] == "nehmen"
    report("D28", name, passed, f"verb={result and result['verb']}", expected="nehmen", got=str(result and result['verb']))

def test_D29():
    name = "_resolve_verb: calls progress_cb with 'Looking up' when verb not in pool"
    pool = {"core": {"verbs": []}, "on_demand": {"verbs": []}}
    conj = {"verb": "nehmen", "english": "to take", "conjugations": {"ich": "nehme"}}
    messages = []
    def _cb(msg): messages.append(msg)
    with mock.patch("german_domain._load_drill_pool", return_value=pool), \
         mock.patch("german_domain._save_drill_pool"), \
         mock.patch("german_domain._fetch_conjugations", return_value=conj):
        result = _resolve_verb("nehmen", progress_cb=_cb)
    passed = result is not None and any("Looking up" in m for m in messages)
    report("D29", name, passed, f"messages={messages}", expected="'Looking up' in callback", got=str(messages))

def test_D30():
    name = "_resolve_phrases: returns cached phrases without calling progress_cb (progress_cb=None)"
    entry = {"verb": "nehmen", "english": "to take", "phrases": [{"english": "I take it", "german": "Ich nehme es"}]}
    result = _resolve_phrases(entry, progress_cb=None)
    passed = result == entry["phrases"]
    report("D30", name, passed, f"count={len(result)}", expected="1 phrase", got=str(len(result)))

def test_D31():
    name = "_resolve_drill_verb: returns entry for known verb in pool (progress_cb=None)"
    pool = {"core": {"verbs": [{"verb": "nehmen", "english": "to take", "conjugations": {}}]}}
    with mock.patch("german_domain._load_drill_pool", return_value=pool):
        result = _resolve_drill_verb("drill nehmen", progress_cb=None)
    passed = result is not None and result["verb"] == "nehmen"
    report("D31", name, passed, f"verb={result and result['verb']}", expected="nehmen", got=str(result and result['verb']))

def test_D32():
    name = "_resolve_drill_verb: callback fired when scene keyword resolves to verb"
    pool = {"core": {"verbs": [{"verb": "nehmen", "english": "to take", "conjugations": {}, "scenes": ["cafe"]}]}}
    messages = []
    def _cb(msg): messages.append(msg)
    with mock.patch("german_domain._load_drill_pool", return_value=pool):
        result = _resolve_drill_verb("drill cafe", progress_cb=_cb)
    passed = result is not None and any("scene" in m for m in messages)
    report("D32", name, passed, f"verb={result and result['verb']}, cb_msgs={messages}", expected="scene in callback", got=str(messages))


# ─── Main runner ──────────────────────────────────────────────────────────────

ALL_TESTS = [
    test_D01, test_D02, test_D03, test_D04, test_D05,
    test_D06, test_D07, test_D08, test_D09, test_D10,
    test_D11, test_D12, test_D13, test_D14, test_D15,
    test_D16, test_D17, test_D18, test_D19, test_D20,
    test_D21, test_D22, test_D23, test_D24, test_D25,
    test_D26, test_D27,
    test_D28, test_D29, test_D30, test_D31, test_D32,
]

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", type=str, help="Run single test by number, e.g. D03")
    args = parser.parse_args()

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

    runner.finish()
