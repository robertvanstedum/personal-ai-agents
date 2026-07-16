"""
core/identity.py — single canonical request-identity resolver, shared
across every domain (German, Portuguese, and any future domain).

Replaces per-domain reimplementations that disagreed on fallback
behavior (see _working/Github-cleanup-july14/defect_multiuser_identity_resolution_2026-07-15.md
and spec_83_multiuser_identity_full.md). No domain should implement its
own version of this logic — import and call resolve_user_id() instead.
"""

from flask import Request


def resolve_user_id(request: Request) -> int | str | None:
    """
    Resolve the identity of the current request, forwarded by the portal's
    reverse proxy (minimoi_portal/proxy.py):
      - X-Minimoi-Auth-Id: numeric auth_id, when the account has one
        (owner/admin/guest — all accounts are expected to have one once
        the Postgres identity migration is complete).
      - X-Minimoi-Username: string username, used only if no auth_id is
        present (should become rare/unused once every account has an
        auth_id, but kept as a fallback for any account that predates
        the migration or is missing one for another reason).
      - Neither present: return None. Callers must treat None as "no
        proven identity" and fail closed (return/allow nothing), never
        fall back to an unfiltered or unscoped operation.
    """
    auth_id = request.headers.get("X-Minimoi-Auth-Id")
    if auth_id:
        try:
            return int(auth_id)
        except ValueError:
            pass
    username = request.headers.get("X-Minimoi-Username")
    return username if username else None
