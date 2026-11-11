"""Admission-time depth selection shared by MC and CoS.

Only a host catalogue opts a conversation role in. No prompt, attachment or model
output may name a route. The owner's unaugmented words can choose one of the two
host-approved routes; the conversation's stored selection is never rewritten.
Runtime continuation is separate from this admission policy.
"""
from __future__ import annotations

import re

from . import RouteRefused, check_selectable, selection_of, snapshot

# Deliberately anchored: quoted documents and earlier conversation are not routing
# instructions. Unrecognized important questions belong to the lead's judgement.
_QUICK = re.compile(r"^\s*(?:please\s+)?quick answer\b", re.I)
_DEEP = re.compile(
    r"^\s*(?:please\s+)?(?:think harder(?: about this)?|this is important|look into this|go deep)\b", re.I
)


def explicit_depth(owner_text: str) -> str | None:
    if _QUICK.match(owner_text):
        return "quick"
    if _DEEP.match(owner_text):
        return "deep"
    return None


def validate_policy(catalogue: dict, role_name: str) -> dict | None:
    role = catalogue["roles"][role_name]
    policy = role.get("depth_routes")
    if policy is None:
        return None
    if (role_name not in ("mc-conversation", "cos-conversation") or not role.get("selectable")
            or not isinstance(policy, dict) or set(policy) != {"quick", "deep"}
            or not all(isinstance(v, str) for v in policy.values())):
        raise ValueError("invalid conversation depth routes")
    routes = {kind: check_selectable(catalogue, role["agent"], rid) for kind, rid in policy.items()}
    if any(r.get("runtime") != "openclaw" or not r.get("runtime_override", True) for r in routes.values()):
        raise ValueError("conversation depth requires explicit OpenClaw model routes")
    # Effort metadata is not transport. Until effort transport is proven, each
    # depth must have its own runtime model reference (including gateway alias).
    if len({(r["provider"], r["model"]) for r in routes.values()}) != 2:
        raise ValueError("conversation depth routes must have distinct runtime models")
    if role["route"] not in policy.values():
        raise ValueError("conversation default must belong to its depth routes")
    return policy


def for_owner_turn(catalogue: dict, role_name: str, conv: dict | None, owner_text: str) -> dict:
    """Pin the selected route and deep continuation candidate at admission.

    An explicit conversation model outside the host's pair retains its existing
    semantics. The pair is not enabled by default in any shipped catalogue.
    """
    original = snapshot(catalogue, role_name, conv)
    policy = validate_policy(catalogue, role_name)
    if policy is None or original["route_id"] not in policy.values():
        return original
    if not isinstance(owner_text, str):
        raise RouteRefused("depth_text_invalid", "The original message is unavailable. Nothing was sent.")
    requested = explicit_depth(owner_text)
    kind = requested or ("deep" if original["route_id"] == policy["deep"] else "quick")
    selection = selection_of(conv or {}, original["agent"])

    def pin(which):
        return snapshot(catalogue, role_name, {"routes": {original["agent"]: {
            **selection, "route_id": policy[which],
        }}})

    selected = pin(kind)
    selected["depth"] = {"kind": kind, "explicit": requested is not None,
                         "allow_continuation": kind == "quick" and requested is None}
    if selected["depth"]["allow_continuation"]:
        selected["depth"]["continuation_route"] = pin("deep")
    return selected
