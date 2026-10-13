"""Who may use Rooms (Rooms identity, Slice 2): the owner switches Rooms on or off for a mini-moi account. Owner-only, behind the same write guard
as every Guild write. Access is data (the binding file, audited), never a list in code; granting creates the person's Records identity (idempotent,
by a signed call as the owner) and activates the grant only after that succeeded; withdrawing applies on the very next Rooms call.
"""
from __future__ import annotations

from flask import current_app, jsonify

from .. import rooms_identity as RI
from .security import json_error


def _api():
    from . import api
    return api


def _bridge():
    return current_app.extensions.get("records_bridge") or {}


def _access():
    return current_app.extensions.get("rooms_access")


def _accounts():
    """The mini-moi accounts the owner can choose from: [{username, label, tier}]. A reader failing gives [] (shown as unavailable)."""
    reader = current_app.extensions.get("rooms_accounts")
    try:
        return list(reader()) if reader else []
    except Exception:
        return None


def _state(binding):
    if not binding:
        return "none"
    if binding.get("active"):
        return "granted"
    return "pending" if binding.get("pending") else "withdrawn"


def access_list():
    api = _api()
    access = _access()
    if _bridge().get("state") != "on" or access is None:
        return jsonify({"enabled": False, "accounts": []})
    rows = access.listing()
    accounts = _accounts()
    if rows is None or accounts is None:
        return json_error("unavailable", "The Rooms access list cannot be read right now. Nothing was changed.", 503)
    by_account = {r["account"]: r for r in rows}
    out = []
    for a in accounts:
        row = by_account.get(f"account:{a['username']}")
        out.append({"username": a["username"], "label": a.get("label") or a["username"], "tier": a.get("tier", ""),
                    "state": _state(row), "granted_by": (row or {}).get("granted_by"), "granted_at": (row or {}).get("granted_at"),
                    "is_owner": bool(row and row.get("principal") == RI.OWNER_PRINCIPAL)})
    return jsonify({"enabled": True, "accounts": out})


def _choose(body):
    username = body.get("username")
    if not isinstance(username, str) or not username or len(username) > 200:
        return None, json_error("invalid", "Choose an account. Nothing was changed.", 422)
    accounts = _accounts()
    if accounts is None:
        return None, json_error("unavailable", "The account list cannot be read right now. Nothing was changed.", 503)
    for a in accounts:
        if a["username"] == username:
            return a, None
    return None, json_error("not_found", "That account is not in the account list. Nothing was changed.", 404)


def _owner_name():
    return (_api().cfg()["current_user"]() or {}).get("username") or "owner"


def access_grant():
    api = _api()
    access = _access()
    if _bridge().get("state") != "on" or access is None:
        return json_error("rooms_off", "Rooms are not connected on this portal. Nothing was changed.", 409)
    refusal, body = api._write_body()
    if refusal is not None:
        return refusal
    account, error = _choose(body)
    if error is not None:
        return error
    call = current_app.extensions.get("rooms_call")
    name = f"account:{account['username']}"
    try:
        row = access.begin_grant(name, account.get("label") or account["username"], _owner_name())
    except ValueError as exc:
        return json_error("refused", f"{exc}. Nothing was changed.", 409)
    if row.get("active"):
        return jsonify({"username": account["username"], "state": "granted", "changed": False})
    status, answer = call("POST", "/api/v1/portal/principals", {"id": row["principal"], "label": (account.get("label") or account["username"])[:100]}) if call else (0, None)
    if status != 200:
        RI_LOG.warning("rooms access: Records could not create the person for a grant (status %s)", status)
        return json_error("not_finished", "Rooms could not finish setting this person up, so access is not switched on yet. "
                          "Nothing was duplicated; try again.", 502, username=account["username"], state="pending")
    access.complete_grant(name, _owner_name())
    return jsonify({"username": account["username"], "state": "granted", "changed": True})


def access_withdraw():
    api = _api()
    access = _access()
    if _bridge().get("state") != "on" or access is None:
        return json_error("rooms_off", "Rooms are not connected on this portal. Nothing was changed.", 409)
    refusal, body = api._write_body()
    if refusal is not None:
        return refusal
    account, error = _choose(body)
    if error is not None:
        return error
    try:
        access.withdraw(f"account:{account['username']}", _owner_name())
    except ValueError as exc:
        return json_error("refused", f"{exc}. Nothing was changed.", 409)
    return jsonify({"username": account["username"], "state": "withdrawn"})


import logging  # noqa: E402

RI_LOG = logging.getLogger(__name__)

RULES = [
    ("/rooms/access", "api_rooms_access", access_list, ["GET"]),
    ("/rooms/access/grant", "api_rooms_access_grant", access_grant, ["POST"]),
    ("/rooms/access/withdraw", "api_rooms_access_withdraw", access_withdraw, ["POST"]),
]
