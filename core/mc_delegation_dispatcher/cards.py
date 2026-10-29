"""Cards: one owner approval of a card's digest admits its enumerated steps, and the authority writes each next step
itself (host runtime spec v0.5 R4.3-c with Robert's N1, R4.3-d, R4.3-f, R6.2, R6.8, R7.4-a/b).

A card class is a catalogue row (card_classes.json), never code: its roles, its step kinds in order, and per kind a
template: scope, a brief frame with one ``{brief}`` placeholder, required capabilities, which earlier outputs the step
takes (``inputs``: ``"card"`` for the card's own, or artifact kinds, each taken from the latest earlier step that
recorded one), when it follows (``after``: an artifact kind the step before it must have recorded, else always), and
whether the card states how many times it repeats (``repeat``, the card's ``rounds``). A card names its class, a
worker (actor, route, amount in the route's unit, dollar cap) for each role, its words for each step kind, its attempt
allowance and rounds (N3), its own input artifacts, a per-step deadline, and optionally an owner-approved lifetime
beyond a day (R4.3-f), the paths it may touch and an estimate (§7.7's drift rules). Robert approves its digest.

Compilation is a pure function of (card, class row, families). Each enumerated step gets a template digest over every
fixed field of its request (worker, scope, repository, exact commit, brief, route, amount, dollar cap, required
capabilities) with the card's digest and the class pin. The card grant (delegate.card) names exactly the card steps'
digests, one operation each (GrantStore's card rule). The only free field of a compiled step is its input artifacts:
digests the authority itself recorded as outputs of an earlier step of the same card, chosen by the template. A step
whose worker is of another model family than the card's first step is never a card step: the grant does not name it,
and it needs its own admission (a cross-vendor review, R6.2, T13).

Runs on Python 3.9, like the rest of this package (the production bundle compiles every file in it): GrantStore is
imported only where a card grant is built.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

CARD_SCHEMA = "minimoi.card.v1"
STEP_SCHEMA = "minimoi.card.step.v1"
CLASSES_SCHEMA = "minimoi.card.classes.v1"
CARD_CAPABILITY = "delegate.card"          # GrantStore's card rule (core/minimoi_actions/grants.py)
CARD_POLICY = "minimoi.runtime.card.v1"    # the policy version a card grant is issued under
CARD_ACTOR = "authority"                   # who acts under a card grant: the authority admits each step
CLASSES_PATH = Path(__file__).with_name("card_classes.json")
MAX_STEPS = 100                            # GrantDefinition's bound on a grant's digests and operations
MAX_ATTEMPTS = 10
MAX_INPUTS = 16                            # the envelope's bound on input artifacts (envelope.MAX_ARTIFACTS)
MAX_BRIEF = 16_000                         # the envelope's bound on a brief
DAY_S = 86_400                             # without an owner-approved lifetime a card grant lives a day (R4.3-f)
MAX_LIFETIME_S = 30 * DAY_S
SCOPES = ("research", "build")
_CARD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,39}$")    # leaves room for "<card>.<step>" as a job id
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,15}$")
_ACTOR = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_REF = re.compile(r"^[0-9a-f]{40}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_ART_REF = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
_ROUTE = re.compile(r"^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)*$")
_CAP = re.compile(r"^[a-z][a-z0-9_-]{0,15}:[A-Za-z0-9._:-]{1,64}$")
_PATH = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[A-Za-z0-9._/@+-]{1,256}$")
_STEP_FIELDS = {"kind", "role", "scope", "brief", "required_capabilities", "inputs", "after", "repeat"}
_REQUIRED = {"schema", "card_id", "class", "class_version", "repository", "ref", "workers", "briefs", "attempts",
             "step_deadline_s"}
_OPTIONAL = {"rounds": {}, "input_artifacts": [], "required_capabilities": [], "lifetime_s": None,
             "paths_allowed": [], "estimate_s": None}


class CardRefused(ValueError):
    """A card or class the authority will not compile: a fixed code, never the card's content."""


def _need(ok, code):
    if not ok:
        raise CardRefused(code)


def digest_of(doc) -> str:
    """The canonical digest: sorted keys, no whitespace, UTF-8 (as the MC policy's digest_of)."""
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


def _int(value, low, high) -> bool:
    return type(value) is int and low <= value <= high


