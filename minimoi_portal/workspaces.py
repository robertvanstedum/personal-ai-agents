"""Shared workspace visibility for portal navigation.

The route decorators in ``app.py`` remain the security boundary.  These
helpers centralize the same access decisions for presentation so the landing
page, signed-in home, and injected navigation do not drift.
"""

from minimoi_portal import domain_auth


WORKSPACES = (
    {
        "key": "curator",
        "label": "Curator",
        "path": "/app/curator",
        "eyebrow": "Research and decisions",
        "summary": "Daily briefings, scans, deeper dives, and continuing research.",
        "image": "/static/tour/curator-landing.jpg",
        "public_visible": True,
    },
    {
        "key": "german",
        "label": "Mein Deutsch",
        "path": "/app/german",
        "eyebrow": "Language immersion",
        "summary": "Contemporary reading, conversation, writing, and correction.",
        "image": "/static/tour/german-landing.jpg",
        "public_visible": True,
    },
    {
        "key": "portuguese",
        "label": "Meu Português",
        "path": "/app/portuguese",
        "eyebrow": "Language immersion",
        "summary": "Reading and real-world practice for a multilingual household.",
        "image": "/static/tour/portuguese-landing.jpg",
        "public_visible": True,
    },
    {
        "key": "guild",
        "label": "Guild",
        "path": "/guild",
        "eyebrow": "Build and operations",
        "summary": "Specs, build work, operating status, and the next iteration.",
        "image": "/static/tour/guild-landing.jpg",
        "public_visible": True,
    },
    {
        "key": "cos",
        "label": "Chief of Staff",
        "short_label": "CoS",
        "path": "/app/cos",
        "eyebrow": "Cross-domain coordination",
        "summary": "Confer, record, track, and carry context across the system.",
        "image": None,
        "public_visible": False,
    },
)


def can_access_workspace(user: dict | None, key: str) -> bool:
    """Return whether ``user`` passes the existing route-level access policy."""
    if not user:
        return False

    tier = user.get("tier")

    if key in ("guild", "cos"):
        return tier == "owner"

    if key in ("curator", "german"):
        # Existing JSON-backed family guests have no auth_id and retain the
        # restricted Curator/German experience. Domain-specific token users
        # have an auth_id and do not receive those domains implicitly.
        return not (tier == "guest" and user.get("auth_id"))

    if key == "portuguese":
        if tier in ("owner", "admin"):
            return True
        auth_id = user.get("auth_id")
        if not auth_id:
            return False
        try:
            return bool(domain_auth.has_domain_access(auth_id, "portuguese"))
        except Exception:
            return False

    return False


def workspace_navigation(user: dict | None) -> list[dict]:
    """Build the visible workspace list for the current landing/nav state.

    Signed-out visitors see public workspaces as locked orientation labels.
    Signed-in users see only workspaces they can actually open.
    """
    items = []
    for definition in WORKSPACES:
        allowed = can_access_workspace(user, definition["key"])
        if user:
            if not allowed:
                continue
        elif not definition["public_visible"]:
            continue

        items.append({
            **definition,
            "allowed": allowed,
            "locked": not bool(user),
        })
    return items
