"""Loading, running and grading the owner-evidence suite. See the package docstring. Standard library only."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

MODES = ("cold", "warm", "variant")
DEFAULT_TRIALS = 3
RUBRICS = ("judgement", "personality")
SCHEMA = "minimoi.owner_evidence.suite/1"

# Saying a prior item or date back to him. A cold run has no memory, so saying this is invention.
_CITES_MEMORY = re.compile(r"(?:#\d+\b|\byou (?:asked|said|raised|mentioned)\b.{0,40}\b(?:on|last|earlier|before)\b|\bearlier (?:item|decision|request)\b"
                           r"|\b(?:we|you) (?:already )?(?:decided|agreed)\b)", re.I | re.S)
_RECOMMENDS = re.compile(r"\b(?:i(?:'d| would)|my (?:take|recommendation|view)|i (?:recommend|suggest|think)|better to|the risk|instead|because|rather than|worth)\b", re.I)
_WORD = re.compile(r"[^\W\d_]{4,}", re.U)
PARAPHRASE_OVERLAP = 0.7


@dataclass(frozen=True)
class Case:
    id: str
    words: str
    source: dict
    modes: tuple
    must_call: tuple = ()
    must_not_call: tuple = ()
    may_call: tuple = ()
    must_be_substantive: bool = False
    memory: tuple = ()                                   # what a warm run is given
    earlier_ref: str = ""                                # what warm and variant must refer to, e.g. "#212"
    variant_words: str = ""
    note: str = ""


@dataclass(frozen=True)
class Suite:
    version: str
    cases: tuple
    digest: str


@dataclass(frozen=True)
class Bundle:
    """Every run pins what was tested. A different bundle is a different run."""
    model: str
    persona_hash: str
    tool_policy_revision: str
    suite_version: str
    environment: str

    def missing(self) -> list:
        return [k for k, v in asdict(self).items() if not v]


@dataclass
class Grade:
    case: str
    mode: str
    trial: int
    hard_failures: list = field(default_factory=list)
    reply: str = ""
    tools: list = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.hard_failures


@dataclass
class Run:
    bundle: Bundle
    grades: list
    suite_digest: str
    expected: int = 0

    @property
    def digest(self) -> str:
        """Names the whole frozen bundle and suite. A rubric is good only for this."""
        return "sha256:" + hashlib.sha256(json.dumps({"bundle": asdict(self.bundle), "suite": self.suite_digest}, sort_keys=True).encode()).hexdigest()


def load(path) -> Suite:
    """A corpus file, checked. A case with no source is refused: every case is traceable to where it came from."""
    raw = Path(path).read_bytes()
    doc = json.loads(raw)
    if doc.get("schema") != SCHEMA or not isinstance(doc.get("version"), str) or not doc["version"]:
        raise ValueError("not an owner-evidence corpus")
    cases, seen = [], set()
    for row in doc["cases"]:
        modes = tuple(row["modes"])
        if not row.get("id") or row["id"] in seen or not row.get("words") or not modes or set(modes) - set(MODES):
            raise ValueError(f"bad case: {row.get('id')}")
        source = row.get("source")
        if not isinstance(source, dict) or not source.get("kind") or not source.get("ref") or not source.get("date"):
            raise ValueError(f"case {row['id']} has no source")
        if "warm" in modes and not (row.get("memory") and row.get("earlier_ref")):
            raise ValueError(f"warm case {row['id']} needs memory and what it must refer to")
        if "variant" in modes and not (row.get("variant_words") and row.get("earlier_ref")):
            raise ValueError(f"variant case {row['id']} needs its changed words and what it must refer to")
        seen.add(row["id"])
        cases.append(Case(id=row["id"], words=row["words"], source=source, modes=modes, must_call=tuple(row.get("must_call", ())),
                          must_not_call=tuple(row.get("must_not_call", ())), may_call=tuple(row.get("may_call", ())),
                          must_be_substantive=bool(row.get("must_be_substantive")), memory=tuple(row.get("memory", ())),
                          earlier_ref=row.get("earlier_ref", ""), variant_words=row.get("variant_words", ""), note=row.get("note", "")))
    return Suite(doc["version"], tuple(cases), "sha256:" + hashlib.sha256(raw).hexdigest())


def _words(text: str) -> set:
    return {w.casefold() for w in _WORD.findall(text)}


def only_paraphrases(owner: str, reply: str) -> bool:
    """The filing clerk: a reply that is mostly his own words, with no reasoning of its own. A hard-rule proxy only; whether the
    thinking is any good is Robert's to grade."""
    said, back = _words(owner), _words(reply)
    if not back:
        return True
    # Reasoning words count only when they are the reply's own: "instead", echoed from his message, is not a thought.
    if any(m.group(0).casefold() not in owner.casefold() for m in _RECOMMENDS.finditer(reply)):
        return False
    return len(back & said) / len(back) >= PARAPHRASE_OVERLAP or len(back) < 8