def _fixed(value, places) -> str:
    """An amount as a fixed-point string with ``places`` decimals: from a number, a Decimal or such a string."""
    try:
        d = value if type(value) is Decimal else Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise CardRefused("card_amount_invalid") from None
    step = Decimal(1).scaleb(-places)
    _need(type(value) is not bool and d.is_finite() and d > 0 and d == d.quantize(step), "card_amount_invalid")
    return format(d.quantize(step), "f")


def _caps(caps) -> list:
    _need(type(caps) is list and len(caps) <= 64 and all(type(c) is str and _CAP.match(c) for c in caps)
          and len(set(caps)) == len(caps), "card_capabilities_invalid")
    return sorted(caps)


def _artifacts(arts) -> list:
    _need(type(arts) is list and len(arts) <= MAX_INPUTS and all(
        type(a) is dict and set(a) == {"ref", "sha256"} and type(a["ref"]) is str and _ART_REF.match(a["ref"])
        and type(a["sha256"]) is str and _SHA.match(a["sha256"]) for a in arts), "card_input_artifacts_invalid")
    return [{"ref": a["ref"], "sha256": a["sha256"]} for a in arts]


def recorded_outputs(artifacts) -> list:
    """The well-formed artifacts of a recorded result ({kind, ref, sha256} and whatever else the host reported):
    only these can become a later step's inputs."""
    return [a for a in (artifacts if type(artifacts) is list else [])
            if type(a) is dict and type(a.get("kind")) is str and _NAME.match(a["kind"])
            and type(a.get("ref")) is str and _ART_REF.match(a["ref"])
            and type(a.get("sha256")) is str and _SHA.match(a["sha256"])]


# ── the catalogue (R6.8): card classes as rows ─────────────────────────────────────────────────────────────────
def check_class(name, row) -> dict:
    """One class row, strictly: its roles, its step kinds in order, and each kind's template."""
    _need(type(name) is str and _NAME.match(name), "card_class_invalid")
    _need(type(row) is dict and set(row) <= {"version", "note", "roles", "steps"}
          and {"version", "roles", "steps"} <= set(row), "card_class_invalid")
    _need(_int(row["version"], 1, 10**6) and type(row.get("note", "")) is str, "card_class_invalid")
    roles, steps = row["roles"], row["steps"]
    _need(type(roles) is list and 1 <= len(roles) <= 8 and all(type(r) is str and _NAME.match(r) for r in roles)
          and len(set(roles)) == len(roles), "card_class_roles_invalid")
    _need(type(steps) is list and 1 <= len(steps) <= 16, "card_class_steps_invalid")
    kinds = []
    for i, s in enumerate(steps):
        _need(type(s) is dict and set(s) == _STEP_FIELDS, "card_class_steps_invalid")
        _need(type(s["kind"]) is str and _NAME.match(s["kind"]) and s["kind"] not in kinds
              and s["role"] in roles and s["scope"] in SCOPES and type(s["repeat"]) is bool, "card_class_steps_invalid")
        _need(type(s["brief"]) is str and s["brief"].count("{brief}") == 1 and len(s["brief"]) <= 2000,
              "card_class_brief_invalid")
        try:
            _caps(s["required_capabilities"])
        except CardRefused:
            raise CardRefused("card_class_steps_invalid") from None
        inputs = s["inputs"]
        # Only the first step takes the card's own inputs; every other step takes recorded outputs of earlier ones.
        _need((inputs == "card") == (i == 0) and (inputs == "card" or (
            type(inputs) is list and len(inputs) <= 8 and all(type(k) is str and _NAME.match(k) for k in inputs)
            and len(set(inputs)) == len(inputs))), "card_class_inputs_invalid")
        _need(s["after"] is None or (type(s["after"]) is str and _NAME.match(s["after"]) and i > 0),
              "card_class_after_invalid")
        _need(i > 0 or not s["repeat"], "card_class_steps_invalid")          # a card always has its first step
        kinds.append(s["kind"])
    return row


@dataclass(frozen=True)
class Step:
    index: int                    # its place in the card's enumerated plan
    name: str                     # the kind, or "<kind>-<k>" for the k-th of a repeated kind
    kind: str
    role: str
    actor: str
    scope: str
    brief: str
    route: str
    amount: Decimal               # in the route's unit (§8.1 record 1)
    cost_cap_usd: Decimal
    required_capabilities: tuple
    inputs: object                # "card", or the artifact kinds it takes, in order
    after: str | None             # an artifact kind the step before it must have recorded, or None (always)
    on_card: bool                 # the card grant names it; otherwise it needs its own admission
    digest: str                   # its template digest


