"""What a control document is allowed to *say*, apart from where it is stored.

Two questions sit between the request surface and the disk tree, and neither
belongs to either of them.

*Which pending object does this effect publish?* A pending object is not "a
control record that happens to validate"; it is the exact document one write
site emits, and the writer fixes most of it mechanically — which subtree the
bytes went into, which kind of reference names them, which provenance class
the effect always states, whether a revision number or a superseded reference
is part of that shape at all. Those are statements about what an effect
*means*. :mod:`store` deliberately has no effect vocabulary (see its own
opening lines and the design's module table), so the table lives here and the
store applies the row it resolves.

*Which evidence may one record pin?* The ``based_on`` contract has four rules
beyond the shape of a single entry: at most
:data:`MAX_EVIDENCE_ENTRIES` inputs, no reference twice, no artifact naming
itself, and an ``approved:`` reference only for the record's own subject. The
request parser and the stored-record parser both have to hold to them, and a
second, independently chosen copy of "32" or of the approved-reference
prefix rule is exactly how the two drift apart — the stored parser once
admitted 64 entries, duplicates, self-references and another subject's
approved text, all of which the request parser refused. They are defined once
here, and each caller keeps its own wording: the request parser answers a
caller in caller-facing terms, the stored parser answers about a document
this service wrote. The rule they are answering about is the same object.

This module is provider-neutral and does no I/O. It knows nothing about
paths, locks, publication or recovery, and nothing about transport, product
or model identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from . import records
from .envelope import SOURCE_CONTEXT_CLASSES
from .retrieval import APPROVED_ROOT_PREFIX

__all__ = [
    "MAX_EVIDENCE_ENTRIES",
    "MAX_EVIDENCE_REF_CHARS",
    "EVIDENCE_RULES",
    "EvidenceViolation",
    "PENDING_VARIANTS",
    "PendingVariant",
    "approved_evidence_subject",
    "check_evidence_count",
    "check_evidence_not_duplicate",
    "check_evidence_not_self",
    "check_evidence_ref_text",
    "check_evidence_refs",
    "check_evidence_subject",
    "variant_for",
]


# --------------------------------------------------------------------------
# The per-effect pending variant
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PendingVariant:
    """The one pending shape a single effect is allowed to have published.

    Stating the shape as data — one row per effect — is what makes "this is
    not a document we ever wrote" decidable, rather than a sequence of
    partial conditionals that can only notice the disagreements two
    populated fields happen to expose.

    The table is closed in both directions: an effect with no row here never
    publishes a pending object, and a row admits nothing beyond what its own
    write site produces.
    """

    #: The subtree this effect's bytes go into, or ``None`` when the effect
    #: writes no bytes of its own and therefore carries no content group.
    subtree: str | None = None
    #: The reference kind naming those bytes.
    ref_prefix: str | None = None
    #: The provenance classes this effect may state. A single-member set is
    #: a value the writer fixes, not a choice the record gets to make.
    context_classes: frozenset[str] = frozenset()
    #: Whether the content group carries a revision number.
    revision: bool = False
    #: ``"forbidden"`` or ``"required"``. Nothing here is optional: section
    #: 6.2 gives ``supersedes_ref`` to ``use_robert_edit`` only, and always.
    supersedes: str = "forbidden"
    #: Whether this effect may pin ``based_on`` evidence at all. An effect
    #: that never writes evidence must carry an empty ``expected_inputs``,
    #: not merely a well-formed one.
    based_on: bool = False
    #: ``"forbidden"``, or ``"reopen_required"`` for the one effect that may
    #: write the conversation pointer: permitted when the same operation
    #: creates the record, required when it reopens an existing one, because
    #: reopening with nothing to bind publishes no pending object at all.
    binding: str = "forbidden"
    #: Whether this effect may publish a record that did not exist before.
    creates_record: bool = False


#: The closed set of pending objects this service can write, one row per
#: effect. The two read effects are absent on purpose: a read publishes a
#: receipt and nothing else, so a pending object naming one is a document
#: this writer cannot have produced, and recovery must never act on it.
PENDING_VARIANTS: dict[str, PendingVariant] = {
    "open_work": PendingVariant(binding="reopen_required", creates_record=True),
    "attach_source": PendingVariant(
        subtree=records.SOURCES_DIRNAME,
        ref_prefix="src-",
        context_classes=SOURCE_CONTEXT_CLASSES,
    ),
    "write_artifact": PendingVariant(
        subtree=records.ARTIFACTS_DIRNAME,
        ref_prefix="art-",
        context_classes=frozenset({"agent_draft"}),
        revision=True,
        based_on=True,
    ),
    "use_robert_edit": PendingVariant(
        subtree=records.ARTIFACTS_DIRNAME,
        ref_prefix="art-",
        context_classes=frozenset({"coauthored_output"}),
        revision=True,
        supersedes="required",
    ),
    "request_disposition": PendingVariant(),
    "record_disposition": PendingVariant(),
}


def variant_for(effect: str) -> PendingVariant | None:
    """The row for an effect, or ``None`` when the effect never writes one."""
    return PENDING_VARIANTS.get(effect)


# --------------------------------------------------------------------------
# The evidence-list contract (design §6.1)
# --------------------------------------------------------------------------

#: How many pinned inputs one artifact record may name.
MAX_EVIDENCE_ENTRIES = 32

#: How long one pinned input reference may be. The qualified ``approved:``
#: form is the long one; a derived handle is far shorter.
MAX_EVIDENCE_REF_CHARS = 256

#: The closed set of ways an evidence list can break this contract. A rule
#: name is not a message: each caller turns one into wording that suits who
#: it is answering.
EVIDENCE_RULES: frozenset[str] = frozenset(
    {
        "too_many",
        "ref_not_usable",
        "duplicate_ref",
        "self_reference",
        "foreign_subject",
    }
)


class EvidenceViolation(Exception):
    """One evidence rule, broken, with the reference that broke it.

    Deliberately not a :class:`~.envelope.WorkError`: this module states the
    rule and says nothing about how the refusal should be reported. The
    request parser raises ``invalid_request`` in caller-facing words; the
    stored-record parser raises its own closed control-record error about a
    document this service wrote. Both refuse; neither borrows the other's
    voice.
    """

    def __init__(self, rule: str, detail: str | None = None) -> None:
        if rule not in EVIDENCE_RULES:
            raise ValueError(f"unknown evidence rule: {rule}")
        super().__init__(rule)
        self.rule = rule
        self.detail = detail


def check_evidence_count(count: int) -> None:
    """At most :data:`MAX_EVIDENCE_ENTRIES` pinned inputs."""
    if count > MAX_EVIDENCE_ENTRIES:
        raise EvidenceViolation("too_many")


def check_evidence_ref_text(ref: object) -> str:
    """A reference is non-blank text within the length ceiling."""
    if not isinstance(ref, str) or not ref.strip() or len(ref) > MAX_EVIDENCE_REF_CHARS:
        raise EvidenceViolation("ref_not_usable", ref if isinstance(ref, str) else None)
    return ref


def check_evidence_not_duplicate(ref: str, seen: set[str]) -> None:
    """No reference twice, whatever digest each occurrence claims."""
    if ref in seen:
        raise EvidenceViolation("duplicate_ref", ref)


def check_evidence_not_self(ref: str, self_ref: str | None) -> None:
    """No artifact cites itself as its own input."""
    if self_ref is not None and ref == self_ref:
        raise EvidenceViolation("self_reference", ref)


def approved_evidence_subject(ref: str) -> str | None:
    """The subject an ``approved:`` reference names, or ``None`` for a handle.

    Only the subject segment is read here. What a legal path below it looks
    like is a question each caller answers in its own terms: the request
    parser normalises a path a caller supplied, the stored parser validates
    a path this service already wrote.
    """
    if not ref.startswith(APPROVED_ROOT_PREFIX):
        return None
    remainder = ref[len(APPROVED_ROOT_PREFIX) :]
    named_subject, slash, _rest = remainder.partition("/")
    if not slash:
        return ""
    return named_subject


def check_evidence_subject(ref: str, subject: str) -> None:
    """An approved reference may only name this record's own subject."""
    named = approved_evidence_subject(ref)
    if named is not None and named != subject:
        raise EvidenceViolation("foreign_subject", ref)


def check_evidence_refs(
    refs: Sequence[str],
    *,
    subject: str,
    self_ref: str | None = None,
) -> None:
    """Every list-level rule at once, for a caller with the whole list.

    The stored-record parser has the subject and the reference being created
    in hand, so it can apply all four rules in one place. The request parser
    reaches them at three different moments and calls the pieces directly;
    both go through the definitions above.
    """
    check_evidence_count(len(refs))
    seen: set[str] = set()
    for raw in refs:
        ref = check_evidence_ref_text(raw)
        check_evidence_not_duplicate(ref, seen)
        seen.add(ref)
        check_evidence_not_self(ref, self_ref)
        check_evidence_subject(ref, subject)