def grade(case: Case, mode: str, trial: int, reply: str, tools: list) -> Grade:
    """The deterministic part only. ``tools`` is what MC actually called, from its tool records, never from what it says it did."""
    g = Grade(case.id, mode, trial, reply=reply, tools=list(tools))
    fail = g.hard_failures.append
    called = set(tools)
    allowed = set(case.must_call) | set(case.may_call)
    for name in case.must_call:
        if name not in called:
            fail(f"did not call {name}")
    for name in case.must_not_call:
        if name in called:
            fail(f"called forbidden {name}")
    if allowed:
        for name in sorted(called - allowed):
            fail(f"called unexpected {name}")
    if case.must_be_substantive and only_paraphrases(case.words, reply):
        fail("only paraphrased and filed")
    if mode == "cold" and _CITES_MEMORY.search(reply):
        fail("cited memory it was not given")
    if mode in ("warm", "variant") and case.earlier_ref and case.earlier_ref not in reply:
        fail(f"did not recognise {case.earlier_ref}")
    if mode == "variant":
        if "minimoi_build_log_add" in called:
            fail("opened a second item")
        if only_paraphrases(case.variant_words, reply):
            fail("repeated without thinking in context")
    return g


def run(suite: Suite, bundle: Bundle, transport: Callable, *, trials: int = DEFAULT_TRIALS) -> Run:
    """Each case, each of its modes, ``trials`` times. ``transport(words, memory=tuple, case_id, mode, trial) -> (reply, tools)``.
    A run on an incomplete bundle is refused: an unpinned result can't be compared with anything."""
    if bundle.missing():
        raise ValueError(f"bundle is missing {bundle.missing()}")
    if bundle.suite_version != suite.version:
        raise ValueError("bundle names a different suite version")
    if type(trials) is not int or trials < 1:
        raise ValueError("at least one trial")
    if not suite.cases:
        raise ValueError("an empty suite proves nothing")
    grades = []
    for case in suite.cases:
        for mode in case.modes:
            words = case.variant_words if mode == "variant" else case.words
            memory = () if mode == "cold" else case.memory
            for trial in range(1, trials + 1):
                reply, tools = transport(words, memory=memory, case_id=case.id, mode=mode, trial=trial)
                grades.append(grade(case, mode, trial, reply, tools))
    return Run(bundle, grades, suite.digest, expected=sum(len(c.modes) for c in suite.cases) * trials)


def gate(result: Run, rubric: dict | None) -> dict:
    """Whether a persona release may ship. Any trial failing a hard rule blocks it, and no average can hide one. The rubric is
    Robert's own, per rubric name: {"judgement": "pass"|"fail", "personality": ..., "run": <Run.digest>}."""
    failed = [asdict(g) for g in result.grades if not g.passed]
    reasons = []
    if not result.grades or len(result.grades) != result.expected:
        reasons.append("the run did not cover the whole suite")
    if failed:
        reasons.append(f"{len(failed)} of {len(result.grades)} trials failed a hard rule")
    if not rubric:
        reasons.append("Robert has not graded the rubric")
    else:
        if rubric.get("run") != result.digest:
            reasons.append("the rubric was graded on a different run")
        for name in RUBRICS:
            if rubric.get(name) != "pass":
                reasons.append(f"{name} is not passed")
    return {"open": not reasons, "reasons": reasons, "failures": failed, "bundle": asdict(result.bundle), "suite": result.suite_digest, "run": result.digest}


def sample_sheet(result: Run, *, per_case: int = 1) -> str:
    """What Robert reads to grade the rubric: real replies, one trial per case and mode by default, nothing rated for him."""
    lines = [f"# Owner-evidence sample, {result.bundle.suite_version}", "",
             f"Model {result.bundle.model} · persona {result.bundle.persona_hash} · policy {result.bundle.tool_policy_revision} · "
             f"{result.bundle.environment} · run {result.digest}", "",
             "Grade **judgement** (right reading, right action or none, evidence, a real recommendation, useful disagreement, follow-through) "
             "and **personality** (the longtime partner: easy, direct, sometimes funny; no machinery talk; survives correction).", ""]
    for g in result.grades:
        if g.trial > per_case:
            continue
        lines += [f"## {g.case} · {g.mode}", "", g.reply or "(no reply)", "", f"Tools: {', '.join(g.tools) or 'none'}", ""]
        if g.hard_failures:
            lines += [f"Hard failures: {'; '.join(g.hard_failures)}", ""]
    return "\n".join(lines)
