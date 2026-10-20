"""Admission-owned metadata for the MC operating harness (§3.2).

Construct only after authenticating the owner and admitting the turn. This
immutable value is NOT an authentication mechanism, execution grant or network
credential. Never deserialize it from model/tool arguments. The trusted adapter
must bind a runtime run before tools; execution must recheck current authority.
"""

from dataclasses import dataclass, field
from datetime import datetime
import unicodedata


class ActionContractError(ValueError):
    """A fixed safe code, without request contents or caller-supplied values."""


def _identifier(value: object) -> bool:
    return (
        type(value) is str
        and 0 < len(value) <= 256
        and value == value.strip()
        and not any(unicodedata.category(c) in {'Cc', 'Cf', 'Cs'} for c in value)
    )


def _aware(value: object) -> bool:
    return isinstance(value, datetime) and value.utcoffset() is not None


@dataclass(frozen=True, slots=True, repr=False)
class ActionContext:
    """Server-owned context, kept out of generic repr/log output.

    Versions are opaque identifiers from admission. An environment mismatch,
    revoked grant or old policy/role must still be refused by the executor.
    ``grant_ids`` is an immutable tuple, not a mutable list hidden in a frozen
    dataclass. A context without grants can support scoped observation only;
    possession of this object never itself authorizes a domain operation.
    """

    principal_id: str
    actor_id: str
    environment: str
    conversation_id: str
    turn_id: str
    dispatch_id: str
    generation: int
    expires_at: datetime
    policy_version: str
    role_version: str
    privacy_mode: str
    budget_account: str
    grant_ids: tuple[str, ...] = field(default_factory=tuple)
    work_id: str | None = None

    def __post_init__(self) -> None:
        identifiers = (
            self.principal_id, self.actor_id, self.conversation_id, self.turn_id,
            self.dispatch_id, self.policy_version, self.role_version,
            self.privacy_mode, self.budget_account,
        )
        if not all(_identifier(value) for value in identifiers):
            raise ActionContractError('context_identifier_invalid')
        if type(self.environment) is not str or self.environment not in {'dev', 'prod'}:
            raise ActionContractError('context_environment_invalid')
        if type(self.generation) is not int or self.generation < 1:
            raise ActionContractError('context_generation_invalid')
        if not _aware(self.expires_at):
            raise ActionContractError('context_expiry_invalid')
        if self.work_id is not None and not _identifier(self.work_id):
            raise ActionContractError('context_work_invalid')
        if (
            type(self.grant_ids) is not tuple
            or len(self.grant_ids) > 100
            or not all(_identifier(grant) for grant in self.grant_ids)
            or len(set(self.grant_ids)) != len(self.grant_ids)
        ):
            raise ActionContractError('context_grants_invalid')

    def has_expired(self, now: datetime) -> bool:
        """Exact expiry is expired. Inject the server clock, never tool time."""
        if not _aware(now):
            raise ActionContractError('context_clock_invalid')
        return now >= self.expires_at