@dataclass(frozen=True)
class CompiledCard:
    card: dict                    # the card as normalized: its digest is what the owner approves
    digest: str
    class_name: str
    class_row: dict
    families: dict
    class_digest: str             # the class row and families this card was compiled under, pinned
    steps: tuple

    @property
    def card_id(self) -> str:
        return self.card["card_id"]

    @property
    def grant_digests(self) -> tuple:
        """What the card grant names: the card steps' template digests, one operation each (R4.3-c)."""
        return tuple(s.digest for s in self.steps if s.on_card)

    def step(self, name):
        return next((s for s in self.steps if s.name == name), None)

    def following(self, step, outputs):
        """The step the authority writes once ``step`` completed with ``outputs`` (its recorded artifacts), or None
        when the card is done: the plan's next step, unless that step follows only on an artifact kind the completed
        step did not record (a fix only after findings)."""
        if step.index + 1 >= len(self.steps):
            return None
        nxt = self.steps[step.index + 1]
        if nxt.after is not None and nxt.after not in {a["kind"] for a in recorded_outputs(outputs)}:
            return None
        return nxt

    def inputs(self, step, earlier) -> tuple:
        """(input_artifacts, inputs_from) for ``step``, its only free field, filled from the authority's records:
        the card's own inputs for the first step; otherwise, for each artifact kind its template names, the artifacts
        of that kind recorded by the latest earlier completed step that has any. ``earlier`` holds the card's
        completed steps as recorded: [{step, item_id, artifacts}]. ``inputs_from`` names each input's source."""
        if step.inputs == "card":
            arts = [dict(a) for a in self.card["input_artifacts"]]
            return arts, [{"from": "card", "ref": a["ref"], "sha256": a["sha256"]} for a in arts]
        done = {r["step"]: r for r in earlier}
        prior = [s for s in self.steps[:step.index] if s.name in done]
        arts, sources = [], []
        for kind in step.inputs:
            for s in reversed(prior):
                found = [a for a in recorded_outputs(done[s.name]["artifacts"]) if a["kind"] == kind]
                if found:
                    for a in found:
                        arts.append({"ref": a["ref"], "sha256": a["sha256"]})
                        sources.append({"from": s.name, "item_id": str(done[s.name]["item_id"]), "kind": kind,
                                        "ref": a["ref"], "sha256": a["sha256"]})
                    break
        _need(len(arts) <= MAX_INPUTS, "card_inputs_too_many")
        return arts, sources


def step_digest(*, card_id, card_digest, class_digest, step, kind, actor, scope, repository, ref, brief, route,
                amount, cost_cap_usd, required_capabilities) -> str:
    """A step's template digest: every fixed field of its request, with its card and class. Input artifacts, the
    only free field, are not in it."""
    return digest_of({"schema": STEP_SCHEMA, "card_id": card_id, "card_digest": card_digest,
                      "class_digest": class_digest, "step": step, "kind": kind, "actor": actor, "scope": scope,
                      "repository": repository, "ref": ref, "brief": brief, "route": route,
                      "amount": _fixed(amount, 4), "cost_cap_usd": _fixed(cost_cap_usd, 2),
                      "required_capabilities": sorted(required_capabilities)})


def row_step_digest(row) -> str | None:
    """The template digest of a step as the authority stored it (its item row, recomputed from its columns), or None
    when the row is not a card step. A row that differs in any fixed field from what was compiled has another digest,
    which the card grant does not name."""
    card = (row.get("facts") or {}).get("card")
    if not card:
        return None
    try:
        return step_digest(card_id=row["card_id"], card_digest=card["digest"], class_digest=card["class_digest"],
                           step=row["step"], kind=card["kind"], actor=row["actor"], scope=row["scope"],
                           repository=row["repository"], ref=row["ref"], brief=row["brief"], route=row["route"],
                           amount=(row["reservation"] or {}).get("requested"), cost_cap_usd=row["cost_cap_usd"],
                           required_capabilities=list(row["required_capabilities"] or []))
    except (CardRefused, KeyError, TypeError):
        return None


