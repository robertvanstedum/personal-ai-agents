"""The telemetry vocabulary (spec 161 §5.5): the closed set of metrics, labels, services,
components, events and summary codes MiniMoi may emit. Anything not listed is a contract error.

Values are closed sets or tight patterns, never free text, so telemetry cannot carry message
text, names, emails, paths outside the allowed shapes, prompts or secrets (spec 161 P4).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "observability" / "vocabulary.yaml"
SCHEMA = "minimoi-telemetry-vocabulary/1"
SEVERITIES = {"clear": 9, "warning": 13, "major": 17, "critical": 21}   # OTel SeverityNumber (§5.4)
STATES = ("opened", "cleared", "info")
_NAME = re.compile(r"^minimoi_[a-z0-9_]{1,100}$")
MAX_VALUE_LEN = 96


class TelemetryContractError(ValueError):
    """A signal that is not in the vocabulary. The message names the field, never the value."""


@dataclass(frozen=True)
class Metric:
    name: str
    type: str
    unit: str
    labels: tuple


@dataclass(frozen=True)
class Vocabulary:
    schema_version: int
    services: frozenset
    labels: dict           # key -> ("values", frozenset) | ("pattern", compiled)
    metrics: dict          # name -> Metric
    components: frozenset
    events: dict           # event.name -> frozenset(summary codes)
    alert_key: re.Pattern

    def check_service(self, service_name: str):
        if service_name not in self.services:
            raise TelemetryContractError("service_name_not_in_vocabulary")

    def check_label(self, key: str, value) -> str:
        rule = self.labels.get(key)
        if rule is None:
            raise TelemetryContractError(f"label_key_not_in_vocabulary:{key}")
        if not isinstance(value, str) or not value or len(value) > MAX_VALUE_LEN:
            raise TelemetryContractError(f"label_value_invalid:{key}")
        kind, allowed = rule
        if (kind == "values" and value not in allowed) or (kind == "pattern" and not allowed.match(value)):
            raise TelemetryContractError(f"label_value_not_allowed:{key}")
        return value

    def check_metric(self, name: str, kind: str, labels: dict) -> Metric:
        metric = self.metrics.get(name)
        if metric is None:
            raise TelemetryContractError("metric_not_in_vocabulary")
        if metric.type != kind:
            raise TelemetryContractError(f"metric_type_mismatch:{name}")
        if set(labels) != set(metric.labels):
            raise TelemetryContractError(f"metric_labels_mismatch:{name}")
        for key, value in labels.items():
            self.check_label(key, value)
        return metric

    def check_event(self, *, event_name: str, alert_key: str, state: str, severity: str,
                    summary_code: str, component: str):
        codes = self.events.get(event_name)
        if codes is None:
            raise TelemetryContractError("event_name_not_in_vocabulary")
        if summary_code not in codes:
            raise TelemetryContractError("summary_code_not_allowed")
        if state not in STATES:
            raise TelemetryContractError("event_state_invalid")
        if severity not in SEVERITIES:
            raise TelemetryContractError("severity_invalid")
        if component not in self.components:
            raise TelemetryContractError("component_not_in_vocabulary")
        if not isinstance(alert_key, str) or not self.alert_key.match(alert_key):
            raise TelemetryContractError("alert_key_invalid")


def parse(doc) -> Vocabulary:
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise TelemetryContractError("vocabulary_schema_unknown")
    labels = {}
    for key, rule in (doc.get("labels") or {}).items():
        if isinstance(rule, dict) and isinstance(rule.get("values"), list):
            labels[key] = ("values", frozenset(str(v) for v in rule["values"]))
        elif isinstance(rule, dict) and isinstance(rule.get("pattern"), str):
            pattern = rule["pattern"]
            if not (pattern.startswith("^") and pattern.endswith("$")):
                raise TelemetryContractError(f"label_pattern_unanchored:{key}")
            labels[key] = ("pattern", re.compile(pattern))
        else:
            raise TelemetryContractError(f"label_rule_invalid:{key}")
    metrics = {}
    for name, spec in (doc.get("metrics") or {}).items():
        if not _NAME.match(name) or not isinstance(spec, dict) or spec.get("type") not in ("gauge", "counter"):
            raise TelemetryContractError("metric_definition_invalid")
        if spec["type"] == "counter" and not name.endswith("_total"):
            raise TelemetryContractError("counter_name_must_end_total")
        unknown = [k for k in spec.get("labels", []) if k not in labels]
        if unknown:
            raise TelemetryContractError("metric_label_not_defined")
        metrics[name] = Metric(name, spec["type"], str(spec.get("unit", "1")), tuple(spec.get("labels", [])))
    events = {name: frozenset(codes) for name, codes in (doc.get("events") or {}).items()}
    key_rule = (doc.get("alert_key") or {}).get("pattern")
    if not isinstance(key_rule, str):
        raise TelemetryContractError("alert_key_pattern_missing")
    return Vocabulary(schema_version=int(doc.get("schema_version", 1)),
                      services=frozenset(doc.get("services") or []), labels=labels, metrics=metrics,
                      components=frozenset(doc.get("components") or []), events=events,
                      alert_key=re.compile(key_rule))


@lru_cache(maxsize=4)
def load(path: str | None = None) -> Vocabulary:
    target = Path(path) if path else DEFAULT_PATH
    with open(target, encoding="utf-8") as fh:
        return parse(yaml.safe_load(fh))
