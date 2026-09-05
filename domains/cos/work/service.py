"""The eight effects, and no ninth.

This module is where a request meets its authority and its resources. It
validates the envelope, verifies and spends the grant, resolves every
reference into something the store can act on, and builds the receipt. It
owns no transport, no product, and no second retrieval mechanism: reads go
through the accumulation reference and writes go through the store.

Three rules shape almost everything here.

*No effect names a path.* Every file reference is a root and a relative path,
or a handle already recorded in this work item. Every written path is derived
under the lock from a sequence number the record itself decides, so a caller
cannot choose where its bytes land.

*No effect chooses provenance.* What a capture is recorded as follows from
where its bytes came from — the declared class of a configured root, or the
class whoever minted the grant fixed for that single use. There is no
request field to argue with, which is why no request can move bytes into a
class they did not come from.

*Nothing reports success without a committed operation record.* A request
that mutated the tree returns a receipt read back from its own terminal
marker, re-validated through the receipt constructor, or it fails.
"""

from __future__ import annotations

import itertools
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from importlib import import_module
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import approval, grants, records, store
from .envelope import (
    PROPOSED_STATES,
    SOURCE_CONTEXT_CLASSES,
    WORK_CONTRACT_VERSION,
    InvalidRequest,
    Receipt,
    WorkError,
    error_response,
    make_receipt,
    require_effect,
    require_identifier,
    require_uuid4,
    success_response,
)
from .retrieval import (
    Accumulation,
    MAX_EXCERPT_CHARS_CEILING,
    MAX_RESULTS_CEILING,
    approved_root_ref,
    is_approved_root,
)
from .roots import RootConfiguration, load_root_configuration

confine = import_module(f"{__package__}.confine")

#: Textual limits. Over-limit is refused, never clamped: silently shortening
#: a caller's input would make the stored record disagree with what was asked
#: for, and nothing downstream could tell.
MAX_LABEL_CHARS = 120
MAX_INTENT_CHARS = 2000
MAX_ORIGIN_NOTE_CHARS = 200
MAX_REASON_CHARS = 500
MAX_BASED_ON_ENTRIES = 32
MAX_QUALIFIED_REF_CHARS = 256
MAX_CONVERSATION_ID_CHARS = 64

#: How long a pending approval stays answerable.
PENDING_TTL_SECONDS = 600

#: What inline text is stored as. Both members of the readable set are
#: legal; supplied text is plain, and a produced artifact is Markdown.
INLINE_SOURCE_EXTENSION = ".txt"
ARTIFACT_EXTENSION = ".md"

_SOURCE_SLUG_FALLBACK = "source"
_ARTIFACT_SLUG_FALLBACK = "letter"


@dataclass(frozen=True)
class EffectSpec:
    """One row of the effect matrix, as data rather than as branching code."""

    required: frozenset[str]
    optional: frozenset[str]
    exclusive: tuple[frozenset[str], ...]
    writes: bool

    @property
    def known(self) -> frozenset[str]:
        names = set(self.required) | set(self.optional)
        for group in self.exclusive:
            names |= set(group)
        return frozenset(names)


#: The parameter half of the effect matrix. The grant half lives in
#: :data:`grants.GRANT_BINDINGS`, and the two are compared against the
#: reviewed design by a test rather than kept in step by hand.
EFFECT_SPECS: dict[str, EffectSpec] = {
    "open_work": EffectSpec(
        required=frozenset({"subject"}),
        optional=frozenset({"work_id", "label", "intent", "conversation_id"}),
        exclusive=(),
        writes=True,
    ),
    "attach_source": EffectSpec(
        required=frozenset({"work_id"}),
        optional=frozenset({"origin_note", "filename_hint"}),
        exclusive=(frozenset({"content"}), frozenset({"file_ref"})),
        writes=True,
    ),
    "search_sources": EffectSpec(
        required=frozenset({"work_id", "query"}),
        optional=frozenset(
            {
                "root_refs",
                "max_results",
                "max_excerpt_chars",
                "max_files_examined",
                "max_bytes_examined",
            }
        ),
        exclusive=(),
        writes=False,
    ),
    "read_source": EffectSpec(
        required=frozenset({"work_id"}),
        optional=frozenset(),
        exclusive=(frozenset({"source_ref"}), frozenset({"file_ref"})),
        writes=False,
    ),
    "write_artifact": EffectSpec(
        required=frozenset({"work_id", "content"}),
        optional=frozenset({"based_on"}),
        exclusive=(),
        writes=True,
    ),
    "request_disposition": EffectSpec(
        required=frozenset({"work_id", "proposed_state"}),
        optional=frozenset({"artifact_ref"}),
        exclusive=(),
        writes=True,
    ),
    "record_disposition": EffectSpec(
        required=frozenset({"work_id", "pending_id", "confirmed_state"}),
        optional=frozenset({"reason"}),
        exclusive=(),
        writes=True,
    ),
    "use_robert_edit": EffectSpec(
        required=frozenset({"work_id", "supersedes_ref", "expected_sha256"}),
        optional=frozenset(),
        exclusive=(frozenset({"content"}), frozenset({"file_ref"})),
        writes=True,
    ),
}


def _utc(moment: datetime | None = None) -> datetime:
    return (moment or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _text_param(params: Mapping[str, Any], key: str, limit: int, *, required: bool) -> str | None:
    value = params.get(key)
    if value is None:
        if required:
            raise InvalidRequest(f"{key} is required")
        return None
    if not isinstance(value, str) or not value.strip():
        raise InvalidRequest(f"{key} must be text")
    if len(value) > limit:
        raise InvalidRequest(f"{key} is longer than this operation accepts")
    return value


def _content_param(params: Mapping[str, Any]) -> bytes:
    value = params.get("content")
    if not isinstance(value, str) or not value:
        raise InvalidRequest("content must be a non-empty string")
    raw = value.encode("utf-8")
    if len(raw) > confine.DEFAULT_MAX_FILE_BYTES:
        raise InvalidRequest("content is larger than this operation accepts")
    return raw


def _file_ref_param(params: Mapping[str, Any]) -> tuple[str, str]:
    value = params.get("file_ref")
    if not isinstance(value, Mapping):
        raise InvalidRequest("file_ref must name a root and a relative path")
    unknown = sorted(set(value) - {"root_ref", "relative_path"})
    if unknown:
        raise InvalidRequest("file_ref has fields that are not part of it")
    root_ref = value.get("root_ref")
    if is_approved_root(root_ref):
        require_identifier(root_ref[len("approved:") :], "subject")
    else:
        require_identifier(root_ref, "root_ref")
    relative = str(confine.normalise_relative(value.get("relative_path")))
    return str(root_ref), relative


def _based_on_param(params: Mapping[str, Any]) -> tuple[dict[str, str], ...]:
    value = params.get("based_on")
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise InvalidRequest("based_on must be a list of pinned inputs")
    if len(value) > MAX_BASED_ON_ENTRIES:
        raise InvalidRequest("based_on names more inputs than this operation accepts")
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"ref", "sha256"}:
            raise InvalidRequest("each based_on entry names a ref and a sha256")
        ref = item["ref"]
        if not isinstance(ref, str) or not ref.strip() or len(ref) > MAX_QUALIFIED_REF_CHARS:
            raise InvalidRequest("that reference is not a usable input")
        if ref in seen:
            raise InvalidRequest("based_on names the same input twice")
        seen.add(ref)
        digest = item["sha256"]
        if records.SHA256_PATTERN.fullmatch(str(digest)) is None:
            raise InvalidRequest("each based_on entry needs a lowercase hex sha256")
        entries.append({"ref": ref, "sha256": str(digest)})
    return tuple(entries)


def parse_approved_ref(raw: str, subject: str) -> str:
    """Parse the one qualified input reference the contract admits.

    The wrapper is parsed structurally and the membership decision is left
    entirely to the accumulation reference. Nothing here re-implements what
    counts as approved, and nothing here constrains the shape of a path below
    ``artifacts/`` — the record schema already accepts nesting there, so a
    grammar that refused it would refuse a legitimate approved artifact.
    """
    from . import retrieval

    if not isinstance(raw, str) or len(raw) > MAX_QUALIFIED_REF_CHARS:
        raise InvalidRequest("that reference is not a usable input")
    prefix = retrieval.APPROVED_ROOT_PREFIX
    if not raw.startswith(prefix):
        raise InvalidRequest("that reference is not a usable input")
    remainder = raw[len(prefix) :]
    named_subject, slash, rest = remainder.partition("/")
    if not slash:
        raise InvalidRequest("that reference names no file")
    require_identifier(named_subject, "subject")
    if named_subject != subject:
        raise InvalidRequest("that reference belongs to a different subject")
    relative = confine.normalise_relative(rest)
    parts = relative.parts
    if len(parts) < 3 or parts[1] != records.ARTIFACTS_DIRNAME:
        raise InvalidRequest("an approved input must name an artifact")
    return str(relative)


