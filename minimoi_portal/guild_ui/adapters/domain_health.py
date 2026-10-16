"""Explicit-profile fixed-target liveness evidence. No actions, auth forwarding or model calls."""
from __future__ import annotations
import json
import time
import requests
from .contract import live_ok, live_unknown, not_configured

ENV = "MINIMOI_MC_DOMAIN_HEALTH"
PRODUCTION_ENV = "MINIMOI_MC_DOMAIN_HEALTH_PRODUCTION"

def profile_for(origin, environ):
    """Operator configuration only; never a browser/model supplied destination."""
    if origin == "https://dev.minimoi.ai" and environ.get(ENV) == "1":
        return "dev"
    on = lambda name: str(environ.get(name, "")).strip().lower() in {"1", "true", "on", "yes"}
    if (origin == "https://minimoi.ai" and environ.get(PRODUCTION_ENV) == "1"
            and on("MINIMOI_GUILD_NEXT") and on("MINIMOI_GUILD_PRODUCTION")
            and str(environ.get("MINIMOI_GUILD_CHAT", "")).strip().lower() == "cos"):
        return "production"
    return None

TARGETS = (("Curator", "curator", "CURATOR_BACKEND", "http://curator:8766"),
           ("Mein Deutsch", "german", "GERMAN_BACKEND", "http://german:8767"),
           ("Meu Português", "portuguese", "PORTUGUESE_BACKEND", "http://portuguese:8770"),
           ("Guild", "portal", None, "http://127.0.0.1:5001"))
MAX_BYTES = 4096


def snapshot(config, *, origin, environ, session_factory=requests.Session, clock=time.monotonic):
    # The fixed internal service origins are identical across isolated Compose
    # deployments, but admission and evidence explicitly identify the profile.
    # Matching config is required; never follow caller URLs or ambient proxies.
    profile = profile_for(origin, environ)
    if profile is None:
        return [(label, not_configured("internal_health", "Deployment liveness probe"))
                for label, *_ in TARGETS]
    result = []
    deadline = clock() + 2.5
    with session_factory() as session:
        session.trust_env = False
        for label, expected, key, fixed in TARGETS:
            evidence = "internal_" + profile + "_health:" + expected
            if key and config.get(key) != fixed:
                result.append((label, not_configured(evidence, "Expected fixed service origin")))
                continue
            if clock() >= deadline:
                result.append((label, live_unknown("read_failed", "probe_budget_exhausted", evidence)))
                continue
            try:
                remaining = max(.01, min(.3, deadline - clock()))
                with session.get(fixed + "/health", timeout=(remaining, remaining),
                                 allow_redirects=False, stream=True) as response:
                    status = response.status_code
                    if status != 200:
                        code = "unavailable_from_probe" if status >= 500 else "unexpected_http_status"
                        value = live_unknown("read_failed", code, evidence)
                    else:
                        raw = bytearray()
                        # One-byte chunks let the elapsed budget be checked even
                        # for a slowly trickled response; read timeout bounds stalls.
                        for chunk in response.iter_content(chunk_size=1):
                            if clock() >= deadline:
                                raise ValueError("probe_budget_exhausted")
                            raw.extend(chunk)
                            if len(raw) > MAX_BYTES:
                                raise ValueError("response_too_large")
                        body = json.loads(raw)
                        if (not isinstance(body, dict) or body.get("status") != "ok"
                                or body.get("service") != expected):
                            raise ValueError("unexpected_service_response")
                        value = live_ok({"state": "healthy_liveness", "http_status": 200}, evidence, fresh_for_s=0)
                    result.append((label, value))
            except requests.RequestException:
                result.append((label, live_unknown("read_failed", "unavailable_from_probe", evidence)))
            except (ValueError, UnicodeError):
                result.append((label, live_unknown("invalid", "response_not_verified", evidence)))
    return result
