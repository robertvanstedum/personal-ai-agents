"""What MiniMoi is and what MC can do in it now (spec v1.2 §11.1; A09/A09b).

Reviewed manifests beside each domain (domains/<id>/manifest.json) say what a domain is for and which capabilities it
has or plans. They hold no authority, credentials or health claims. ``describe_house`` is a bounded index;
``describe_domain`` combines one manifest with what is installed and allowed right now and with live liveness:

  available        implemented, an installed tool, enabled by the policy in force, and the domain reachable
  disabled         a policy, portal dependency or plugin offer explicitly disables it
  unreachable      enabled, but the domain's liveness check failed just now
  not_implemented  planned in the manifest (with its milestone); no tool can do it today
  unknown          a required plugin offer or liveness check is not verified

Every answer carries ``observed_at`` and source refs (manifest path and digest, the liveness evidence), so MC can cite
why a capability is or is not available. Counts ("N open tickets") always need their own read; nothing here pretends.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from . import catalogue

ROOT = Path(__file__).resolve().parents[2]
DOMAINS = ("guild", "german", "portuguese", "curator", "cos")
SCHEMA = "minimoi-domain-manifest/1"
_FIELDS = {"schema", "domain_id", "display_name", "aliases", "purpose", "capabilities", "source_adapters", "links"}
_CAP_FIELDS = {"id", "kind", "label", "mc_tool", "status"}
_CAP_OPTIONAL = {"milestone", "note"}
_CAP_ID = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+")
STATES = ("available", "disabled", "unreachable", "not_implemented", "unknown")


class ManifestInvalid(Exception):
    pass


def _manifest(domain_id: str, root: Path = ROOT) -> tuple[dict, dict]:
    """(manifest, source ref). Strict: unknown fields, a tool name outside the catalogue or a mismatched id refuse."""
    path = root / "domains" / domain_id / "manifest.json"
    raw = path.read_bytes()
    doc = json.loads(raw)
    if (type(doc) is not dict or set(doc) != _FIELDS or doc["schema"] != SCHEMA or doc["domain_id"] != domain_id
            or not isinstance(doc["capabilities"], list)):
        raise ManifestInvalid(domain_id)
    for cap in doc["capabilities"]:
        if (type(cap) is not dict or not _CAP_FIELDS <= set(cap) <= _CAP_FIELDS | _CAP_OPTIONAL
                or not _CAP_ID.fullmatch(str(cap["id"])) or cap["kind"] not in ("read", "action")
                or cap["status"] not in ("implemented", "planned")
                or (cap["mc_tool"] is not None and cap["mc_tool"] not in catalogue.TOOLS)
                or (cap["status"] == "implemented") != (cap["mc_tool"] is not None)):
            raise ManifestInvalid(domain_id)
    ref = {"manifest": f"domains/{domain_id}/manifest.json", "sha256": hashlib.sha256(raw).hexdigest()[:16]}
    return doc, ref


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _state(cap, enabled_tools, health, availability=None) -> str:
    if cap["status"] == "planned" or cap["mc_tool"] is None:
        return "not_implemented"
    if cap["mc_tool"] not in enabled_tools:
        return "disabled"
    state = (availability or {}).get(cap["mc_tool"], {}).get("state", "unknown")
    if state != "enabled":
        return "disabled" if state == "disabled" else "unknown"
    liveness = (health or {}).get("state")
    if liveness == "ok":
        return "available"
    if liveness == "down":
        return "unreachable"
    return "unknown"


def describe_house(*, enabled_tools, health_for=lambda domain_id: None, root: Path = ROOT, availability=None) -> dict:
    """A bounded index: each domain's name, purpose, and how many of its capabilities are available now."""
    out, refs = [], []
    for domain_id in DOMAINS:
        try:
            doc, ref = _manifest(domain_id, root)
        except (OSError, ValueError, ManifestInvalid):
            out.append({"domain": domain_id, "state": "unknown", "reason": "manifest_unavailable"})
            continue
        health = health_for(domain_id)
        states = [_state(c, enabled_tools, health, availability) for c in doc["capabilities"]]
        constraints = []
        if availability is not None:
            for c in doc["capabilities"]:
                for reason in availability.get(c["mc_tool"], {}).get("reasons", []):
                    if reason not in constraints:
                        constraints.append(reason)
        out.append({"domain": domain_id, "name": doc["display_name"], "aliases": doc["aliases"][:6],
                    "purpose": doc["purpose"][:300],
                    "capabilities": {s: states.count(s) for s in STATES if states.count(s)},
                    "constraints": constraints})
        refs.append(ref)
    return {"observed_at": _now(), "domains": out, "sources": refs}


def describe_domain(domain_id: str, *, enabled_tools, health_for=lambda domain_id: None, root: Path = ROOT,
                    availability=None) -> dict:
    doc, ref = _manifest(domain_id, root)
    health = health_for(domain_id)
    caps = [{"id": c["id"], "label": c["label"], "kind": c["kind"], "state": _state(c, enabled_tools, health, availability),
             **({"reasons": availability.get(c["mc_tool"], {}).get("reasons", [])}
                if availability is not None and c["mc_tool"] else {}),
             **({"tool": c["mc_tool"]} if c["mc_tool"] else {}),
             **({"milestone": c["milestone"]} if c.get("milestone") else {}),
             **({"note": c["note"]} if c.get("note") else {})} for c in doc["capabilities"]]
    sources = [ref] + ([{"liveness": health.get("evidence"), "observed_at": health.get("observed_at")}]
                       if isinstance(health, dict) and health.get("evidence") else [])
    return {"domain": domain_id, "name": doc["display_name"], "aliases": doc["aliases"], "purpose": doc["purpose"],
            "observed_at": _now(), "liveness": (health or {}).get("state", "unknown"), "capabilities": caps,
            "sources": sources}


def capability(domain_id: str, capability_id: str, root: Path = ROOT):
    """(manifest capability, source ref) or (None, ref)."""
    doc, ref = _manifest(domain_id, root)
    return next((c for c in doc["capabilities"] if c["id"] == capability_id), None), ref
