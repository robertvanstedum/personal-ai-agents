"""Synthetic authentication boundary; NOT a deployed authentication service."""
from dataclasses import dataclass
import hashlib
import hmac
import secrets
from .contracts import CLASSES, ContractError, VERSION, canonical, validate, now


@dataclass(frozen=True)
class Principal:
    name: str
    clearance: str
    topics: frozenset[str]
    owner: bool = False


@dataclass(frozen=True)
class Verified:
    principal: Principal
    proof: str


@dataclass(frozen=True)
class Receipt:
    payload: dict
    proof: str


class FixtureAuthority:
    """In-memory fixture credentials only. Body fields never select a principal.

    A P4 adapter must replace this with verified platform authentication and
    durable owner receipts. The synthetic secret is never persisted or logged.
    """
    def __init__(self, token_map):
        self._tokens = dict(token_map)
        self._key = secrets.token_bytes(32)
        for p in self._tokens.values():
            if p.clearance not in CLASSES:
                raise ContractError('Invalid configured clearance')

    def _proof(self, payload):
        return hmac.new(self._key, canonical(payload), hashlib.sha256).hexdigest()

    def authenticate(self, token):
        p = self._tokens.get(token)
        if p is None:
            raise ContractError('Unauthenticated fixture request')
        return Verified(p, self._proof(self._identity(p)))

    @staticmethod
    def _identity(p):
        return dict(name=p.name, clearance=p.clearance, topics=sorted(p.topics), owner=p.owner)

    def principal(self, verified):
        if not isinstance(verified, Verified) or not hmac.compare_digest(verified.proof, self._proof(self._identity(verified.principal))):
            raise ContractError('Unverified fixture principal')
        return verified.principal

    def issue(self, verified, operation, target_hash):
        p = self.principal(verified)
        if not p.owner:
            raise ContractError('Owner authority required')
        payload = dict(schema_version=VERSION, principal=p.name, operation=operation,
                       target_hash=target_hash, created_at=now(), receipt_id=secrets.token_hex(16))
        validate('receipt', payload)
        return Receipt(payload, self._proof(payload))

    def receipt(self, receipt, operation, target_hash):
        if not isinstance(receipt, Receipt):
            raise ContractError('Verified owner receipt required')
        validate('receipt', receipt.payload)
        if not hmac.compare_digest(receipt.proof, self._proof(receipt.payload)) or receipt.payload['operation'] != operation or receipt.payload['target_hash'] != target_hash:
            raise ContractError('Receipt does not authorize this exact revision')
        return receipt.payload


def can_read(principal, record, route):
    if route not in ('local', 'team-cloud'):
        raise ContractError('Unconfigured route')
    cls = record['disclosure_class']
    return (bool(set(record['topics']) & principal.topics) and cls != 'owner-only'
            and CLASSES.index(cls) <= CLASSES.index(principal.clearance)
            and (route == 'local' or cls == 'team'))
