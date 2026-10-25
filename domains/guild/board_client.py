"""Fixed-operation Board client. Never retries an uncertain write automatically."""
from jsonschema import Draft202012Validator
from core.domain_api.client import DomainClient, Verified, NotApplied, Unknown
from core.domain_api.contract import DomainError, packed, require, operation_id
from .board_operations import operations
from .board_wire import payload, digest


class BoardClient:
    def __init__(self, endpoint, profile, *, board, **transport):
        require(endpoint.domain == profile.domain == "guild", "board_scope_invalid")
        self.transport = DomainClient(profile, endpoint, **transport)
        self.profile, self.board = profile, board
        self.ops = {op.id.removeprefix("guild.board."): op for op in operations()}

    def call(self, operation, body=None, *, target=None, operation_uuid=""):
        require(operation in self.ops, "operation_unknown")
        op = self.ops[operation]
        require(op.capability in self.profile.capabilities, "scope_denied", 403)
        body = {} if body is None else body
        require(not list(Draft202012Validator(op.arguments).iter_errors(body)), "arguments_invalid", 422)
        path = op.path
        if "{note_id}" in path:
            require(type(target) is str and target.isascii() and target.isdecimal() and 0 < int(target) < 10**18
                    and str(int(target)) == target, "note_reference_invalid")
            path = path.replace("{note_id}", target)
        if "{operation_id}" in path:
            path = path.replace("{operation_id}", operation_id(target))
        mutation = op.effect != "read"
        expected = digest(op.capability, payload(op.capability, body, target)) if mutation else None
        if mutation:
            operation_id(operation_uuid)
        else:
            require(operation_uuid == "", "read_operation_refused")
        raw = b"" if op.method == "GET" else packed(body)
        headers = self.transport._prepare(op.method, path, raw, operation_uuid)
        try:
            reply = self.transport._send(op.method, path, raw, headers)
            if reply.status >= 400:
                return NotApplied(operation_uuid, reply.body["error"]) if mutation else reply
            result = reply.body
            require(not list(Draft202012Validator(op.result).iter_errors(result)), "result_invalid")
            if not mutation:
                return reply
            if result["state"] == "unknown":
                return Unknown(operation_uuid)
            receipt = result["receipt"]
            if receipt is not None:
                require((receipt["operation_id"], receipt["capability"], receipt["principal"], receipt["executor"],
                         receipt["environment"], receipt["board"], receipt["payload_sha256"]) ==
                        (operation_uuid, op.capability, self.profile.principal, self.profile.actor,
                         self.profile.environment, self.board, expected), "receipt_mismatch")
            if result["state"] == "not_applied":
                return NotApplied(operation_uuid, result["code"])
            require(receipt is not None, "receipt_missing")
            return Verified(receipt, result)
        except Exception:
            if mutation:
                return Unknown(operation_uuid)
            raise DomainError("board_read_unconfirmed", 503, state="unknown") from None
