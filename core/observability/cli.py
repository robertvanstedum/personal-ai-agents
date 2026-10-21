"""``minimoi-telemetry``: the library for shell jobs (spec 161 §7.5).

    python -m core.observability.cli gauge <metric> <value> [--label k=v ...]
    python -m core.observability.cli count <metric> [<n>] [--label k=v ...]
    python -m core.observability.cli event --name <event.name> --key <alert_key> --state opened
        --severity major --summary exit_nonzero --component jobs [--label k=v ...]

The exit code is always 0: a monitoring problem must never fail the job that reports it. A
contract error prints one short line to stderr (never the value) and records a drop.
"""
from __future__ import annotations

import argparse
import os
import sys

from . import configure
from .vocabulary import TelemetryContractError


def _labels(pairs):
    out = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise TelemetryContractError("label_syntax")
        k, v = pair.split("=", 1)
        out[k] = v
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="minimoi-telemetry")
    p.add_argument("--service", default=os.environ.get("MINIMOI_TELEMETRY_SERVICE", "minimoi-mac-jobs"))
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gauge")
    g.add_argument("metric")
    g.add_argument("value", type=float)
    g.add_argument("--label", action="append")
    c = sub.add_parser("count")
    c.add_argument("metric")
    c.add_argument("n", type=float, nargs="?", default=1)
    c.add_argument("--label", action="append")
    e = sub.add_parser("event")
    e.add_argument("--name", required=True)
    e.add_argument("--key", required=True)
    e.add_argument("--state", required=True)
    e.add_argument("--severity", required=True)
    e.add_argument("--summary", required=True)
    e.add_argument("--component", required=True)
    e.add_argument("--label", action="append")
    try:
        args = p.parse_args(argv)
    except SystemExit:
        return 0
    try:
        tel = configure(service_name=args.service, heartbeat_s=0, export_interval_s=1.0)
        labels = _labels(args.label)
        if args.cmd == "gauge":
            tel.gauge(args.metric, args.value, **labels)
        elif args.cmd == "count":
            tel.count(args.metric, args.n, **labels)
        else:
            tel.event(event_name=args.name, alert_key=args.key, state=args.state, severity=args.severity,
                      summary_code=args.summary, component=args.component, **labels)
        tel.flush(5.0)
    except TelemetryContractError as exc:
        print(f"minimoi-telemetry: refused ({exc})", file=sys.stderr)
    except Exception as exc:        # never fail the calling job
        print(f"minimoi-telemetry: not sent ({type(exc).__name__})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
