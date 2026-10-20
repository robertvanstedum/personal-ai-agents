"""Bounded model-request parsing, separate from trusted admission metadata.

The envelope is exactly operation, operation_version and arguments. Neither an
operation ID nor context/approval/grant fields can be supplied at the envelope
level. IDs for intended effects are assigned by the application, not the model.

Parsing is NOT authorization. After parsing, a registered operation must check
its exact version, domain argument schema, target and current finite grant.
Nothing in an argument object (including quoted instructions) creates authority.
"""

from dataclasses import dataclass, field
import hashlib
import json
import math
import re

from .context import ActionContractError

MAX_REQUEST_BYTES = 64 * 1024
MAX_JSON_DEPTH = 32
_OPERATION = re.compile(r'[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+')
_FIELDS = {'operation', 'operation_version', 'arguments'}


def _object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ActionContractError('request_duplicate_key')
        result[key] = value
    return result


def _constant(_value: str) -> None:
    raise ActionContractError('request_number_invalid')


def _bounded_tree(value: object, depth: int = 0) -> None:
    if depth > MAX_JSON_DEPTH:
        raise ActionContractError('request_depth_exceeded')
    if isinstance(value, float) and not math.isfinite(value):
        # parse_constant alone does not catch exponent overflow such as 1e999.
        raise ActionContractError('request_number_invalid')
    if isinstance(value, dict):
        for child in value.values():
            _bounded_tree(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _bounded_tree(child, depth + 1)


@dataclass(frozen=True, slots=True)
class ToolRequest:
    operation: str
    operation_version: int
    _arguments_json: bytes = field(repr=False)

    def __post_init__(self) -> None:
        # Direct construction must enforce the same invariants as transport
        # parsing. Canonicalize here so digest identity never depends on which
        # trusted caller constructed the value.
        if (type(self.operation) is not str or len(self.operation) > 128
                or not _OPERATION.fullmatch(self.operation)):
            raise ActionContractError('request_operation_invalid')
        if type(self.operation_version) is not int or self.operation_version < 1:
            raise ActionContractError('request_version_invalid')
        encoded = _canonical_arguments(self._arguments_json)
        object.__setattr__(self, '_arguments_json', encoded)

    @property
    def arguments(self) -> dict:
        """A fresh copy: callers cannot mutate a validated request in place."""
        return json.loads(self._arguments_json)

    @property
    def arguments_sha256(self) -> str:
        """Argument digest only, not an operation/approval/idempotency key."""
        return hashlib.sha256(self._arguments_json).hexdigest()


def _canonical_arguments(raw: bytes) -> bytes:
    if type(raw) is not bytes:
        raise ActionContractError('request_bytes_required')
    if len(raw) > MAX_REQUEST_BYTES:
        raise ActionContractError('request_too_large')
    try:
        args = json.loads(raw.decode('utf-8'), object_pairs_hook=_object,
                          parse_constant=_constant)
        if type(args) is not dict:
            raise ActionContractError('request_arguments_invalid')
        # Arguments occur one level below the transport envelope.
        _bounded_tree(args, 1)
        encoded = json.dumps(args, sort_keys=True, ensure_ascii=False,
                             separators=(',', ':'), allow_nan=False).encode('utf-8')
        if len(encoded) > MAX_REQUEST_BYTES:
            raise ActionContractError('request_too_large')
        return encoded
    except ActionContractError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise ActionContractError('request_json_invalid') from None


def parse_tool_request(raw: bytes) -> ToolRequest:
    """Parse a small UTF-8 request; failures expose fixed codes only.

    Transports must bound the body while reading it, before materializing
    ``raw``. This second check prevents bypass by a different trusted adapter.
    Domain services may impose smaller limits. Responses are bounded separately.
    """
    if type(raw) is not bytes:
        raise ActionContractError('request_bytes_required')
    if len(raw) > MAX_REQUEST_BYTES:
        raise ActionContractError('request_too_large')
    try:
        payload = json.loads(
            raw.decode('utf-8'), object_pairs_hook=_object,
            parse_constant=_constant,
        )
        _bounded_tree(payload)
        if type(payload) is not dict or set(payload) != _FIELDS:
            raise ActionContractError('request_fields_invalid')
        operation = payload['operation']
        if (
            type(operation) is not str or len(operation) > 128
            or not _OPERATION.fullmatch(operation)
        ):
            raise ActionContractError('request_operation_invalid')
        version = payload['operation_version']
        if type(version) is not int or version < 1:
            raise ActionContractError('request_version_invalid')
        if type(payload['arguments']) is not dict:
            raise ActionContractError('request_arguments_invalid')
        encoded = json.dumps(
            payload['arguments'], sort_keys=True, ensure_ascii=False,
            separators=(',', ':'), allow_nan=False,
        ).encode('utf-8')
        if len(encoded) > MAX_REQUEST_BYTES:
            raise ActionContractError('request_too_large')
    except ActionContractError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise ActionContractError('request_json_invalid') from None
    return ToolRequest(operation, version, encoded)