def request_fingerprint(
    effect: str,
    operation_id: str,
    subject: str,
    work_id: str | None,
    normalised_params: Mapping[str, Any],
) -> str:
    """A canonical, content-free digest of one request.

    An operation id is a promise about one request. The fingerprint is what
    makes that checkable: bodies never enter it, only their digests and
    counts, and an absent optional parameter and an explicitly null one
    normalise to the same request.
    """
    payload = {
        "work_contract_version": WORK_CONTRACT_VERSION,
        "effect": effect,
        "operation_id": operation_id,
        "subject": subject,
        "work_id": work_id,
        "params": dict(normalised_params),
    }
    return confine.sha256_bytes(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
    )


def _digest_of_text(value: str) -> dict[str, Any]:
    return {"sha256": confine.sha256_bytes(value.encode("utf-8")), "chars": len(value)}


def _normalise_params(effect: str, params: Mapping[str, Any]) -> dict[str, Any]:
    """Reduce a request's parameters to the behaviourally relevant shape."""
    normalised: dict[str, Any] = {}
    for key in ("work_id", "pending_id", "proposed_state", "confirmed_state",
                "source_ref", "artifact_ref", "supersedes_ref", "expected_sha256",
                "conversation_id", "query", "max_results", "max_excerpt_chars",
                "max_files_examined", "max_bytes_examined"):
        if params.get(key) is not None:
            normalised[key] = params[key]
    for key in ("label", "intent", "origin_note", "reason"):
        value = params.get(key)
        if value is not None:
            normalised[key] = _digest_of_text(str(value))
    content = params.get("content")
    if content is not None:
        raw = str(content).encode("utf-8")
        normalised["content"] = {"sha256": confine.sha256_bytes(raw), "bytes": len(raw)}
    file_ref = params.get("file_ref")
    if isinstance(file_ref, Mapping):
        normalised["file_ref"] = {
            "root_ref": file_ref.get("root_ref"),
            "relative_path": str(confine.normalise_relative(file_ref.get("relative_path"))),
        }
    based_on = params.get("based_on")
    if based_on:
        normalised["based_on"] = sorted(
            ({"ref": entry.get("ref"), "sha256": entry.get("sha256")} for entry in based_on),
            key=lambda entry: (str(entry["ref"]), str(entry["sha256"])),
        )
    root_refs = params.get("root_refs")
    if root_refs is not None:
        normalised["root_refs"] = sorted(set(root_refs))
    hint = params.get("filename_hint")
    if hint is not None:
        normalised["filename_hint"] = store.slugify(hint, _SOURCE_SLUG_FALLBACK)
    return normalised


@dataclass
class _Call:
    """One invocation's resolved context, carried between the steps."""

    effect: str
    operation_id: str
    params: Mapping[str, Any]
    grant: grants.Grant
    subject: str
    request_sha256: str = ""
    work_id: str | None = None
    paths: store.WorkPaths | None = None
    subject_paths: store.SubjectPaths | None = None
    record: records.WorkRecord | None = None
    document: dict[str, Any] = field(default_factory=dict)
    record_before: str | None = None


