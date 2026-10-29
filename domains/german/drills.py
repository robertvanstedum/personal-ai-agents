"""German drill prompts, state transitions and card-content rules.

These operations only read or mutate caller-owned dictionaries. They do not
load learner state, write Anki cards, contact providers or initialize a bot.
The caller owns persistence and when to finalize a drill.
"""
import random

DRILL_PERSONS   = ["ich", "du", "er", "wir", "ihr", "sie"]
PERSONS_DISPLAY = ["ich", "du", "er/sie/es", "wir", "ihr", "sie/Sie"]
PERSONS_POOL    = ["ich", "du", "er",         "wir", "ihr", "sie"]

def item_tag(wrong_count: int, hint_used: bool, auto_revealed: bool) -> str:
    if auto_revealed or hint_used or wrong_count >= 2:
        return "needs-practice"
    if wrong_count == 1:
        return "drill-reinforced"
    return "drill-clean"


def drill_prompt(state: dict) -> str:
    """Level 1 prompt: person fill-in."""
    person = state["current"]
    verb = state["verb"]
    english = state["english"]
    total = len(state["queue"])
    return f"{verb} ({english}) — {state['pos']+1}/{total}\n\n{person} ___?"


def l2_prompt(state: dict) -> str:
    """Level 2 prompt: translate this phrase."""
    idx = state["queue"][state["pos"]]
    phrase = state["phrases"][idx]
    total = len(state["queue"])
    return f"{state['pos']+1}/{total}\n\nHow do you say:\n\"{phrase['english']}\""


def start_drill_state(entry: dict) -> dict:
    queue = random.sample(DRILL_PERSONS, len(DRILL_PERSONS))
    return {
        "verb":             entry["verb"],
        "english":          entry.get("english", ""),
        "conjugations":     entry.get("conjugations", {}),
        "queue":            queue,
        "pos":              0,
        "current":          queue[0],
        "score":            0,
        "total":            0,
        "retry":            False,
        "items":            [],
        "hint_used_current": False,
        "l1_worst_tag":     "drill-clean",
    }


def record_l2_item(state: dict, phrase: dict, wrong_count: int, auto_revealed: bool) -> None:
    tag = item_tag(wrong_count, state.get("hint_used_current", False), auto_revealed)
    state["items"].append({
        "front":  phrase["english"],
        "back":   phrase["german"],
        "result": tag,
        "tags":   f"{tag} Vienna phrase {state['verb']}",
    })
    state["hint_used_current"] = False


def record_l1_person(state: dict, wrong_count: int, auto_revealed: bool) -> None:
    tag = item_tag(wrong_count, state.get("hint_used_current", False), auto_revealed)
    priority = {"drill-clean": 0, "drill-reinforced": 1, "needs-practice": 2}
    if priority.get(tag, 0) > priority.get(state.get("l1_worst_tag", "drill-clean"), 0):
        state["l1_worst_tag"] = tag
    state["hint_used_current"] = False


def finalize_l1_items(state: dict) -> None:
    """Convert l1_worst_tag into one Anki item if any friction occurred."""
    tag = state.get("l1_worst_tag", "drill-clean")
    if tag == "drill-clean":
        return
    conj = state.get("conjugations", {})
    table = " / ".join(
        f"{dp} {conj.get(pp, '?')}"
        for dp, pp in zip(PERSONS_DISPLAY, PERSONS_POOL)
    )
    state["items"].append({
        "front":  f"{state['verb']} — {state.get('english', '')}",
        "back":   table,
        "result": tag,
        "tags":   f"{tag} Vienna conjugation",
    })