def _normalize(doc, name, row, families) -> dict:
    """The card as approved: every field checked, the optional ones filled, amounts as fixed-point strings."""
    _need(type(doc) is dict and _REQUIRED <= set(doc) <= _REQUIRED | set(_OPTIONAL), "card_fields_invalid")
    card = {**json.loads(json.dumps(_OPTIONAL)), **doc}
    _need(card["schema"] == CARD_SCHEMA, "card_schema_invalid")
    _need(type(card["card_id"]) is str and _CARD_ID.match(card["card_id"]), "card_id_invalid")
    _need(card["class"] == name and card["class_version"] == row["version"], "card_class_unknown")
    _need(type(card["repository"]) is str and _REPO.match(card["repository"]), "card_repository_invalid")
    _need(type(card["ref"]) is str and _REF.match(card["ref"]), "card_ref_invalid")
    roles = set(row["roles"])
    workers = card["workers"]
    _need(type(workers) is dict and set(workers) == roles, "card_workers_invalid")
    norm = {}
    for role, w in workers.items():
        _need(type(w) is dict and set(w) == {"actor", "route", "amount", "cost_cap_usd"}
              and w["actor"] in families and type(w["route"]) is str and len(w["route"]) <= 64
              and _ROUTE.match(w["route"]), "card_workers_invalid")
        cap = _fixed(w["cost_cap_usd"], 2)
        _need(Decimal(cap) <= Decimal("999999.99"), "card_amount_invalid")         # the item's NUMERIC(8,2)
        amount = _fixed(w["amount"], 4)
        _need(Decimal(amount) <= 10**6, "card_amount_invalid")
        norm[role] = {"actor": w["actor"], "route": w["route"], "amount": amount, "cost_cap_usd": cap}
    card["workers"] = norm
    kinds = [s["kind"] for s in row["steps"]]
    briefs = card["briefs"]
    _need(type(briefs) is dict and set(briefs) == set(kinds) and all(
        type(b) is str and b.strip() and len(b) <= MAX_BRIEF for b in briefs.values()), "card_briefs_invalid")
    _need(_int(card["attempts"], 1, MAX_ATTEMPTS), "card_attempts_invalid")
    rounds = card["rounds"]
    _need(type(rounds) is dict and set(rounds) == {s["kind"] for s in row["steps"] if s["repeat"]}
          and all(_int(n, 0, MAX_STEPS) for n in rounds.values()), "card_rounds_invalid")
    card["input_artifacts"] = _artifacts(card["input_artifacts"])
    card["required_capabilities"] = _caps(card["required_capabilities"])
    _need(_int(card["step_deadline_s"], 60, 7 * DAY_S), "card_deadline_invalid")
    _need(card["lifetime_s"] is None or _int(card["lifetime_s"], 60, MAX_LIFETIME_S), "card_lifetime_invalid")
    paths = card["paths_allowed"]
    _need(type(paths) is list and len(paths) <= 200 and all(type(p) is str and _PATH.match(p) for p in paths)
          and len(set(paths)) == len(paths), "card_paths_invalid")
    card["paths_allowed"] = sorted(paths)
    _need(card["estimate_s"] is None or _int(card["estimate_s"], 1, MAX_LIFETIME_S), "card_estimate_invalid")
    return card


def compile_card(doc, name, row, families) -> CompiledCard:
    """Every enumerated step of the card ``doc`` under the class row ``row`` (named ``name``) and ``families``, in
    plan order, each with its template digest. Pure: the same inputs give the same card on every host."""
    check_class(name, row)
    card = _normalize(doc, name, row, families)
    card_digest = digest_of(card)
    class_digest = digest_of({"class": name, "row": row, "families": families})
    plan = []
    for entry in row["steps"]:
        count = card["rounds"][entry["kind"]] if entry["repeat"] else 1
        plan += [(f"{entry['kind']}-{k}" if entry["repeat"] else entry["kind"], entry) for k in range(1, count + 1)]
    _need(len(plan) <= MAX_STEPS, "card_too_many_steps")
    family = families[card["workers"][row["steps"][0]["role"]]["actor"]]
    steps = []
    for index, (step_name, entry) in enumerate(plan):
        w = card["workers"][entry["role"]]
        caps = tuple(sorted(set(entry["required_capabilities"]) | set(card["required_capabilities"])))
        _need(len(caps) <= 64, "card_capabilities_invalid")
        brief = entry["brief"].replace("{brief}", card["briefs"][entry["kind"]])
        _need(len(brief) <= MAX_BRIEF, "card_briefs_invalid")
        digest = step_digest(card_id=card["card_id"], card_digest=card_digest, class_digest=class_digest,
                             step=step_name, kind=entry["kind"], actor=w["actor"], scope=entry["scope"],
                             repository=card["repository"], ref=card["ref"], brief=brief, route=w["route"],
                             amount=w["amount"], cost_cap_usd=w["cost_cap_usd"], required_capabilities=caps)
        inputs = "card" if entry["inputs"] == "card" else tuple(entry["inputs"])
        steps.append(Step(index, step_name, entry["kind"], entry["role"], w["actor"], entry["scope"], brief,
                          w["route"], Decimal(w["amount"]), Decimal(w["cost_cap_usd"]), caps, inputs, entry["after"],
                          families[w["actor"]] == family, digest))
    return CompiledCard(card, card_digest, name, row, dict(families), class_digest, tuple(steps))