class WorkService:
    """The provider-neutral Work service.

    It is given validated deployment configuration and an in-process grant
    issuer, and it needs nothing else: no runtime, no transport, no model, no
    conversation machinery. A later gate replaces its callers.
    """

    def __init__(
        self,
        configuration: RootConfiguration | None = None,
        *,
        env: Mapping[str, str] | None = None,
        issuer: grants.GrantIssuer | None = None,
        recovery_min_age_seconds: int = store.RECOVERY_MIN_AGE_SECONDS,
        clock: Any = None,
    ) -> None:
        self.configuration = configuration or load_root_configuration(env)
        self.accumulation = Accumulation(self.configuration)
        self.issuer = issuer or grants.GrantIssuer(self.accumulation)
        self.store = store.WorkStore(
            self.configuration.require_work_root(),
            recovery_min_age_seconds=recovery_min_age_seconds,
        )
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    # -- entry point --------------------------------------------------

    def invoke(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Carry one version-1 request envelope to one version-1 response."""
        effect = "open_work"
        operation_id = None
        try:
            if not isinstance(request, Mapping):
                raise InvalidRequest("a request must be an object")
            effect = require_effect(request.get("effect"))
            operation_id = require_uuid4(request.get("operation_id"), "operation_id")
            if request.get("work_contract_version") != WORK_CONTRACT_VERSION:
                raise WorkError(
                    "contract_version_unsupported",
                    "this request uses a contract version that is not served",
                )
            params = request.get("params")
            if not isinstance(params, Mapping):
                raise InvalidRequest("params must be an object")
            result, receipt = self._dispatch(effect, operation_id, params, request.get("grant_ref"))
            return success_response(effect, operation_id, result, receipt)
        except WorkError as exc:
            return error_response(
                effect,
                operation_id or "00000000-0000-4000-8000-000000000000",
                exc,
            )

    # -- validation ---------------------------------------------------

    def _check_params(self, effect: str, params: Mapping[str, Any]) -> None:
        spec = EFFECT_SPECS[effect]
        supplied = {key for key, value in params.items() if value is not None}
        unknown = sorted(supplied - spec.known)
        if unknown:
            raise InvalidRequest(
                "this operation does not take: " + ", ".join(unknown)
            )
        missing = sorted(spec.required - supplied)
        if missing:
            raise InvalidRequest("this operation needs: " + ", ".join(missing))
        if spec.exclusive:
            present = [
                sorted(group)[0] for group in spec.exclusive if supplied & set(group)
            ]
            if len(present) != 1:
                raise InvalidRequest(
                    "this operation takes exactly one of "
                    + " or ".join(sorted(sorted(group)[0] for group in spec.exclusive))
                )

    def _dispatch(
        self,
        effect: str,
        operation_id: str,
        params: Mapping[str, Any],
        grant_ref: Any,
    ) -> tuple[dict[str, Any], Receipt]:
        self._check_params(effect, params)
        handler = self._handlers()[effect]
        return handler(operation_id, params, grant_ref)

    def _handlers(self) -> dict[str, Any]:
        """The handler table. Its keys are exactly the closed effect set."""
        return {
            "open_work": self._open_work,
            "attach_source": self._attach_source,
            "search_sources": self._search_sources,
            "read_source": self._read_source,
            "write_artifact": self._write_artifact,
            "request_disposition": self._request_disposition,
            "record_disposition": self._record_disposition,
            "use_robert_edit": self._use_robert_edit,
        }

    # -- grant plumbing -----------------------------------------------

    def _authorise(
        self,
        effect: str,
        grant_ref: Any,
        *,
        subject: str,
        resolved: Mapping[str, Any],
    ) -> grants.Grant:
        return self.issuer.verify_and_consume(
            grant_ref, effect=effect, subject=subject, resolved=resolved
        )

    def _require_binding(self, call: _Call) -> None:
        """One direct open. No reverse index, no scan over conversations.

        The canonical work record carries no conversation id, so asking
        "does this work belong to another conversation?" would mean reading
        every binding. The bounded question is the other way round: this
        turn's conversation names exactly one active work item, and it may
        reach into that one.
        """
        assert call.subject_paths is not None
        conversation_id = call.grant.conversation_id
        if conversation_id is None:
            raise grants.GrantError(
                "grant_resource_mismatch", "that authority belongs to different work"
            )
        try:
            binding, _ = store.read_binding(call.subject_paths, conversation_id)
        except WorkError as exc:
            raise grants.GrantError(
                "grant_resource_mismatch", "that authority belongs to different work"
            ) from exc
        if (
            binding is None
            or binding.work_id != call.work_id
            or binding.subject != call.subject
        ):
            raise grants.GrantError(
                "grant_resource_mismatch", "that authority belongs to different work"
            )

    # -- shared write plumbing ----------------------------------------

    def _load(self, call: _Call) -> None:
        """Step P3: one confined snapshot of the record, parsed from it."""
        assert call.paths is not None
        loaded = store.read_record(call.paths)
        if loaded is None:
            raise WorkError("not_found", "there is no such work item")
        record, document, snap = loaded
        if record.work_id != call.work_id or record.subject != call.subject:
            raise WorkError("not_found", "there is no such work item")
        call.record = record
        call.document = document
        call.record_before = snap.sha256

    def _refuse_after_disposition(self, call: _Call) -> None:
        """A decision is the end of the line for this work item."""
        if call.record is not None and call.record.disposition is not None:
            raise InvalidRequest("this work item has been decided and takes no more changes")

    def _duplicate(self, call: _Call) -> tuple[dict[str, Any], Receipt] | None:
        """The lock-free fast path: one open on a derived name, no scan."""
        assert call.paths is not None
        terminal = store.read_terminal(call.paths, call.operation_id)
        if terminal is None:
            return None
        return self._answer_terminal(call, terminal)

    def _answer_terminal(
        self, call: _Call, terminal: store.Terminal
    ) -> tuple[dict[str, Any], Receipt]:
        if terminal.request_sha256 != call.request_sha256:
            raise InvalidRequest("that operation id was used for a different request")
        if terminal.outcome == "committed" and terminal.receipt is not None:
            return {"retry": True}, terminal.receipt
        raise self._terminal_failure(terminal)

    @staticmethod
    def _terminal_failure(terminal: store.Terminal) -> WorkError:
        if terminal.outcome == "abandoned":
            return WorkError(
                "internal_error",
                "a previous attempt at this operation did not complete and was "
                "abandoned; nothing was written — retry with a new operation_id",
            )
        return records.StaleContext(terminal.relative_path or store.WORK_RECORD_FILENAME)

    @staticmethod
    def _recovery_failure(outcome: store.RecoveryOutcome) -> WorkError:
        if outcome.outcome == "abandoned":
            return WorkError(
                "internal_error",
                "a previous attempt at this operation did not complete and was "
                "abandoned; nothing was written — retry with a new operation_id",
            )
        return records.StaleContext(outcome.relative_path or store.WORK_RECORD_FILENAME)

    def _finish_crashed_attempt(self, call: _Call) -> tuple[dict[str, Any], Receipt] | None:
        """Step P2: never start a second attempt under the same operation id."""
        assert call.paths is not None and call.subject_paths is not None
        terminal = store.read_terminal(call.paths, call.operation_id)
        if terminal is not None:
            return self._answer_terminal(call, terminal)
        intent = store.read_pending(call.paths, call.operation_id)
        if intent is None:
            return None
        outcome = self.store.recover(call.paths, intent, subject_paths=call.subject_paths)
        if outcome.outcome == "committed" and outcome.receipt is not None:
            return {"retry": True}, outcome.receipt
        raise self._recovery_failure(outcome)

    def _snapshot_entry(self, call: _Call, relative_path: str, expected: str) -> store.Snapshot:
        """Read one recorded file once and hold it to its pinned digest."""
        assert call.paths is not None
        snap = store.try_snapshot(
            call.paths.directory, relative_path, extensions=confine.ALLOWED_EXTENSIONS
        )
        if snap is None:
            raise records.StaleContext(relative_path)
        if snap.sha256 != expected:
            raise records.StaleContext(relative_path)
        return snap

    def _verify_based_on(self, call: _Call, entries: Sequence[Mapping[str, str]]) -> None:
        """Every input is pinned, and every pin is checked before anything is written."""
        assert call.record is not None
        for entry in entries:
            ref = entry["ref"]
            digest = entry["sha256"]
            if ref.startswith("approved:"):
                relative = parse_approved_ref(ref, call.subject)
                outcome = self.accumulation.read_source(
                    call.subject, approved_root_ref(call.subject), relative
                )
                if outcome.sha256 != digest:
                    raise records.StaleContext(relative)
                continue
            local = self._resolve_local_ref(call.record, ref)
            if local is None:
                raise InvalidRequest("that reference is not part of this work item")
            self._snapshot_entry(call, local.path, digest)

    @staticmethod
    def _resolve_local_ref(record: records.WorkRecord, ref: str) -> Any:
        for entry in record.sources:
            if entry.ref == ref:
                return entry
        return record.artifact(ref)

    def _plan_document(
        self,
        call: _Call,
        *,
        mutate,
        moment: datetime,
    ) -> tuple[dict[str, Any], bytes, str]:
        """Build the next record in memory and pin its digest. No I/O."""
        document = json.loads(json.dumps(call.document))
        mutate(document)
        document["updated_at"] = store.now_stamp(moment)
        records.parse_work_record(document)
        payload = store.encode_json(document)
        return document, payload, confine.sha256_bytes(payload)

    def _binding_leg(
        self, call: _Call, conversation_id: str | None, moment: datetime
    ) -> tuple[str | None, str | None, str | None, bytes | None, Path | None, str | None]:
        """The binding half of a write, when one is asked for."""
        if conversation_id is None:
            return None, None, None, None, None, None
        assert call.subject_paths is not None
        relative = call.subject_paths.binding_relative_path(conversation_id)
        binding, snap = store.read_binding(call.subject_paths, conversation_id)
        if binding is not None and binding.work_id == call.work_id:
            return None, None, None, None, None, None
        payload = store.encode_json(
            {
                "schema_version": records.SCHEMA_VERSION,
                "conversation_id": conversation_id,
                "subject": call.subject,
                "work_id": call.work_id,
                "updated_at": store.now_stamp(moment),
            }
        )
        return (
            relative,
            snap.sha256 if snap is not None else None,
            confine.sha256_bytes(payload),
            payload,
            call.subject_paths.conversations,
            f"{conversation_id}.json",
        )

    # -- effect 1: open_work ------------------------------------------

    def _open_work(
        self, operation_id: str, params: Mapping[str, Any], grant_ref: Any
    ) -> tuple[dict[str, Any], Receipt]:
        subject = require_identifier(params.get("subject"), "subject")
        work_id = params.get("work_id")
        label = _text_param(params, "label", MAX_LABEL_CHARS, required=False)
        intent_text = _text_param(params, "intent", MAX_INTENT_CHARS, required=False) or ""
        conversation_id = params.get("conversation_id")
        if conversation_id is not None:
            require_identifier(conversation_id, "conversation_id")
            if len(conversation_id) > MAX_CONVERSATION_ID_CHARS:
                raise InvalidRequest("conversation_id is not a valid name")
        if work_id is not None:
            work_id = require_uuid4(work_id, "work_id")

        resolved: dict[str, Any] = {}
        if work_id is not None:
            resolved["work_id"] = work_id
        else:
            resolved["operation_id"] = operation_id
        grant = self._authorise(
            "open_work", grant_ref, subject=subject, resolved=resolved
        )

        if grant.allow_create:
            if work_id is not None:
                raise InvalidRequest("a new work item cannot also name an existing one")
            if label is None:
                raise InvalidRequest("a new work item needs a label")
            return self._create(
                operation_id, subject, label, intent_text, conversation_id, grant, params
            )
        if work_id is None:
            # There is no lookup by label: a work item is reached by its own
            # identifier or it is created. Guessing between similarly named
            # items is exactly the ambiguity this contract refuses to have.
            raise InvalidRequest("this operation needs the identifier of the work item")
        if label is not None:
            raise InvalidRequest("an existing work item's label is not changed here")
        return self._continue_work(operation_id, subject, work_id, conversation_id, grant, params)

    def _create(
        self,
        operation_id: str,
        subject: str,
        label: str,
        intent_text: str,
        conversation_id: str | None,
        grant: grants.Grant,
        params: Mapping[str, Any],
    ) -> tuple[dict[str, Any], Receipt]:
        """The create sequence, in the order the recovery table is stated against.

        The reservation is published first and it names the directory this
        create will make. That is what turns a retry into a *derived* lookup
        rather than a search: the retry opens one path computed from the
        operation id, and the fingerprint check has already established that
        it is the same request that chose that name.
        """
        subject_paths = self.store.ensure_subject(subject)
        fingerprint = request_fingerprint(
            "open_work", operation_id, subject, None, _normalise_params("open_work", params)
        )
        moment = _utc(self._clock())

        with store.Lock(subject_paths.create_lock):
            reservation = self._read_reservation(subject_paths, operation_id)
            if reservation is not None:
                if reservation.request_sha256 != fingerprint:
                    raise InvalidRequest("that operation id was used for a different request")
            else:
                store.unlink_quietly(
                    subject_paths.creates,
                    store.temp_name(f"{operation_id}.json", operation_id),
                )
                reservation = self._reserve(
                    subject_paths,
                    operation_id,
                    subject,
                    label,
                    intent_text,
                    conversation_id,
                    fingerprint,
                    moment,
                )
            return self._complete_create(
                subject_paths,
                reservation,
                operation_id,
                subject,
                label,
                intent_text,
                conversation_id,
            )

    def _create_payloads(
        self,
        subject_paths: store.SubjectPaths,
        subject: str,
        work_id: str,
        label: str,
        intent_text: str,
        conversation_id: str | None,
        reserved_at: str,
    ) -> tuple[bytes, bytes | None]:
        """The exact bytes a create installs, derived from its own request.

        A create's record is fully determined by the request that asked for
        it plus the reservation's own timestamp, so a retry can rebuild the
        identical bytes and the reservation's pinned digest proves it did.
        This is not the forbidden rebuild-from-an-intent: nothing private has
        accumulated in a work item that does not exist yet.
        """
        document = {
            "schema_version": records.SCHEMA_VERSION,
            "work_contract_version": WORK_CONTRACT_VERSION,
            "work_id": work_id,
            "subject": subject,
            "label": label,
            "intent": intent_text,
            "state": "continuing",
            "created_at": reserved_at,
            "updated_at": reserved_at,
            "sources": [],
            "artifacts": [],
            "pending_approval": None,
            "disposition": None,
        }
        records.parse_work_record(document)
        binding_payload = None
        if conversation_id is not None:
            binding_payload = store.encode_json(
                {
                    "schema_version": records.SCHEMA_VERSION,
                    "conversation_id": conversation_id,
                    "subject": subject,
                    "work_id": work_id,
                    "updated_at": reserved_at,
                }
            )
        return store.encode_json(document), binding_payload

    def _reserve(
        self,
        subject_paths: store.SubjectPaths,
        operation_id: str,
        subject: str,
        label: str,
        intent_text: str,
        conversation_id: str | None,
        fingerprint: str,
        moment: datetime,
    ) -> store.Reservation:
        """Publish the content-free reservation that owns this create's id."""
        work_id = store.new_work_id()
        work_dirname = f"{store.slugify(label, 'work')}--{work_id}"
        reserved_at = store.now_stamp(moment)
        record_payload, binding_payload = self._create_payloads(
            subject_paths, subject, work_id, label, intent_text, conversation_id, reserved_at
        )
        binding_relative = None
        binding_before = None
        if conversation_id is not None:
            binding_relative = subject_paths.binding_relative_path(conversation_id)
            _, snap = store.read_binding(subject_paths, conversation_id)
            binding_before = snap.sha256 if snap is not None else None
        receipt = make_receipt(
            operation_id,
            "open_work",
            "committed",
            subject=subject,
            work_id=work_id,
            state="continuing",
            created_at=reserved_at,
        )
        reservation = {
            "schema_version": 1,
            "operation_id": operation_id,
            "work_id": work_id,
            "subject": subject,
            "request_sha256": fingerprint,
            "work_dirname": work_dirname,
            "record_sha256_before": None,
            "record_candidate_sha256": confine.sha256_bytes(record_payload),
            "binding_relative_path": binding_relative,
            "binding_sha256_before": binding_before,
            "binding_candidate_sha256": (
                confine.sha256_bytes(binding_payload) if binding_payload else None
            ),
            "reserved_at": reserved_at,
            "receipt": receipt.as_dict(),
        }
        parsed = store.parse_reservation(reservation)
        store._checkpoint("create:reserve")
        store.publish(
            subject_paths.creates,
            f"{operation_id}.json",
            store.encode_json(reservation),
            operation_id=operation_id,
        )
        return parsed

    def _read_reservation(
        self, subject_paths: store.SubjectPaths, operation_id: str
    ) -> store.Reservation | None:
        """One derived open, read back as the exact reservation schema.

        A retry decides which directory it completes, and which receipt it
        answers with, from these bytes. Raw decoded JSON is therefore not
        good enough: the reservation is validated as the bound control
        record it is, and a malformed one fails closed here rather than
        becoming a path or a duplicate answer.
        """
        snap = store.try_snapshot(
            subject_paths.base, f"{store.CREATES_DIRNAME}/{operation_id}.json"
        )
        if snap is None:
            return None
        reservation = store.parse_reservation(
            store.decode_control(snap.raw, "a create reservation")
        )
        if reservation.operation_id != operation_id:
            raise store.ControlRecordInvalid(
                "a create reservation names a different operation"
            )
        return reservation

    def _complete_create(
        self,
        subject_paths: store.SubjectPaths,
        reservation: store.Reservation,
        operation_id: str,
        subject: str,
        label: str,
        intent_text: str,
        conversation_id: str | None,
    ) -> tuple[dict[str, Any], Receipt]:
        """Steps 3 to 10, run identically on a first attempt and on a retry."""
        work_id = reservation.work_id
        directory = subject_paths.work_base / reservation.work_dirname
        paths = store.WorkPaths(directory=directory)

        receipt = reservation.receipt
        intent = store.Intent(
            operation_id=operation_id,
            effect="open_work",
            work_id=work_id,
            subject=subject,
            request_sha256=reservation.request_sha256,
            record_sha256_before=reservation.record_sha256_before,
            record_candidate_sha256=reservation.record_candidate_sha256,
            created_at=reservation.reserved_at,
            binding_relative_path=reservation.binding_relative_path,
            binding_sha256_before=reservation.binding_sha256_before,
            binding_candidate_sha256=reservation.binding_candidate_sha256,
            receipt=receipt,
        )

        if paths.operations.is_dir():
            terminal = store.read_terminal(paths, operation_id)
            if terminal is not None:
                leftover = store.read_pending(paths, operation_id)
                if leftover is not None:
                    # An existing terminal marker is authoritative: finish
                    # the cleanup it never reached, publish nothing.
                    self.store.recover(paths, leftover, subject_paths=subject_paths)
                return self._answer_committed_create(
                    subject_paths, reservation, terminal, paths
                )

        self._make_create_tree(paths, operation_id, intent.record_candidate_sha256)

        if store.read_pending(paths, operation_id) is not None:
            outcome = self.store.recover(paths, intent, subject_paths=subject_paths)
            if outcome.outcome == "committed" and outcome.receipt is not None:
                return (
                    {"retry": True, **self._orientation(paths, subject_paths)},
                    outcome.receipt,
                )
            raise self._recovery_failure(outcome)

        record_payload, binding_payload = self._create_payloads(
            subject_paths,
            subject,
            work_id,
            label,
            intent_text,
            conversation_id,
            reservation.reserved_at,
        )
        if confine.sha256_bytes(record_payload) != intent.record_candidate_sha256:
            raise WorkError("internal_error", "this change could not be confirmed")

        plan = store.WritePlan(
            intent=intent,
            record_candidate=record_payload,
            receipt=receipt,
            binding_candidate=binding_payload,
            conversations_dir=subject_paths.conversations,
            binding_filename=(
                f"{conversation_id}.json" if conversation_id is not None else None
            ),
        )
        stored = self.store.commit(paths, plan, subject_paths=subject_paths)
        return self._orientation(paths, subject_paths), stored

    def _make_create_tree(
        self, paths: store.WorkPaths, operation_id: str, record_candidate_sha256: str
    ) -> None:
        """Create the work directory and its five children, resumably.

        A directory this reservation names is reservation-owned by
        derivation: its name embeds a work id this process generated under
        the subject create lock, so no other operation has ever had a reason
        to write into it. Finding it already there is a resumption point, not
        an error. Finding something in it that this create cannot have made
        is refused outright, and nothing is completed.
        """
        base = paths.directory
        expected_files = {
            store.WORK_RECORD_FILENAME,
            store.LOCK_FILENAME,
            paths.record_candidate(operation_id),
        }
        expected_dirs = {name.split("/")[0] for name in store.CREATE_SUBDIRECTORIES}

        for relative in ("",) + store.CREATE_SUBDIRECTORIES:
            target = base if relative == "" else base / relative
            store._checkpoint(f"create:mkdir:{relative or 'work'}")
            if store.make_dir(target):
                if relative == "":
                    store.touch_lock(paths.lock)
                continue
            try:
                handle = store.open_dir(target)
            except OSError as exc:
                raise self._occupied(paths, operation_id, relative or base.name) from exc
            os.close(handle)
            if relative == "":
                store.touch_lock(paths.lock)
                self._check_create_tree(
                    paths,
                    operation_id,
                    expected_files,
                    expected_dirs,
                    record_candidate_sha256,
                )

    def _check_create_tree(
        self,
        paths: store.WorkPaths,
        operation_id: str,
        expected_files: set[str],
        expected_dirs: set[str],
        record_candidate_sha256: str,
    ) -> None:
        """One bounded look at what is actually in a reservation-owned tree."""
        bound = store.MAX_RECOVERED_OPERATIONS + 1
        installed = store.try_snapshot(paths.directory, store.WORK_RECORD_FILENAME)
        if installed is not None and installed.sha256 != record_candidate_sha256:
            # A record at this name that this create did not write is someone
            # else's work item, not a resumption point.
            raise self._occupied(paths, operation_id, store.WORK_RECORD_FILENAME)
        with os.scandir(paths.directory) as entries:
            for entry in itertools.islice(entries, bound):
                if entry.is_dir(follow_symlinks=False):
                    if entry.name not in expected_dirs:
                        raise self._occupied(paths, operation_id, entry.name)
                    continue
                if entry.name not in expected_files:
                    raise self._occupied(paths, operation_id, entry.name)
        if paths.operations.is_dir():
            with os.scandir(paths.operations) as entries:
                for entry in itertools.islice(entries, bound):
                    if entry.is_dir(follow_symlinks=False):
                        continue
                    if entry.name != paths.terminal_name(operation_id):
                        raise self._occupied(paths, operation_id, entry.name)
        for directory in (paths.pending, paths.staging):
            if not directory.is_dir():
                continue
            with os.scandir(directory) as entries:
                for entry in itertools.islice(entries, bound):
                    if operation_id not in entry.name:
                        raise self._occupied(paths, operation_id, entry.name)

    def _occupied(self, paths: store.WorkPaths, operation_id: str, name: str) -> WorkError:
        """Refuse without completing anything, and say so where it can be said.

        A marker can only be published when there is already an
        ``operations/`` directory to publish it into. When the obstruction is
        the work directory itself there is nowhere to write one, and the
        create simply fails having written nothing — which is the correct
        record of the fact that it never got a directory of its own.
        """
        if paths.operations.is_dir():
            try:
                store.publish(
                    paths.operations,
                    paths.terminal_name(operation_id),
                    store.encode_json(
                        {
                            "schema_version": 1,
                            "operation_id": operation_id,
                            "outcome": "quarantined",
                            "request_sha256": "",
                            "reason_code": "create_path_occupied",
                        }
                    ),
                    operation_id=operation_id,
                )
            except store.AlreadyPublished:
                pass
        return records.StaleContext(name)

    def _answer_committed_create(
        self,
        subject_paths: store.SubjectPaths,
        reservation: store.Reservation,
        terminal: store.Terminal,
        paths: store.WorkPaths,
    ) -> tuple[dict[str, Any], Receipt]:
        """A committed duplicate answers, and writes nothing at all.

        Binding installation is ordered before the terminal marker, so no
        crash can leave a committed create whose pinned binding is missing.
        A binding that is missing or different here was changed by something
        outside this operation, and the honest answer is to say so and leave
        the evidence exactly as found. Re-establishing it is a fresh, explicit
        open under a new operation id.
        """
        if terminal.outcome != "committed" or terminal.receipt is None:
            raise self._terminal_failure(terminal)
        relative = reservation.binding_relative_path
        if relative:
            snap = store.try_snapshot(subject_paths.base, relative)
            if snap is None or snap.sha256 != reservation.binding_candidate_sha256:
                raise records.StaleContext(relative)
        return (
            {"retry": True, **self._orientation(paths, subject_paths)},
            terminal.receipt,
        )

    def _continue_work(
        self,
        operation_id: str,
        subject: str,
        work_id: str,
        conversation_id: str | None,
        grant: grants.Grant,
        params: Mapping[str, Any],
    ) -> tuple[dict[str, Any], Receipt]:
        """Open an existing work item, recover what crashed, and orient."""
        subject_paths = self.store.subject_paths(subject)
        directory = self.store.find_work_directory(subject, work_id)
        if directory is None:
            raise WorkError("not_found", "there is no such work item")
        paths = store.WorkPaths(directory=directory)
        call = _Call(
            effect="open_work",
            operation_id=operation_id,
            params=params,
            grant=grant,
            subject=subject,
            work_id=work_id,
            paths=paths,
            subject_paths=subject_paths,
        )
        call.request_sha256 = request_fingerprint(
            "open_work", operation_id, subject, work_id, _normalise_params("open_work", params)
        )

        answered = self._duplicate(call)
        if answered is not None:
            result, receipt = answered
            return {**result, **self._orientation(paths, subject_paths)}, receipt

        moment = _utc(self._clock())
        with store.Lock(paths.lock):
            answered = self._finish_crashed_attempt(call)
            if answered is not None:
                result, receipt = answered
                return {**result, **self._orientation(paths, subject_paths)}, receipt

            unreconciled, truncated = self._sweep(call)
            self._load(call)

            (
                binding_relative,
                binding_before,
                binding_digest,
                binding_payload,
                conversations_dir,
                binding_filename,
            ) = self._binding_leg(call, conversation_id, moment)

            if binding_payload is None:
                # Nothing to change: no intent, no marker, no record churn.
                receipt = make_receipt(
                    operation_id,
                    "open_work",
                    "ok",
                    subject=subject,
                    work_id=work_id,
                    state=call.record.state if call.record else "continuing",
                )
                return (
                    self._orientation(
                        paths,
                        subject_paths,
                        unreconciled=unreconciled,
                        truncated=truncated,
                    ),
                    receipt,
                )

            document, payload, digest = self._plan_document(
                call, mutate=lambda doc: None, moment=moment
            )
            receipt = make_receipt(
                operation_id,
                "open_work",
                "committed",
                subject=subject,
                work_id=work_id,
                state=document["state"],
                created_at=store.now_stamp(moment),
            )
            intent = store.Intent(
                operation_id=operation_id,
                effect="open_work",
                work_id=work_id,
                subject=subject,
                request_sha256=call.request_sha256,
                record_sha256_before=call.record_before,
                record_candidate_sha256=digest,
                created_at=store.now_stamp(moment),
                binding_relative_path=binding_relative,
                binding_sha256_before=binding_before,
                binding_candidate_sha256=binding_digest,
                receipt=receipt,
            )
            plan = store.WritePlan(
                intent=intent,
                record_candidate=payload,
                receipt=receipt,
                binding_candidate=binding_payload,
                conversations_dir=conversations_dir,
                binding_filename=binding_filename,
            )
            stored = self.store.commit(paths, plan, subject_paths=subject_paths)
            return (
                self._orientation(
                    paths, subject_paths, unreconciled=unreconciled, truncated=truncated
                ),
                stored,
            )

    def _sweep(self, call: _Call) -> tuple[list[dict[str, Any]], bool]:
        """The lazy recovery pass: bounded selection, bounded staging sweep."""
        assert call.paths is not None and call.subject_paths is not None
        self.store.sweep_staging(call.paths)
        pending, truncated = self.store.select_pending(call.paths)
        unreconciled: list[dict[str, Any]] = []
        for intent in pending:
            if intent.operation_id == call.operation_id:
                continue
            if not self.store.is_old_enough(intent):
                unreconciled.append(
                    {
                        "operation_id": intent.operation_id,
                        "effect": intent.effect,
                        "outcome": "pending",
                    }
                )
                continue
            outcome = self.store.recover(
                call.paths, intent, subject_paths=call.subject_paths
            )
            unreconciled.append(
                {
                    "operation_id": outcome.operation_id,
                    "effect": outcome.effect,
                    "outcome": outcome.outcome,
                }
            )
        return unreconciled, truncated

    def _orientation(
        self,
        paths: store.WorkPaths,
        subject_paths: store.SubjectPaths,
        *,
        unreconciled: Sequence[Mapping[str, Any]] = (),
        truncated: bool = False,
    ) -> dict[str, Any]:
        """What the caller is told about a work item. No bodies, no absolute paths."""
        loaded = store.read_record(paths)
        if loaded is None:
            raise WorkError("not_found", "there is no such work item")
        record, _document, _snap = loaded
        used = 0
        for entry in list(record.sources) + list(record.artifacts):
            used += entry.bytes or 0
        return {
            "work_id": record.work_id,
            "subject": record.subject,
            "label": record.label,
            "intent": record.intent,
            "state": record.state,
            "sources": [
                {
                    "ref": entry.ref,
                    "path": entry.path,
                    "sha256": entry.sha256,
                    "bytes": entry.bytes,
                    "context_class": entry.context_class,
                    "origin_note": entry.origin_note,
                    "created_at": entry.created_at,
                }
                for entry in record.sources
            ],
            "artifacts": [
                {
                    "ref": entry.ref,
                    "revision": entry.revision,
                    "path": entry.path,
                    "sha256": entry.sha256,
                    "bytes": entry.bytes,
                    "context_class": entry.context_class,
                    "based_on": [
                        {"ref": item.ref, "sha256": item.sha256} for item in entry.based_on
                    ],
                    "supersedes_ref": entry.supersedes_ref,
                    "created_at": entry.created_at,
                }
                for entry in record.artifacts
            ],
            "latest_artifact_ref": record.artifacts[-1].ref if record.artifacts else None,
            "pending_approval": (
                {
                    "pending_id": record.pending_approval.pending_id,
                    "proposed_state": record.pending_approval.proposed_state,
                    "artifact_ref": record.pending_approval.artifact_ref,
                    "artifact_sha256": record.pending_approval.artifact_sha256,
                    "issued_at": record.pending_approval.issued_at,
                    "expires_at": record.pending_approval.expires_at,
                }
                if record.pending_approval is not None
                else None
            ),
            "disposition": (
                {
                    "state": record.disposition.state,
                    "decided_at": record.disposition.decided_at,
                    "artifact_ref": record.disposition.artifact_ref,
                    "reason": record.disposition.reason,
                }
                if record.disposition is not None
                else None
            ),
            "allowed_source_roots": list(
                self.accumulation.available_root_refs(record.subject)
            ),
            "work_bytes_used": used,
            "work_bytes_limit": store.MAX_WORK_TOTAL_BYTES,
            "unreconciled_operations": [dict(entry) for entry in unreconciled],
            "recovery_view_truncated": truncated,
        }

    # -- effects 2 to 8: the shared opening ---------------------------

    def _begin(
        self,
        effect: str,
        operation_id: str,
        params: Mapping[str, Any],
        grant_ref: Any,
        *,
        resolved: Mapping[str, Any],
    ) -> _Call:
        work_id = require_uuid4(params.get("work_id"), "work_id")
        grant_ref_value = grant_ref
        pre = self.issuer.verify_and_consume(
            grant_ref_value,
            effect=effect,
            subject=self._grant_subject(grant_ref_value),
            resolved={"work_id": work_id, **dict(resolved)},
        )
        subject = pre.subject
        subject_paths = self.store.subject_paths(subject)
        directory = self.store.find_work_directory(subject, work_id)
        if directory is None:
            raise WorkError("not_found", "there is no such work item")
        call = _Call(
            effect=effect,
            operation_id=operation_id,
            params=params,
            grant=pre,
            subject=subject,
            work_id=work_id,
            paths=store.WorkPaths(directory=directory),
            subject_paths=subject_paths,
        )
        call.request_sha256 = request_fingerprint(
            effect, operation_id, subject, work_id, _normalise_params(effect, params)
        )
        self._require_binding(call)
        return call

    def _grant_subject(self, grant_ref: Any) -> str:
        """The subject a grant names.

        Effects two to eight do not take a subject parameter at all: the
        grant says which subject's work this turn may reach, so there is no
        request field that could disagree with it.
        """
        grant = self.issuer.peek(grant_ref)
        if grant is None:
            raise grants.GrantError("grant_invalid", "this turn holds no authority for that")
        return grant.subject

    # -- effect 2: attach_source --------------------------------------

    def _attach_source(
        self, operation_id: str, params: Mapping[str, Any], grant_ref: Any
    ) -> tuple[dict[str, Any], Receipt]:
        inline = params.get("content") is not None
        resolved: dict[str, Any] = {}
        raw: bytes | None = None
        if inline:
            raw = _content_param(params)
            resolved["content_sha256"] = confine.sha256_bytes(raw)
            resolved["content_bytes"] = len(raw)
        else:
            root_ref, relative = _file_ref_param(params)
            resolved["root_refs"] = frozenset({root_ref})
            resolved["relative_path"] = relative

        call = self._begin(
            "attach_source", operation_id, params, grant_ref, resolved=resolved
        )
        origin_note = _text_param(params, "origin_note", MAX_ORIGIN_NOTE_CHARS, required=False)
        slug = store.slugify(params.get("filename_hint"), _SOURCE_SLUG_FALLBACK)

        answered = self._duplicate(call)
        if answered is not None:
            return answered

        if inline:
            assert raw is not None
            context_class = call.grant.source_class
            if context_class not in SOURCE_CONTEXT_CLASSES:
                raise InvalidRequest("this authority does not say what these bytes are")
            extension = INLINE_SOURCE_EXTENSION
        else:
            root_ref, relative = _file_ref_param(params)
            if is_approved_root(root_ref):
                # Approved material is read and cited, never recaptured:
                # there is no source class it could honestly be stored as.
                raise InvalidRequest("approved material is cited, not captured")
            root = self.configuration.resolve(call.subject, root_ref)
            if root.context_class not in SOURCE_CONTEXT_CLASSES:
                raise InvalidRequest("that root does not hold material this can capture")
            outcome = self.accumulation.read_source(call.subject, root_ref, relative)
            raw = outcome.content.encode("utf-8")
            context_class = root.context_class
            extension = Path(relative).suffix.lower() or ".txt"

        moment = _utc(self._clock())
        with store.Lock(call.paths.lock):
            answered = self._finish_crashed_attempt(call)
            if answered is not None:
                return answered
            self._load(call)
            self._refuse_after_disposition(call)
            assert call.record is not None

            index = len(call.record.sources) + 1
            ref = f"src-{index:04d}"
            relative_path = f"{records.SOURCES_DIRNAME}/{index:04d}-{slug}{extension}"
            used = self.store.measure(call.paths, call.record)
            self.store.require_capacity(used, len(raw), relative_path)

            created_at = store.now_stamp(moment)
            entry = {
                "ref": ref,
                "path": relative_path,
                "sha256": confine.sha256_bytes(raw),
                "bytes": len(raw),
                "context_class": context_class,
                "created_at": created_at,
                "operation_id": operation_id,
            }
            if origin_note is not None:
                entry["origin_note"] = origin_note

            def mutate(document: dict[str, Any]) -> None:
                document["sources"] = list(document["sources"]) + [entry]

            _document, payload, digest = self._plan_document(call, mutate=mutate, moment=moment)
            receipt = make_receipt(
                operation_id,
                "attach_source",
                "committed",
                subject=call.subject,
                work_id=call.work_id,
                ref=ref,
                relative_path=relative_path,
                sha256=entry["sha256"],
                bytes=entry["bytes"],
                context_class=context_class,
                created_at=created_at,
            )
            intent = store.Intent(
                operation_id=operation_id,
                effect="attach_source",
                work_id=call.work_id,
                subject=call.subject,
                request_sha256=call.request_sha256,
                record_sha256_before=call.record_before,
                record_candidate_sha256=digest,
                created_at=created_at,
                target_relative_path=relative_path,
                output_sha256=entry["sha256"],
                output_bytes=entry["bytes"],
                ref=ref,
                context_class=context_class,
                receipt=receipt,
            )
            stored = self.store.commit(
                call.paths,
                store.WritePlan(
                    intent=intent, record_candidate=payload, receipt=receipt, output=raw
                ),
                subject_paths=call.subject_paths,
            )
        return (
            {
                "source_ref": ref,
                "relative_path": relative_path,
                "sha256": entry["sha256"],
                "bytes": entry["bytes"],
                "context_class": context_class,
                "created_at": created_at,
            },
            stored,
        )

    # -- effect 3: search_sources -------------------------------------

    def _search_sources(
        self, operation_id: str, params: Mapping[str, Any], grant_ref: Any
    ) -> tuple[dict[str, Any], Receipt]:
        # The selection is validated into a set of root names *before* it is
        # made into one: `frozenset(...)` over an unvalidated request field
        # is the request escaping the envelope, because a scalar or an
        # unhashable member raises out of the handler rather than answering
        # `invalid_request`.
        requested = grants.normalise_requested_roots(params.get("root_refs"))
        work_id = require_uuid4(params.get("work_id"), "work_id")
        grant = self.issuer.verify_and_consume(
            grant_ref,
            effect="search_sources",
            subject=self._grant_subject(grant_ref),
            resolved={
                "work_id": work_id,
                "root_refs": frozenset(requested)
                if requested is not None
                else self._bound_roots(grant_ref),
            },
        )
        call = self._context_for(grant, operation_id, params, work_id, "search_sources")
        self._require_binding(call)

        refs = grants.narrowed_root_refs(grant, requested)
        outcome = self.accumulation.search_sources(
            call.subject,
            list(refs) if refs is not None else None,
            params.get("query"),
            max_results=params.get("max_results", MAX_RESULTS_CEILING),
            max_excerpt_chars=params.get("max_excerpt_chars", MAX_EXCERPT_CHARS_CEILING),
            **{
                key: params[key]
                for key in ("max_files_examined", "max_bytes_examined")
                if params.get(key) is not None
            },
        )
        result = {
            "hits": [
                {
                    "subject": hit.subject,
                    "root_ref": hit.root_ref,
                    "relative_path": hit.relative_path,
                    "sha256": hit.sha256,
                    "line_start": hit.line_start,
                    "line_end": hit.line_end,
                    "excerpt": hit.excerpt,
                    "context_class": hit.context_class,
                    "disposition": (
                        {
                            "work_id": hit.disposition.work_id,
                            "operation_id": hit.disposition.operation_id,
                            "decided_at": hit.disposition.decided_at,
                        }
                        if hit.disposition is not None
                        else None
                    ),
                }
                for hit in outcome.hits
            ],
            "issues": [
                {
                    "code": issue.code,
                    "root_ref": issue.root_ref,
                    "relative_path": issue.relative_path,
                    "message": issue.message,
                }
                for issue in outcome.issues
            ],
        }
        receipt = make_receipt(
            operation_id,
            "search_sources",
            "ok",
            subject=call.subject,
            result_count=len(outcome.hits),
        )
        return result, receipt

    def _bound_roots(self, grant_ref: Any) -> frozenset[str] | None:
        grant = self.issuer.peek(grant_ref)
        return grant.root_refs if grant is not None else None

    def _context_for(
        self,
        grant: grants.Grant,
        operation_id: str,
        params: Mapping[str, Any],
        work_id: str,
        effect: str,
    ) -> _Call:
        subject = grant.subject
        directory = self.store.find_work_directory(subject, work_id)
        if directory is None:
            raise WorkError("not_found", "there is no such work item")
        call = _Call(
            effect=effect,
            operation_id=operation_id,
            params=params,
            grant=grant,
            subject=subject,
            work_id=work_id,
            paths=store.WorkPaths(directory=directory),
            subject_paths=self.store.subject_paths(subject),
        )
        call.request_sha256 = request_fingerprint(
            effect, operation_id, subject, work_id, _normalise_params(effect, params)
        )
        return call

    # -- effect 4: read_source ----------------------------------------

    def _read_source(
        self, operation_id: str, params: Mapping[str, Any], grant_ref: Any
    ) -> tuple[dict[str, Any], Receipt]:
        captured = params.get("source_ref") is not None
        resolved: dict[str, Any] = {}
        if captured:
            resolved["source_ref"] = params.get("source_ref")
        else:
            root_ref, relative = _file_ref_param(params)
            resolved["root_refs"] = frozenset({root_ref})
            resolved["relative_path"] = relative
        call = self._begin("read_source", operation_id, params, grant_ref, resolved=resolved)

        if not captured:
            root_ref, relative = _file_ref_param(params)
            outcome = self.accumulation.read_source(call.subject, root_ref, relative)
            result = {
                "content": outcome.content,
                "sha256": outcome.sha256,
                "bytes": outcome.bytes,
                "context_class": outcome.context_class,
                "relative_path": outcome.relative_path,
                "root_ref": outcome.root_ref,
                "disposition": (
                    {
                        "work_id": outcome.disposition.work_id,
                        "operation_id": outcome.disposition.operation_id,
                        "decided_at": outcome.disposition.decided_at,
                    }
                    if outcome.disposition is not None
                    else None
                ),
            }
            receipt = make_receipt(
                operation_id,
                "read_source",
                "ok",
                subject=call.subject,
                root_ref=outcome.root_ref,
                relative_path=outcome.relative_path,
                sha256=outcome.sha256,
                bytes=outcome.bytes,
            )
            return result, receipt

        self._load(call)
        assert call.record is not None
        source_ref = str(params.get("source_ref"))
        entry = next((item for item in call.record.sources if item.ref == source_ref), None)
        if entry is None:
            raise WorkError("not_found", "there is no such captured source")
        snap = self._snapshot_entry(call, entry.path, entry.sha256)
        try:
            content = snap.raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise confine.UnsupportedMedia(entry.path) from exc
        result = {
            "content": content,
            "sha256": snap.sha256,
            "bytes": snap.bytes,
            "context_class": entry.context_class,
            "relative_path": entry.path,
            "source_ref": entry.ref,
            "disposition": None,
        }
        receipt = make_receipt(
            operation_id,
            "read_source",
            "ok",
            subject=call.subject,
            work_id=call.work_id,
            ref=entry.ref,
            relative_path=entry.path,
            sha256=snap.sha256,
            bytes=snap.bytes,
            context_class=entry.context_class,
        )
        return result, receipt

    # -- effect 5: write_artifact -------------------------------------

    def _write_artifact(
        self, operation_id: str, params: Mapping[str, Any], grant_ref: Any
    ) -> tuple[dict[str, Any], Receipt]:
        raw = _content_param(params)
        based_on = _based_on_param(params)
        call = self._begin(
            "write_artifact",
            operation_id,
            params,
            grant_ref,
            resolved={
                "content_sha256": confine.sha256_bytes(raw),
                "content_bytes": len(raw),
            },
        )
        answered = self._duplicate(call)
        if answered is not None:
            return answered
        return self._produce_artifact(
            call,
            raw=raw,
            based_on=based_on,
            context_class="agent_draft",
            supersedes_ref=None,
        )

    # -- effect 8: use_robert_edit ------------------------------------

    def _use_robert_edit(
        self, operation_id: str, params: Mapping[str, Any], grant_ref: Any
    ) -> tuple[dict[str, Any], Receipt]:
        supersedes_ref = params.get("supersedes_ref")
        if not isinstance(supersedes_ref, str) or not supersedes_ref.strip():
            raise InvalidRequest("supersedes_ref must name an artifact in this work item")
        expected = params.get("expected_sha256")
        if records.SHA256_PATTERN.fullmatch(str(expected)) is None:
            raise InvalidRequest("expected_sha256 must be a lowercase hex sha256")
        inline = params.get("content") is not None
        resolved: dict[str, Any] = {
            "supersedes_ref": supersedes_ref,
        }
        raw: bytes | None = None
        if inline:
            raw = _content_param(params)
            resolved["content_sha256"] = confine.sha256_bytes(raw)
            resolved["content_bytes"] = len(raw)
        else:
            root_ref, relative = _file_ref_param(params)
            resolved["root_refs"] = frozenset({root_ref})
            resolved["relative_path"] = relative

        call = self._begin(
            "use_robert_edit", operation_id, params, grant_ref, resolved=resolved
        )
        if call.grant.bound("expected_sha256") != expected:
            raise grants.GrantError(
                "grant_resource_mismatch", "that authority belongs to different work"
            )
        answered = self._duplicate(call)
        if answered is not None:
            return answered

        if not inline:
            root_ref, relative = _file_ref_param(params)
            if is_approved_root(root_ref):
                raise InvalidRequest(
                    "an edit may only be adopted from a personally authored root"
                )
            root = self.configuration.resolve(call.subject, root_ref)
            if root.context_class != "robert_source":
                raise InvalidRequest(
                    "an edit may only be adopted from a personally authored root"
                )
            outcome = self.accumulation.read_source(call.subject, root_ref, relative)
            raw = outcome.content.encode("utf-8")
            bound_input = call.grant.bound("expected_input_sha256")
            if bound_input is not None and confine.sha256_bytes(raw) != bound_input:
                raise records.StaleContext(relative)

        assert raw is not None
        return self._produce_artifact(
            call,
            raw=raw,
            based_on=(),
            context_class="coauthored_output",
            supersedes_ref=supersedes_ref,
            expected_sha256=str(expected),
        )

    def _produce_artifact(
        self,
        call: _Call,
        *,
        raw: bytes,
        based_on: Sequence[Mapping[str, str]],
        context_class: str,
        supersedes_ref: str | None,
        expected_sha256: str | None = None,
    ) -> tuple[dict[str, Any], Receipt]:
        assert call.paths is not None and call.subject_paths is not None
        moment = _utc(self._clock())
        with store.Lock(call.paths.lock):
            answered = self._finish_crashed_attempt(call)
            if answered is not None:
                return answered
            self._load(call)
            self._refuse_after_disposition(call)
            assert call.record is not None

            if supersedes_ref is not None:
                superseded = call.record.artifact(supersedes_ref)
                if superseded is None:
                    raise InvalidRequest("that reference is not part of this work item")
                assert expected_sha256 is not None
                snap = self._snapshot_entry(call, superseded.path, expected_sha256)
                if snap.sha256 != superseded.sha256:
                    raise records.StaleContext(superseded.path)
                if confine.sha256_bytes(raw) == superseded.sha256:
                    # Identical bytes are not evidence of an authorship
                    # contribution, and recording them as one would quietly
                    # relabel an agent draft as co-authored.
                    raise InvalidRequest("these bytes are the same as the revision they replace")

            self._verify_based_on(call, based_on)

            revision = len(call.record.artifacts) + 1
            ref = f"art-{revision:04d}"
            relative_path = (
                f"{records.ARTIFACTS_DIRNAME}/{revision:04d}-"
                f"{_ARTIFACT_SLUG_FALLBACK}{ARTIFACT_EXTENSION}"
            )
            used = self.store.measure(call.paths, call.record)
            self.store.require_capacity(used, len(raw), relative_path)

            created_at = store.now_stamp(moment)
            entry: dict[str, Any] = {
                "ref": ref,
                "path": relative_path,
                "sha256": confine.sha256_bytes(raw),
                "bytes": len(raw),
                "context_class": context_class,
                "revision": revision,
                "based_on": [dict(item) for item in based_on],
                "created_at": created_at,
                "operation_id": call.operation_id,
            }
            if supersedes_ref is not None:
                entry["supersedes_ref"] = supersedes_ref

            def mutate(document: dict[str, Any]) -> None:
                document["artifacts"] = list(document["artifacts"]) + [entry]

            _document, payload, digest = self._plan_document(call, mutate=mutate, moment=moment)
            fields: dict[str, Any] = {
                "subject": call.subject,
                "work_id": call.work_id,
                "ref": ref,
                "revision": revision,
                "relative_path": relative_path,
                "sha256": entry["sha256"],
                "bytes": entry["bytes"],
                "context_class": context_class,
                "created_at": created_at,
            }
            if supersedes_ref is not None:
                fields["supersedes_ref"] = supersedes_ref
            receipt = make_receipt(call.operation_id, call.effect, "committed", **fields)
            intent = store.Intent(
                operation_id=call.operation_id,
                effect=call.effect,
                work_id=call.work_id,
                subject=call.subject,
                request_sha256=call.request_sha256,
                record_sha256_before=call.record_before,
                record_candidate_sha256=digest,
                created_at=created_at,
                target_relative_path=relative_path,
                output_sha256=entry["sha256"],
                output_bytes=entry["bytes"],
                ref=ref,
                revision=revision,
                context_class=context_class,
                supersedes_ref=supersedes_ref,
                expected_inputs=tuple(dict(item) for item in based_on),
                receipt=receipt,
            )
            stored = self.store.commit(
                call.paths,
                store.WritePlan(
                    intent=intent, record_candidate=payload, receipt=receipt, output=raw
                ),
                subject_paths=call.subject_paths,
            )
        result = {
            "artifact_ref": ref,
            "revision": revision,
            "relative_path": relative_path,
            "sha256": entry["sha256"],
            "bytes": entry["bytes"],
            "context_class": context_class,
            "created_at": created_at,
        }
        if supersedes_ref is not None:
            result["supersedes_ref"] = supersedes_ref
        return result, stored

    # -- effect 6: request_disposition --------------------------------

    def _request_disposition(
        self, operation_id: str, params: Mapping[str, Any], grant_ref: Any
    ) -> tuple[dict[str, Any], Receipt]:
        proposed_state = params.get("proposed_state")
        if proposed_state not in PROPOSED_STATES:
            raise InvalidRequest("that is not a decision this operation can propose")
        artifact_ref = params.get("artifact_ref")
        if proposed_state == "approved_text":
            if not isinstance(artifact_ref, str) or not artifact_ref.strip():
                raise InvalidRequest("approving text needs the exact artifact it approves")
        elif artifact_ref is not None:
            raise InvalidRequest("a decision about the work item does not name an artifact")

        resolved: dict[str, Any] = {}
        if artifact_ref is not None:
            resolved["artifact_ref"] = artifact_ref
        call = self._begin(
            "request_disposition", operation_id, params, grant_ref, resolved=resolved
        )
        answered = self._duplicate(call)
        if answered is not None:
            return answered

        moment = _utc(self._clock())
        with store.Lock(call.paths.lock):
            answered = self._finish_crashed_attempt(call)
            if answered is not None:
                return answered
            self._load(call)
            self._refuse_after_disposition(call)
            assert call.record is not None

            pending_id = str(uuid.uuid4())
            issued_at = store.now_stamp(moment)
            expires_at = store.now_stamp(moment + timedelta(seconds=PENDING_TTL_SECONDS))
            pending: dict[str, Any] = {
                "pending_id": pending_id,
                "proposed_state": proposed_state,
                "issued_at": issued_at,
                "expires_at": expires_at,
            }
            artifact_sha256 = None
            if proposed_state == "approved_text":
                artifact = call.record.artifact(str(artifact_ref))
                if artifact is None:
                    raise InvalidRequest("that reference is not part of this work item")
                snap = self._snapshot_entry(call, artifact.path, artifact.sha256)
                artifact_sha256 = snap.sha256
                pending["artifact_ref"] = artifact.ref
                pending["artifact_sha256"] = artifact_sha256

            def mutate(document: dict[str, Any]) -> None:
                document["pending_approval"] = pending

            document, payload, digest = self._plan_document(call, mutate=mutate, moment=moment)
            fields: dict[str, Any] = {
                "subject": call.subject,
                "work_id": call.work_id,
                "pending_id": pending_id,
                "proposed_state": proposed_state,
                "state": document["state"],
                "expires_at": expires_at,
                "created_at": issued_at,
            }
            if proposed_state == "approved_text":
                fields["ref"] = str(artifact_ref)
                fields["sha256"] = str(artifact_sha256)
            receipt = make_receipt(operation_id, "request_disposition", "committed", **fields)
            intent = store.Intent(
                operation_id=operation_id,
                effect="request_disposition",
                work_id=call.work_id,
                subject=call.subject,
                request_sha256=call.request_sha256,
                record_sha256_before=call.record_before,
                record_candidate_sha256=digest,
                created_at=issued_at,
                receipt=receipt,
            )
            stored = self.store.commit(
                call.paths,
                store.WritePlan(intent=intent, record_candidate=payload, receipt=receipt),
                subject_paths=call.subject_paths,
            )
        result: dict[str, Any] = {
            "pending_id": pending_id,
            "proposed_state": proposed_state,
            "issued_at": issued_at,
            "expires_at": expires_at,
            "confirmation_sentence": approval.confirmation_sentence(
                proposed_state, pending_id
            ),
        }
        if proposed_state == "approved_text":
            result["artifact_ref"] = str(artifact_ref)
            result["artifact_sha256"] = str(artifact_sha256)
        return result, stored

    # -- effect 7: record_disposition ---------------------------------

    def _record_disposition(
        self, operation_id: str, params: Mapping[str, Any], grant_ref: Any
    ) -> tuple[dict[str, Any], Receipt]:
        pending_id = require_uuid4(params.get("pending_id"), "pending_id")
        confirmed_state = params.get("confirmed_state")
        if confirmed_state not in PROPOSED_STATES:
            raise InvalidRequest("that is not a decision this operation can record")
        reason = _text_param(params, "reason", MAX_REASON_CHARS, required=False)
        call = self._begin(
            "record_disposition",
            operation_id,
            params,
            grant_ref,
            resolved={"pending_id": pending_id},
        )
        answered = self._duplicate(call)
        if answered is not None:
            return answered

        moment = _utc(self._clock())
        with store.Lock(call.paths.lock):
            answered = self._finish_crashed_attempt(call)
            if answered is not None:
                return answered
            self._load(call)
            self._refuse_after_disposition(call)
            assert call.record is not None

            pending = call.record.pending_approval
            if pending is None or pending.pending_id != pending_id:
                # Absent, superseded and expired all answer the same way: a
                # narrower answer would say which of the three it was.
                raise WorkError("pending_expired", "that decision is no longer answerable")
            expires = store.parse_stamp(pending.expires_at)
            if expires is not None and moment > expires:
                raise WorkError("pending_expired", "that decision is no longer answerable")
            if pending.proposed_state != confirmed_state:
                raise InvalidRequest("that is not the decision that was proposed")

            artifact_ref = None
            artifact_sha256 = None
            if confirmed_state == "approved_text":
                artifact = call.record.artifact(str(pending.artifact_ref))
                if artifact is None:
                    raise InvalidRequest("that reference is not part of this work item")
                snap = store.try_snapshot(
                    call.paths.directory,
                    artifact.path,
                    extensions=confine.ALLOWED_EXTENSIONS,
                )
                if snap is None or snap.sha256 != pending.artifact_sha256:
                    raise WorkError(
                        "pending_target_changed",
                        "the text this decision was about has changed since it was proposed",
                        relative_path=artifact.path,
                    )
                artifact_ref = artifact.ref
                artifact_sha256 = snap.sha256

            decided_at = store.now_stamp(moment)
            disposition = {
                "state": confirmed_state,
                "decided_at": decided_at,
                "artifact_ref": artifact_ref,
                "reason": reason,
                "operation_id": operation_id,
            }

            def mutate(document: dict[str, Any]) -> None:
                document["pending_approval"] = None
                document["disposition"] = disposition
                document["state"] = confirmed_state

            document, payload, digest = self._plan_document(call, mutate=mutate, moment=moment)
            fields: dict[str, Any] = {
                "subject": call.subject,
                "work_id": call.work_id,
                "pending_id": pending_id,
                "state": confirmed_state,
                "created_at": decided_at,
            }
            if confirmed_state == "approved_text":
                fields["ref"] = str(artifact_ref)
                fields["sha256"] = str(artifact_sha256)
            receipt = make_receipt(operation_id, "record_disposition", "committed", **fields)
            intent = store.Intent(
                operation_id=operation_id,
                effect="record_disposition",
                work_id=call.work_id,
                subject=call.subject,
                request_sha256=call.request_sha256,
                record_sha256_before=call.record_before,
                record_candidate_sha256=digest,
                created_at=decided_at,
                receipt=receipt,
            )
            stored = self.store.commit(
                call.paths,
                store.WritePlan(intent=intent, record_candidate=payload, receipt=receipt),
                subject_paths=call.subject_paths,
            )
        result: dict[str, Any] = {
            "state": confirmed_state,
            "disposition": {
                "state": confirmed_state,
                "decided_at": decided_at,
                "artifact_ref": artifact_ref,
                "reason": reason,
            },
        }
        if confirmed_state == "approved_text":
            result["artifact_ref"] = artifact_ref
            result["artifact_sha256"] = artifact_sha256
        return result, stored


__all__ = [
    "EFFECT_SPECS",
    "MAX_BASED_ON_ENTRIES",
    "MAX_QUALIFIED_REF_CHARS",
    "PENDING_TTL_SECONDS",
    "EffectSpec",
    "WorkService",
    "parse_approved_ref",
    "request_fingerprint",
]
