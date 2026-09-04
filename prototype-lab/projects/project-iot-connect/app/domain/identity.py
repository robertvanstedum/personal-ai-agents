from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import NAMESPACE_URL, uuid4, uuid5


class SequenceSource(Protocol):
    def next_number(self, sequence_name: str) -> int: ...


def new_uuid() -> str:
    return str(uuid4())


def seeded_uuid(object_type: str, display_number: str) -> str:
    """Stable UUIDs for resettable prepared records only."""
    # Compatibility constant: the namespace string is hashed, never shown, and is
    # kept from the original prototype so prepared-record UUIDs stay stable
    # across the product rename (saved Postman IDs and prior evidence still resolve).
    return str(uuid5(NAMESPACE_URL, f"prototype-lab/wham-v3/{object_type}/{display_number}"))


@dataclass(frozen=True)
class IdentityFactory:
    sequences: SequenceSource

    def account(self) -> tuple[str, str]:
        return new_uuid(), f"ACCT-{self.sequences.next_number('account'):06d}"

    def customer(self) -> tuple[str, str]:
        return new_uuid(), f"CUS-{self.sequences.next_number('customer'):06d}"

    def contract(self) -> tuple[str, str]:
        return new_uuid(), f"CTR-{self.sequences.next_number('contract'):06d}"

    def subscription(self) -> tuple[str, str]:
        return new_uuid(), f"SUB-{self.sequences.next_number('subscription'):07d}"

    def batch(self) -> tuple[str, str]:
        return new_uuid(), f"BAT-{self.sequences.next_number('batch'):07d}"

    def bill_run(self) -> tuple[str, str]:
        return new_uuid(), f"RUN-{self.sequences.next_number('bill_run'):07d}"
