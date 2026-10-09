"""Fixed outcomes of a journal operation. Each carries a status code and the CLI exit code from v0.6 section 13.

Messages are fixed codes and counts: an error never quotes a journal line, an envelope field or a path outside the root.
"""
from __future__ import annotations


class JournalError(Exception):
    status = "error"
    exit_code = 6
    retryable = False
    committed: bool | None = False

    def __init__(self, reason: str = "", *, event_id: str | None = None, evidence: dict | None = None):
        super().__init__(reason or self.status)
        self.reason, self.event_id, self.evidence = reason or self.status, event_id, evidence or {}


class InvalidInput(JournalError):
    status, exit_code = "invalid_input", 2


class UnsafeRoot(JournalError):
    status, exit_code = "unsafe_root", 2


class LegacyAfterV2(InvalidInput):
    status = "legacy_after_v2"


class IdConflict(JournalError):
    status, exit_code = "id_conflict", 3


class LockBusy(JournalError):
    status, exit_code, retryable = "lock_busy", 4, True


class Corrupt(JournalError):
    status, exit_code = "corrupt", 5


class RecoveryBlocked(Corrupt):
    status = "recovery_blocked"


class WriteFailed(JournalError):
    """The append definitely did not commit (nothing, or only a cut-back partial line, reached the file)."""
    status, exit_code, retryable, committed = "write_failed", 6, True, False


class CommitUnknown(JournalError):
    """The bytes may or may not be durable. Ask for the event by its ID before retrying."""
    status, exit_code, retryable, committed = "commit_unknown", 6, True, None


class Missing(JournalError):
    status, exit_code = "missing", 8


class SourceRefused(JournalError):
    """A document that cannot be kept as it is: changed while read, too large, not text, a link, or an excluded class.

    The reason is a fixed code; nothing from the document is ever quoted."""
    status, exit_code = "source_refused", 2


class ArtifactMissing(Missing):
    status = "artifact_missing"


class ArtifactCorrupt(Corrupt):
    status = "artifact_corrupt"


class PolicyRefused(InvalidInput):
    """The event is well formed but the record forbids it now (an unknown request, someone else's claim, a wrong recipient)."""
    status = "policy_refused"


class ClaimConflict(JournalError):
    """A claim that is not the next generation, or a resource that already has an effective claimant."""
    status, exit_code = "claim_conflict", 3
