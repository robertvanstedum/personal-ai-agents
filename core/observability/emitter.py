"""Telemetry emitter (spec 161 §7): facts out through OpenTelemetry, never judgements.

* Every call is validated against the vocabulary. In ``strict`` mode (dev and tests) a contract
  error raises; otherwise it is dropped and counted, so a telemetry bug never breaks a product
  path (P3).
* The local copy is written first: the latest gauge values in ``metrics-latest.json`` and
  events appended to ``events-YYYYMMDD.jsonl`` (0600, 14 days kept). A local-copy failure is
  counted, never raised.
* Export is OTLP/HTTP through the OTel SDK. Metrics are in-memory instruments read every
  ``export_interval_s``; events go through a bounded queue to a background thread, so the
  caller never waits. Overflow increments ``minimoi_telemetry_dropped_total``.
* With telemetry disabled only the local copy is written.
* A heartbeat gauge is set every ``heartbeat_s`` while configured.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import vocabulary as vocab_mod
from .vocabulary import SEVERITIES, TelemetryContractError

LOCAL_RETENTION_DAYS = 14
EVENT_QUEUE_SIZE = 2048


class Telemetry:
    def __init__(self, *, service_name: str, environment: str, local_dir: str | Path, enabled: bool,
                 strict: bool, endpoint: str | None = None, headers: dict | None = None,
                 version: str = "unknown", host_name: str | None = None, vocabulary=None,
                 metric_reader=None, log_exporter=None, export_interval_s: float = 60.0,
                 heartbeat_s: float = 60.0, queue_size: int = EVENT_QUEUE_SIZE):
        self.vocab = vocabulary or vocab_mod.load()
        if environment not in ("dev", "prod"):
            raise TelemetryContractError("environment_invalid")
        self.vocab.check_service(service_name)
        self.service_name, self.environment, self.strict = service_name, environment, strict
        self.local_dir = Path(local_dir)
        self.enabled = enabled
        self._lock = threading.Lock()
        self._latest: dict = {}
        self._dropped = {"metrics": 0, "logs": 0, "traces": 0}
        self._local_failures = 0
        self._instruments: dict = {}
        self._stop = threading.Event()
        self._meter_provider = self._logger_provider = None
        self._queue: queue.Queue = queue.Queue(maxsize=queue_size)
        if enabled:
            self._start_export(endpoint, headers, version, host_name, metric_reader, log_exporter,
                               export_interval_s)
        self._heartbeat_thread = None
        if heartbeat_s:
            self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, args=(heartbeat_s,),
                                                      name="minimoi-telemetry-heartbeat", daemon=True)
            self._heartbeat_thread.start()

    # ── export setup ────────────────────────────────────────────────────────
    def _start_export(self, endpoint, headers, version, host_name, metric_reader, log_exporter, interval_s):
        from opentelemetry.sdk._logs import LoggerProvider
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource

        resource = Resource.create({
            "service.name": self.service_name, "service.version": version,
            "deployment.environment.name": self.environment,
            "host.name": host_name or os.uname().nodename,
            "minimoi.telemetry.schema_version": self.vocab.schema_version})
        if metric_reader is None:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
            metric_reader = PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=f"{endpoint.rstrip('/')}/v1/metrics", headers=headers, timeout=5),
                export_interval_millis=int(interval_s * 1000), export_timeout_millis=5000)
        if log_exporter is None:
            from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
            log_exporter = OTLPLogExporter(endpoint=f"{endpoint.rstrip('/')}/v1/logs", headers=headers, timeout=5)
        self._meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
        self._meter = self._meter_provider.get_meter("minimoi.observability")
        self._logger_provider = LoggerProvider(resource=resource)
        self._logger_provider.add_log_record_processor(
            BatchLogRecordProcessor(log_exporter, max_queue_size=EVENT_QUEUE_SIZE, max_export_batch_size=512,
                                    export_timeout_millis=5000))
        self._logger = self._logger_provider.get_logger("minimoi.observability")
        self._worker = threading.Thread(target=self._drain, name="minimoi-telemetry-events", daemon=True)
        self._worker.start()

    # ── public API ──────────────────────────────────────────────────────────
    def gauge(self, name: str, value: float, **labels):
        self._metric("gauge", name, value, labels)

    def count(self, name: str, n: float = 1, **labels):
        self._metric("counter", name, n, labels)

    def event(self, *, alert_key: str, state: str, severity: str, summary_code: str, component: str,
              event_name: str, **labels):
        try:
            self.vocab.check_event(event_name=event_name, alert_key=alert_key, state=state, severity=severity,
                                   summary_code=summary_code, component=component)
            for key, value in labels.items():
                self.vocab.check_label(key, value)
        except TelemetryContractError as exc:
            return self._contract_failure("logs", exc)
        record = {"event.name": event_name, "minimoi.alert_key": alert_key, "minimoi.state": state,
                  "minimoi.component": component, "minimoi.summary_code": summary_code, **labels}
        now = datetime.now(timezone.utc)
        self._local_event(dict(record, severity=severity, observed_time=now.strftime("%Y-%m-%dT%H:%M:%SZ")), now)
        if not self.enabled:
            return
        try:
            self._queue.put_nowait((record, SEVERITIES[severity], summary_code, now))
        except queue.Full:
            self._drop("logs")

    def heartbeat(self):
        self.gauge("minimoi_telemetry_heartbeat_timestamp_seconds", time.time())

    def dropped(self) -> dict:
        with self._lock:
            return dict(self._dropped)

    def flush(self, timeout_s: float = 5.0):
        """Best effort, bounded: for short-lived processes (the CLI) before exit."""
        if not self.enabled:
            return
        deadline = time.monotonic() + timeout_s
        while not self._queue.empty() and time.monotonic() < deadline:
            time.sleep(0.01)
        remaining = max(0.1, deadline - time.monotonic())
        try:
            self._logger_provider.force_flush(int(remaining * 1000))
            self._meter_provider.force_flush(int(remaining * 1000))
        except Exception:
            pass

    def shutdown(self):
        self._stop.set()
        if self.enabled:
            self.flush(2.0)
            try:
                self._logger_provider.shutdown()
                self._meter_provider.shutdown(timeout_millis=2000)
            except Exception:
                pass

    # ── internals ───────────────────────────────────────────────────────────
    def _metric(self, kind, name, value, labels):
        try:
            self.vocab.check_metric(name, kind, labels)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value \
                    or value in (float("inf"), float("-inf")):
                raise TelemetryContractError("metric_value_invalid")
            if kind == "counter" and value < 0:
                raise TelemetryContractError("counter_decrement")
        except TelemetryContractError as exc:
            return self._contract_failure("metrics", exc)
        self._local_metric(kind, name, value, labels)
        if not self.enabled:
            return
        try:
            instrument = self._instrument(kind, name)
            if kind == "gauge":
                instrument.set(value, attributes=labels)
            else:
                instrument.add(value, attributes=labels)
        except Exception:
            self._drop("metrics")

    def _instrument(self, kind, name):
        with self._lock:
            inst = self._instruments.get(name)
            if inst is None:
                unit = self.vocab.metrics[name].unit
                inst = (self._meter.create_gauge(name, unit=unit) if kind == "gauge"
                        else self._meter.create_counter(name, unit=unit))
                self._instruments[name] = inst
            return inst

    def _contract_failure(self, signal, exc):
        if self.strict:
            raise exc
        self._drop(signal)

    def _drop(self, signal):
        with self._lock:
            self._dropped[signal] += 1
        if self.enabled:
            try:   # directly on the instrument: no validation path, so no recursion
                self._instrument("counter", "minimoi_telemetry_dropped_total").add(1, attributes={"signal": signal})
            except Exception:
                pass

    def _drain(self):
        while not self._stop.is_set():
            try:
                record, severity_number, body, when = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                from opentelemetry._logs import SeverityNumber
                self._logger.emit(timestamp=int(when.timestamp() * 1e9), severity_number=SeverityNumber(severity_number),
                                  body=body, attributes=record, event_name=record["event.name"])
            except Exception:
                self._drop("logs")

    def _heartbeat_loop(self, period):
        while not self._stop.is_set():
            try:
                self.heartbeat()
            except Exception:
                pass
            self._stop.wait(period)

    # ── local copy ──────────────────────────────────────────────────────────
    def _ensure_dir(self) -> bool:
        try:
            self.local_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            return True
        except OSError:
            self._local_failures += 1
            return False

    def _local_metric(self, kind, name, value, labels):
        if not self._ensure_dir():
            return
        key = name + "".join(f"|{k}={v}" for k, v in sorted(labels.items()))
        with self._lock:
            if kind == "counter":
                value = self._latest.get(key, {}).get("value", 0) + value
            self._latest[key] = {"name": name, "labels": labels, "value": value, "type": kind,
                                 "service": self.service_name,
                                 "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
            snapshot = dict(self._latest)
            try:
                path = self.local_dir / f"metrics-latest.{self.service_name}.json"
                tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as fh:
                    json.dump(snapshot, fh, sort_keys=True)
                os.replace(tmp, path)
            except OSError:
                self._local_failures += 1

    def _local_event(self, record, now):
        if not self._ensure_dir():
            return
        path = self.local_dir / f"events-{now.strftime('%Y%m%d')}.jsonl"
        try:
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, (json.dumps(dict(record, service=self.service_name), sort_keys=True) + "\n").encode())
            finally:
                os.close(fd)
            self._prune(now)
        except OSError:
            self._local_failures += 1

    def _prune(self, now):
        cutoff = (now - timedelta(days=LOCAL_RETENTION_DAYS)).strftime("%Y%m%d")
        for p in self.local_dir.glob("events-*.jsonl"):
            stamp = p.name[len("events-"):-len(".jsonl")]
            if stamp.isdigit() and stamp < cutoff:
                try:
                    p.unlink()
                except OSError:
                    pass
