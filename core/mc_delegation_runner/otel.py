"""The census in OpenTelemetry's GenAI format, as OTLP/JSON files beside the census (host runtime spec v0.5 R6.6-d).

* **What is written.** ``census/otlp/traces.json`` (one ``ExportTraceServiceRequest``) and ``census/otlp/metrics.json``
  (one ``ExportMetricsServiceRequest``), each rewritten atomically from the host's census rows: a snapshot, so each
  agent instance is exactly one span, never one per rewrite. MiniMoi's census file stays the source of truth; these
  files are a view of it, and no viewer is chosen here (§10).
* **Spans.** One ``invoke_agent`` span per agent instance. Its span id is derived from ``agent_id`` and its parent's
  from ``parent_id`` (every parent resolves to a census row: the census writes a placeholder row for a parent it never
  saw, CEN-5). One trace per work item, or per root session for an agent that serves none. The span runs from the
  agent's first event to its end, or, while it has not ended, to its latest event; ``minimoi.census.state`` says
  which (the span's end time is never read as the agent's end).
* **Attributes** are pinned to one semantic-conventions version (``SEMCONV_VERSION``), listed in the mapping file
  ``core/mc_delegation_dispatcher/mappings/otel_genai.md`` and checked by a test: ``gen_ai.*`` where the census knows
  the value, ``minimoi.*`` for the rest. **No content attribute** is ever written: no prompt, completion, message,
  system instruction, tool argument or tool result (``CONTENT_ATTRIBUTES`` is the refusal list). Unknown values are
  left out, never written as zero.
* **Metrics.** ``gen_ai.client.token.usage`` (a histogram per provider, model and token type, from the tokens the
  vendors reported) and ``minimoi.census.agents`` (a gauge: agents by derived state).
* **The exporter** (``OtlpHttpExporter``) can post these same documents to a configured OTLP/HTTP endpoint. It is off
  by default: nothing in the runtime builds one, it is removable, and it is outside acceptance.

Stdlib only; Python 3.9-compatible.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .census import parse_at

SEMCONV_VERSION = "1.37.0"
SCHEMA_URL = "https://opentelemetry.io/schemas/" + SEMCONV_VERSION
SCOPE = {"name": "minimoi.census", "version": "1"}
SERVICE = "minimoi-runtime"
SPAN_KIND_INTERNAL = 1
STATUS_UNSET, STATUS_OK, STATUS_ERROR = 0, 1, 2
TOKEN_BOUNDS = [1, 4, 16, 64, 256, 1024, 4096, 16384, 65536, 262144, 1048576, 4194304, 16777216, 67108864]
AGGREGATION_CUMULATIVE = 2
# The census vendor word -> gen_ai.provider.name's well-known value. A vendor not listed writes no provider name.
PROVIDERS = {"anthropic": "anthropic", "openai": "openai", "xai": "x_ai"}
# Every attribute name this writer may emit (the mapping file lists the same names, and a test holds them equal).
GEN_AI_ATTRIBUTES = ("gen_ai.operation.name", "gen_ai.agent.id", "gen_ai.agent.name", "gen_ai.provider.name",
                     "gen_ai.request.model", "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens",
                     "gen_ai.token.type")
MINIMOI_ATTRIBUTES = ("minimoi.item.id", "minimoi.host.id", "minimoi.environment", "minimoi.census.state",
                      "minimoi.census.reason", "minimoi.cost", "minimoi.cost.unit", "minimoi.usage.complete",
                      "minimoi.census.source", "minimoi.census.verified", "minimoi.census.children",
                      "minimoi.start_path", "minimoi.route", "minimoi.parent.id")
RESOURCE_ATTRIBUTES = ("service.name", "minimoi.host.id", "minimoi.environment")
# Never written (R6.6-d: message content is off): the semantic conventions' content-carrying names.
CONTENT_ATTRIBUTES = ("gen_ai.prompt", "gen_ai.completion", "gen_ai.input.messages", "gen_ai.output.messages",
                      "gen_ai.system_instructions", "gen_ai.tool.call.arguments", "gen_ai.tool.call.result",
                      "gen_ai.tool.definitions", "gen_ai.retrieval.query.text", "gen_ai.retrieval.documents",
                      "gen_ai.agent.description")
METRICS = ("gen_ai.client.token.usage", "minimoi.census.agents")


def span_id(agent_id) -> str:
    """8 bytes, lower-case hex, from the agent id: the same agent is the same span in every rewrite."""
    return hashlib.sha256(("span:" + agent_id).encode("utf-8")).hexdigest()[:16]


def trace_id(key) -> str:
    """16 bytes, lower-case hex: one trace per work item (``item:<id>``) or root session (``root:<agent id>``)."""
    return hashlib.sha256(("trace:" + key).encode("utf-8")).hexdigest()[:32]


def _nanos(at) -> str | None:
    if not at:
        return None
    t = parse_at(at)
    return str((int(t.timestamp()) * 1_000_000_000) + t.microsecond * 1000)


def _attr(key, value):
    if value is None:
        return None
    if type(value) is bool:
        v = {"boolValue": value}
    elif type(value) is int:
        v = {"intValue": str(value)}
    elif type(value) is float:
        v = {"doubleValue": value}
    else:
        v = {"stringValue": str(value)}
    return {"key": key, "value": v}


def _attrs(pairs) -> list:
    return [a for a in (_attr(k, v) for k, v in pairs) if a is not None]


def _root(rows_by_id, row):
    seen = set()
    while row["parent_id"] is not None and row["parent_id"] in rows_by_id and row["agent_id"] not in seen:
        seen.add(row["agent_id"])
        row = rows_by_id[row["parent_id"]]
    return row


def spans(rows) -> list:
    """The census rows as OTLP spans (JSON form): one per agent."""
    by_id = {r["agent_id"]: r for r in rows}
    out = []
    for r in rows:
        root = _root(by_id, r)
        trace_key = "item:" + r["item_id"] if r["item_id"] else "root:" + root["agent_id"]
        u = r["usage"] or {}
        start = _nanos(r["started_at"] or r["last_activity_at"])
        end = _nanos(r["ended_at"] or r["last_activity_at"] or r["started_at"])
        if start is None:
            start = end = "0"                                   # a placeholder parent: no event time is known
        status = STATUS_OK if r["state"] == "done" else STATUS_ERROR if r["state"] == "failed" else STATUS_UNSET
        span = {
            "traceId": trace_id(trace_key), "spanId": span_id(r["agent_id"]),
            "name": "invoke_agent " + (r["role"] or r["agent_id"]), "kind": SPAN_KIND_INTERNAL,
            "startTimeUnixNano": start, "endTimeUnixNano": end,
            "attributes": _attrs([
                ("gen_ai.operation.name", "invoke_agent"), ("gen_ai.agent.id", r["agent_id"]),
                ("gen_ai.agent.name", r["role"]), ("gen_ai.provider.name", PROVIDERS.get(r["vendor"] or "")),
                ("gen_ai.request.model", r["model"]), ("gen_ai.usage.input_tokens", u.get("input_tokens")),
                ("gen_ai.usage.output_tokens", u.get("output_tokens")), ("minimoi.item.id", r["item_id"]),
                ("minimoi.host.id", r["host_id"]), ("minimoi.environment", r["environment"]),
                ("minimoi.census.state", r["state"]), ("minimoi.census.reason", r["reason"]),
                ("minimoi.cost", None if u.get("cost") is None else float(u["cost"])),
                ("minimoi.cost.unit", u.get("unit")), ("minimoi.usage.complete", u.get("complete")),
                ("minimoi.census.source", r["source"]), ("minimoi.census.verified", r["verified"]),
                ("minimoi.census.children", r["children"]), ("minimoi.start_path", r["start_path"]),
                ("minimoi.route", r["route"]), ("minimoi.parent.id", r["parent_id"])]),
            "status": {"code": status}}
        if r["parent_id"] is not None:
            span["parentSpanId"] = span_id(r["parent_id"])
        out.append(span)
    return out


def traces_document(rows, *, host_id, environment) -> dict:
    return {"resourceSpans": [{
        "resource": {"attributes": _attrs([("service.name", SERVICE), ("minimoi.host.id", host_id),
                                           ("minimoi.environment", environment)])},
        "scopeSpans": [{"scope": dict(SCOPE), "spans": spans(rows), "schemaUrl": SCHEMA_URL}],
        "schemaUrl": SCHEMA_URL}]}


def metrics_document(rows, *, host_id, environment, now=None) -> dict:
    """Token usage as reported (agents that reported none add nothing; unknown is never counted as zero), and the
    agents by derived state."""
    now = now or datetime.now(timezone.utc)
    t = str((int(now.timestamp()) * 1_000_000_000) + now.microsecond * 1000)
    starts = [_nanos(r["started_at"]) for r in rows if r["started_at"]]
    t0 = min(starts, key=int) if starts else t
    groups = {}
    for r in rows:
        u = r["usage"] or {}
        for kind, key in (("input", "input_tokens"), ("output", "output_tokens")):
            if u.get(key) is not None:
                g = groups.setdefault((PROVIDERS.get(r["vendor"] or ""), r["model"], kind), [])
                g.append(u[key])
    points = []
    for (provider, model, kind), values in sorted(groups.items(), key=lambda kv: tuple(x or "" for x in kv[0])):
        counts = [0] * (len(TOKEN_BOUNDS) + 1)
        for v in values:
            counts[next((i for i, b in enumerate(TOKEN_BOUNDS) if v <= b), len(TOKEN_BOUNDS))] += 1
        points.append({"attributes": _attrs([("gen_ai.operation.name", "invoke_agent"),
                                             ("gen_ai.provider.name", provider), ("gen_ai.request.model", model),
                                             ("gen_ai.token.type", kind)]),
                       "startTimeUnixNano": t0, "timeUnixNano": t, "count": str(len(values)),
                       "sum": float(sum(values)), "bucketCounts": [str(c) for c in counts],
                       "explicitBounds": [float(b) for b in TOKEN_BOUNDS]})
    states = {}
    for r in rows:
        states[r["state"]] = states.get(r["state"], 0) + 1
    gauge = [{"attributes": _attrs([("minimoi.census.state", s)]), "timeUnixNano": t, "asInt": str(n)}
             for s, n in sorted(states.items())]
    metrics = [{"name": "gen_ai.client.token.usage", "unit": "{token}",
                "description": "Number of input and output tokens used, as the vendors reported them.",
                "histogram": {"dataPoints": points, "aggregationTemporality": AGGREGATION_CUMULATIVE}},
               {"name": "minimoi.census.agents", "unit": "{agent}",
                "description": "Agent instances in the census, by derived state.", "gauge": {"dataPoints": gauge}}]
    return {"resourceMetrics": [{
        "resource": {"attributes": _attrs([("service.name", SERVICE), ("minimoi.host.id", host_id),
                                           ("minimoi.environment", environment)])},
        "scopeMetrics": [{"scope": dict(SCOPE), "metrics": metrics, "schemaUrl": SCHEMA_URL}],
        "schemaUrl": SCHEMA_URL}]}


def _write_atomic(path: Path, doc: dict):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(doc, fh, sort_keys=True, separators=(",", ":"))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def write(folder, rows, *, host_id, environment, now=None) -> dict:
    """Rewrite both OTLP/JSON files in ``folder`` from the census rows. Returns their paths."""
    folder = Path(folder)
    traces, metrics = folder / "traces.json", folder / "metrics.json"
    _write_atomic(traces, traces_document(rows, host_id=host_id, environment=environment))
    _write_atomic(metrics, metrics_document(rows, host_id=host_id, environment=environment, now=now))
    return {"traces": str(traces), "metrics": str(metrics)}


class OtlpHttpExporter:
    """An optional OTLP/HTTP (JSON) exporter for these documents. Off by default: the runtime never builds one, no
    configuration names one, and it is outside acceptance (R6.6-d). Choosing where it points is viewer selection
    (§10). ``opener`` is urllib's; tests pass a fake, and no network is reached in S1."""

    def __init__(self, endpoint, *, headers=None, opener=None, timeout=10):
        if not isinstance(endpoint, str) or not endpoint.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            raise ValueError("otlp_endpoint_invalid")
        self.endpoint, self.headers, self.timeout = endpoint.rstrip("/"), dict(headers or {}), timeout
        if opener is None:
            import urllib.request
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({})).open
        self._open = opener

    def _post(self, path, doc) -> int:
        import urllib.request
        req = urllib.request.Request(self.endpoint + path, data=json.dumps(doc).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json", **self.headers})
        with self._open(req, timeout=self.timeout) as r:
            return getattr(r, "status", 200)

    def export(self, rows, *, host_id, environment) -> dict:
        return {"traces": self._post("/v1/traces", traces_document(rows, host_id=host_id, environment=environment)),
                "metrics": self._post("/v1/metrics", metrics_document(rows, host_id=host_id,
                                                                      environment=environment))}