def recompile(record) -> CompiledCard:
    """The card again from the authority's own record of its admission (card.admitted), under the class row and
    families it pinned, so a later catalogue change never alters an admitted card. Refused unless every digest
    is as recorded."""
    try:
        compiled = compile_card(record["card"], record["class"], record["class_row"], record["families"])
    except (KeyError, TypeError):
        raise CardRefused("card_record_invalid") from None
    _need(compiled.digest == record.get("card_digest") and compiled.class_digest == record.get("class_digest")
          and [s.digest for s in compiled.steps] == [s.get("digest") for s in record.get("steps") or []],
          "card_record_invalid")
    return compiled


def card_grant(compiled, *, grant_id, principal, environment, owner_turn_ids, proposal_id, approved_operation_id,
               approval_expires_at, now):
    """The delegate.card grant the owner path issues on the owner's exact approval of ``compiled``'s digest: it names
    the card steps' template digests, one operation each; its work id is the card; its lifetime is the card's own
    owner-approved lifetime, else a day (R4.3-f). The approval window (at most 15 minutes, GrantStore) bounds the
    admission of the card's first step."""
    from core.minimoi_actions.grants import GrantDefinition       # Python 3.10 or later: the authority only
    life = compiled.card["lifetime_s"] or DAY_S
    return GrantDefinition(grant_id, principal, environment, compiled.card_id, tuple(owner_turn_ids), (CARD_ACTOR,),
                           CARD_POLICY, CARD_CAPABILITY, 1, compiled.grant_digests, len(compiled.grant_digests),
                           now + timedelta(seconds=life), work_id=compiled.card_id, proposal_id=proposal_id,
                           approved_operation_id=approved_operation_id, approval_expires_at=approval_expires_at)


def grant_rows() -> dict:
    """The grant catalogue row cards need (R6.8's "grant catalogue rows"): delegate.card, version 1, one exact owner
    approval, zero spend: spend is the authority's reservation ledger (§8.3), never a grant's."""
    from core.minimoi_actions.grants import CapabilityRule       # Python 3.10 or later: the authority only
    return {CARD_CAPABILITY: CapabilityRule(1, True, True)}


@dataclass(frozen=True)
class Catalogue:
    """The card classes the authority admits cards of, and each worker's model family."""
    families: dict
    classes: dict

    @classmethod
    def load(cls, path=CLASSES_PATH) -> "Catalogue":
        with open(Path(path), "rb") as fh:
            raw = fh.read(256 * 1024 + 1)
        _need(len(raw) <= 256 * 1024, "card_catalogue_too_large")
        try:
            doc = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise CardRefused("card_catalogue_unreadable") from None
        return cls.parse(doc)

    @classmethod
    def parse(cls, doc) -> "Catalogue":
        _need(type(doc) is dict and set(doc) <= {"schema", "note", "families", "classes"}
              and doc.get("schema") == CLASSES_SCHEMA and type(doc.get("note", "")) is str, "card_catalogue_invalid")
        families = doc.get("families")
        _need(type(families) is dict and 1 <= len(families) <= 32 and all(
            type(a) is str and _ACTOR.match(a) and type(f) is str and _NAME.match(f) for a, f in families.items()),
            "card_catalogue_families_invalid")
        classes = doc.get("classes")
        _need(type(classes) is dict and len(classes) <= 64, "card_catalogue_invalid")
        for name, row in classes.items():
            check_class(name, row)
        return cls(dict(families), dict(classes))

    def compile(self, doc) -> CompiledCard:
        name = doc.get("class") if type(doc) is dict else None
        row = self.classes.get(name) if type(name) is str else None
        _need(row is not None and type(doc.get("class_version")) is int and row["version"] == doc["class_version"],
              "card_class_unknown")
        return compile_card(doc, name, row, self.families)
