"""CoS's model route: the same catalogue, tiers and rules as Master Craftsman (core/model_routes).

A host opts in by naming its catalogue. Elsewhere nothing changes: no route, no header, the runtime's default. The route
is the owner's Voice's model (#661, core.model_routes.picker), kept per owner for every chat and device; the per-chat
file beside CoS's conversation (its conversation file is written once, with a closed schema) keeps the evidence: the
last turn's receipt and the notice candidates.
It is fixed when a turn starts, a later change is the next turn's, and a route that cannot serve refuses that turn.
Only the page's owner turns read it. Telegram, the MC lane and meetings take the runtime's default, which is the same A."""
from __future__ import annotations

import hashlib

from core import durable_files as fsio
from core.model_routes import RouteRefused, conversation_role, load_catalogue, routes_enabled, served_receipt
from core.model_routes.depth import for_owner_turn
from core.model_routes.picker import owner_conversation
from core.partner_settings import PartnerSettings, root_for

AGENT = "cos"
ROLE = conversation_role(AGENT)


def enabled() -> bool:
    return routes_enabled()


def _path(history, conversation_id: str):
    return history.root / "routes" / (hashlib.sha256(conversation_id.encode()).hexdigest() + ".json")


def _selection(history, conversation_id: str) -> dict:
    return fsio.read_doc(_path(history, conversation_id)) or {}


def _catalogue() -> dict:
    try:
        return load_catalogue()
    except (OSError, ValueError) as exc:
        raise RouteRefused("route_unavailable", "The model routes could not be read. Nothing was sent.") from exc


def _request_owner():
    """The signed-in owner of this request (the portal's forwarded identity), or None outside a request."""
    from flask import has_request_context, request
    if not has_request_context():
        return None
    from core.identity import resolve_user_id
    return resolve_user_id(request)


def settings(history) -> PartnerSettings:
    """The owner's Voice for CoS (core.partner_settings), kept beside her history."""
    return PartnerSettings(root_for(history.root), AGENT)


def owner_voice(history, owner) -> str | None:
    if history is None or not owner:
        return None
    try:
        return settings(history).get(owner)["voice"]
    except OSError:
        import logging
        logging.getLogger("cos_routes").warning("cos voice choice unreadable; the default applies")
        return None


def for_turn(history, conversation_id: str, owner_text: str = "", attached: bool | None = None, owner=None) -> dict | None:
    """This turn's route, or None where routes are off. Refuses (a ValueError) rather than substitutes. ``attached``
    False (known: no files) lets a short turn take a light route the host's catalogue pairs (#593). The route is the
    owner's Voice's model (#661), the same for every chat and device; ``owner`` defaults to the request's owner."""
    if not enabled():
        return None
    with history._locked():
        selection = _selection(history, conversation_id)
    catalogue = _catalogue()
    voice = owner_voice(history, owner if owner is not None else _request_owner())
    conv = owner_conversation({"routes": {AGENT: selection}}, AGENT, voice, catalogue)
    return for_owner_turn(catalogue, ROLE, conv, owner_text, attached=attached)


def last_turn(history, conversation_id: str | None) -> dict | None:
    if not conversation_id:
        return None
    with history._locked():
        return _selection(history, conversation_id).get("last_turn")


def runtime_model(route: dict | None) -> str | None:
    """The model to ask the runtime for. None: its default, for a route that does not override it."""
    if route is None or not route.get("runtime_override", True):
        return None
    return f"{route['provider']}/{route['model']}"


def record(history, conversation_id: str, route: dict, served: dict | None) -> None:
    """What a turn was sent on and what the runtime's record showed. A failure here never touches the turn."""
    try:
        with history._locked():
            selection = _selection(history, conversation_id)
            fsio.write_doc(_path(history, conversation_id), {**selection, "last_turn": served_receipt(route, served)})
    except Exception:       # evidence only
        import logging
        logging.getLogger("cos_routes").warning("cos route receipt not kept")


def notice_for_turn(history, conversation_id, turn_id, route):
    """Prepare before saving; only canonical history answers count as presented."""
    from core.model_routes.presentation import prepare

    def answer(reference):
        saved = history.turn(conversation_id, reference)
        return (saved.get("reply_text") if saved and saved.get("status") in ("answered", "recovered") else None)

    def notice(selected, line):
        try:
            with history._locked():
                selection = _selection(history, conversation_id)
                rows, show = prepare(selection.get("notice_candidates", []), turn_id, selected, line, answer)
                fsio.write_doc(_path(history, conversation_id), {**selection, "notice_candidates": rows})
            return show
        except Exception:
            return bool(line)

    if route:
        notice(route, "")
    return notice


def with_gateway_fallback(served: dict | None, receipt: dict | None) -> dict | None:
    """The runtime's record, plus the gateway's own receipt when the gateway served a later model in its role chain
    (#658: a gateway fallback is shown, never silent). Only the serving model and its position are copied."""
    position = (receipt or {}).get("fallback_position")
    if not isinstance(position, int) or isinstance(position, bool) or position <= 0:
        return served
    return {**(served or {}), "provider": receipt.get("served_provider") or (served or {}).get("provider"),
            "model": receipt.get("served_model") or (served or {}).get("model"), "fallback_position": position}
