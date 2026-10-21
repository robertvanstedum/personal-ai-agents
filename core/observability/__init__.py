"""MiniMoi observability library (spec 161 §7). Components report facts; tools judge them.

    from core.observability import configure, gauge, count, event, heartbeat
    configure(service_name="minimoi-curator")
    gauge("minimoi_job_last_success_timestamp_seconds", 1761036000, job_id="curator-daily")

Configuration comes from the environment (spec 161 §7.4):
  MINIMOI_TELEMETRY_ENABLED   1 to export (default 0: local copy only)
  OTEL_EXPORTER_OTLP_ENDPOINT default http://otel-collector:4318
  OTEL_EXPORTER_OTLP_HEADERS_FILE  file with "Authorization=Bearer ..." (cross-host only)
  MINIMOI_TELEMETRY_LOCAL_DIR default /var/lib/minimoi/telemetry
  MINIMOI_ENV                 dev | prod (required)
  MINIMOI_TELEMETRY_STRICT    1 to raise on contract errors (default: 1 in dev, 0 in prod)
Calls before ``configure`` are ignored. Nothing here ever blocks or raises into product code
in prod.
"""
from __future__ import annotations

import os
import stat
import threading

from .emitter import Telemetry
from .vocabulary import TelemetryContractError

__all__ = ["configure", "gauge", "count", "event", "heartbeat", "current", "TelemetryContractError"]

_current: Telemetry | None = None
_lock = threading.Lock()


def _headers(path: str | None) -> dict | None:
    if not path:
        return None
    info = os.stat(path)
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise TelemetryContractError("otlp_headers_file_permissions")
    headers = {}
    for line in open(path, encoding="utf-8").read().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            headers[k.strip()] = v.strip()
    return headers


def configure(*, service_name: str, environ=None, **overrides) -> Telemetry:
    """Idempotent per process: a second call returns the existing instance."""
    global _current
    env = os.environ if environ is None else environ
    with _lock:
        if _current is not None:
            return _current
        environment = env.get("MINIMOI_ENV", "")
        strict_default = "0" if environment == "prod" else "1"
        settings = dict(
            service_name=service_name, environment=environment,
            local_dir=env.get("MINIMOI_TELEMETRY_LOCAL_DIR", "/var/lib/minimoi/telemetry"),
            enabled=env.get("MINIMOI_TELEMETRY_ENABLED", "0") == "1",
            strict=env.get("MINIMOI_TELEMETRY_STRICT", strict_default) == "1",
            endpoint=env.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4318"),
            headers=_headers(env.get("OTEL_EXPORTER_OTLP_HEADERS_FILE")),
            version=env.get("MINIMOI_VERSION", "unknown"))
        settings.update(overrides)
        _current = Telemetry(**settings)
        return _current


def current() -> Telemetry | None:
    return _current


def gauge(name, value, **labels):
    if _current is not None:
        _current.gauge(name, value, **labels)


def count(name, n=1, **labels):
    if _current is not None:
        _current.count(name, n, **labels)


def event(**kwargs):
    if _current is not None:
        _current.event(**kwargs)


def heartbeat():
    if _current is not None:
        _current.heartbeat()


def _reset_for_tests():
    global _current
    with _lock:
        if _current is not None:
            _current.shutdown()
        _current = None
