"""Validate content-free runtime receipts; absence is never proof of inactivity."""
import re
import base64
import json

_RUN = re.compile(r"chatcmpl_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_INSTANCE = re.compile(r"[0-9a-f]{32}")
_AGENT = re.compile(r"[a-z][a-z0-9-]{0,63}")


def valid_receipt(value):
    return (type(value) is dict and set(value) == {"instance", "run", "session", "agent", "success", "model_calls", "tool_calls", "signature"}
            and isinstance(value["instance"], str) and bool(_INSTANCE.fullmatch(value["instance"]))
            and isinstance(value["run"], str) and bool(_RUN.fullmatch(value["run"]))
            and isinstance(value["agent"], str) and bool(_AGENT.fullmatch(value["agent"]))
            and isinstance(value["session"], str) and len(value["session"]) <= 512
            and value["session"].startswith(f"agent:{value['agent']}:openai-user:")
            and type(value["success"]) is bool
            and isinstance(value["signature"], str) and bool(re.fullmatch(r"[A-Za-z0-9+/]{86}==", value["signature"]))
            and all(type(value[k]) is int and 0 <= value[k] <= 10000 for k in ("model_calls", "tool_calls")))


def unused_completed(value):
    return valid_receipt(value) and value["success"] and value["model_calls"] == 1 and value["tool_calls"] == 0


def valid_public_key(value):
    try:
        from cryptography.hazmat.primitives.serialization import load_der_public_key
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        return (isinstance(value, str) and len(value) <= 100
                and isinstance(load_der_public_key(base64.b64decode(value, validate=True)), Ed25519PublicKey))
    except (ValueError, TypeError):
        return False


def verified(value, public_key):
    if not valid_receipt(value) or not valid_public_key(public_key):
        return False
    from cryptography.hazmat.primitives.serialization import load_der_public_key
    from cryptography.exceptions import InvalidSignature
    try:
        key = load_der_public_key(base64.b64decode(public_key, validate=True))
        fields = ("instance", "run", "session", "agent", "success", "model_calls", "tool_calls")
        message = json.dumps([value[k] for k in fields], ensure_ascii=False, separators=(",", ":")).encode()
        key.verify(base64.b64decode(value["signature"], validate=True), message)
        return True
    except (ValueError, TypeError, InvalidSignature):
        return False
