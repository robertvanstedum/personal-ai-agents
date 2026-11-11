"""CoS's model route per conversation: the same catalogue, tiers and rules as Master Craftsman (core/model_routes).

A host opts in by naming its catalogue. Elsewhere nothing changes: no route, no header, the runtime's default. The choice
lives beside CoS's conversation, in its own file, because the conversation file is written once and has a closed schema.
It is fixed when a turn starts, a later change is the next turn's, and a route that cannot serve refuses that turn.
Only the page's owner turns read it. Telegram, the MC lane and meetings take the runtime's default, which is the same A."""
from __future__ import annotations

import hashlib

from core import durable_files as fsio
from core.model_routes import RouteRefused, check_selectable, conversation_role, load_catalogue, routes_enabled, served_receipt, view
from core.model_routes.depth import for_owner_turn

AGENT = "cos"
ROLE = conversation_role(AGENT)


class RevisionConflict(Exception):
    def __init__(self, revision: int):
        super().__init__("route revision conflict")
        self.revision = revision


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


def for_turn(history, conversation_id: str, owner_text: str = "") -> dict | None:
    """This turn's route, or None where routes are off. Refuses (a ValueError) rather than substitutes."""
    if not enabled():
        return None
    with history._locked():
        selection = _selection(history, conversation_id)
    return for_owner_turn(_catalogue(), ROLE, {"routes": {AGENT: selection}}, owner_text)


def runtime_model(route: dict | None) -> str | None:
    """The model to ask the runtime for. None: its default, for a route that does not override it."""
    if route is None or not route.get("runtime_override", True):
        return None
    return f"{route['provider']}/{route['model']}"


def view_for(history, conversation_id: str) -> dict:
    with history._locked():
        selection = _selection(history, conversation_id)
    return view(_catalogue(), AGENT, {"routes": {AGENT: selection}})


def choose(history, conversation_id: str, route_id: str, revision: int) -> dict:
    catalogue = _catalogue()
    check_selectable(catalogue, AGENT, route_id)
    with history._locked():
        selection = _selection(history, conversation_id)
        current = int(selection.get("revision") or 0)
        if revision != current:
            raise RevisionConflict(current)
        selection = {**selection, "route_id": route_id, "revision": current + 1}
        fsio.write_doc(_path(history, conversation_id), selection)
    return view(catalogue, AGENT, {"routes": {AGENT: selection}})


def record(history, conversation_id: str, route: dict, served: dict | None) -> None:
    """What a turn was sent on and what the runtime's record showed. A failure here never touches the turn."""
    try:
        with history._locked():
            selection = _selection(history, conversation_id)
            fsio.write_doc(_path(history, conversation_id), {**selection, "last_turn": served_receipt(route, served)})
    except Exception:       # evidence only
        import logging
        logging.getLogger("cos_routes").warning("cos route receipt not kept")
