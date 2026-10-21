# MiniMoi observability platform: specification v0.2 (installation-ready)

**October 20, 2026 · Claude Code for Robert · platform-wide · GitHub issue #364**

**Status: v0.2 for Robert's review, then handoff and two reviews. It authorizes no build.**
- **Authorizes nothing yet:** no code, container, credential, external service, merge or production change.
- **Building starts only** after Robert approves, Codex reviews (`docs/specs/reviews/spec_161/`) and a second reviewer has reviewed.
- **The bar:** "a production quality solution with engineering principles that stand up to review."

**Change log**
- **v0.1** (`1cce1865`): architecture and principles.
- **v0.2** (this version):
  - pinned versions;
  - complete configuration files;
  - the compose service definition;
  - the library and bridge contracts;
  - step-by-step install, verification, upgrade and rollback;
  - a CI job;
  - standards with references (§3, §18);
  - concrete instructions for connecting Grafana, Splunk, Netcool and others (§16).

## Contents

1. In plain words
2. Goals and non-goals
3. Engineering standards adopted
4. Principles and how they are met
5. Signals and conventions
6. Architecture
7. The `core/observability` library
8. The status-file bridge
9. Alert rules (O1 set) and tests
10. Installation
11. Verification (acceptance tests)
12. Operations: change control, upgrades, rollback, retention
13. Security
14. Failure modes
15. Adoption checklist for any component
16. Future direction: connecting tools
17. Phases, ownership and decisions
18. References

## 1. In plain words

MiniMoi has many moving parts:
- **on EC2:** the portal, Curator, German and Portuguese, the Chief of Staff, Guild/MC, the model gateway and Postgres;
- **on the Mac:** scheduled jobs, the Vault sync and backups;
- **soon:** the delegation runner.

Today each watches itself differently, and thresholds are written into code. Robert:

> We put lots of them on different parts of the solution. Some tool listens or consumes them. In that tool, where we build it or use another, the thresholds, alarms etc are set up. Otherwise we are opening up code all the time.

**This spec makes observability one reusable platform capability:**
1. **Every part reports facts** through one small shared library: metrics (numbers), events (things that happened) and, later, traces (request timings). It uses the open standard **OpenTelemetry**, so no vendor is built in.
2. **One relay, the OpenTelemetry Collector,** forwards the facts to whatever tool is configured.
3. **Thresholds and alarms are reviewed configuration** in the standard Prometheus rule format, tested automatically in CI. Until a commercial tool is chosen, the open-source reference tools evaluate them (Prometheus and Alertmanager) and send Telegram. Moving to Grafana, Splunk, Netcool or another tool is a configuration change.
4. **The monitoring watches itself** through an external dead-man's switch.
5. **Nothing private is ever sent.**

## 2. Goals and non-goals

### Goals

| ID | Goal | Met by |
|---|---|---|
| G1 | One way to instrument any component (Python, and shell jobs) | §7 library and CLI; §15 checklist |
| G2 | Vendor neutrality | OTLP to the Collector; backends by exporter configuration (§16) |
| G3 | Operations owns judgement: thresholds, severities and routing are reviewed config | §9 rules; §12 change control |
| G4 | Actionable alerting: deduplicated, open and clear, with a runbook | §9 and §12 |
| G5 | Self-monitoring through an independent path | §9.3 and §10.6 |
| G6 | Privacy and security by construction | §4 P4/P5; §13 |
| G7 | The product never depends on telemetry | §7.3 non-blocking; local copy |
| G8 | Installable and verifiable by anyone following this document | §10 and §11 |

### Non-goals (v0.2)

- **Buying a commercial backend.** That is O3, Robert's decision.
- **Full tracing for every request.** That is O4; only the conventions are fixed here.
- **Retiring existing alerts on day one.** They run in parallel until parity is proven (§12.5).
- **Any AI model in the monitoring path.** MC and CoS may *read* data; they never evaluate alerts or act.

## 3. Engineering standards adopted

Each row names the standard and how this design conforms. Full references are in §18.

| # | Area | Standard / practice | Conformance |
|---|---|---|---|
| S1 | Telemetry model | OpenTelemetry Specification [R1], OTLP [R2] | All signals are OTel; OTLP/HTTP and gRPC |
| S2 | Naming | OTel Semantic Conventions v1.44.0 [R3] | Resource attributes `service.name`, `service.version`, `deployment.environment.name`, `host.name` |
| S3 | Metric naming and exposition | Prometheus naming practice [R6]; OpenMetrics [R5] | Base units, `_seconds`, `_ratio`, `_total`; units in names, never labels |
| S4 | Logs and severity | OTel Logs Data Model, SeverityNumber [R4]; RFC 5424 severities [R14] | Event schema §5.3 with mapped severities |
| S5 | Alert rules | Prometheus alerting rules [R7]; rule unit tests [R8] | All rules in Prometheus format, `promtool`-tested in CI |
| S6 | Notification | Alertmanager [R9] | Grouping, deduplication, inhibition, silences, resolve notices |
| S7 | What to measure | Google SRE "four golden signals" [R11]; RED [R13]; USE [R12] | O1: USE for hosts and freshness for jobs; O4: RED for request paths |
| S8 | Alert design | Prometheus alerting practice [R10]; SRE workbook [R11b] | Symptom-based; page only on actionable items; a runbook per alert |
| S9 | Self-monitoring | Dead-man's switch / Watchdog pattern [R9][R10] | Always-firing Watchdog to an external heartbeat service |
| S10 | Log security | OWASP Logging Cheat Sheet [R15]; NIST SP 800-92 [R16] | No sensitive data; integrity; retention defined |
| S11 | Container hardening | CIS Docker Benchmark [R17] | Non-root, read-only rootfs, capabilities dropped, limits, pinned digests |
| S12 | Trace propagation (O4) | W3C Trace Context [R18] | `traceparent` across services |
| S13 | Fleet management (future) | OpAMP [R19] | Optional remote Collector configuration |
| S14 | Change control | Monitoring as code | Rules, routing and dashboards in git; CI validation (§12) |

## 4. Principles and how they are met

| # | Principle | How it is met |
|---|---|---|
| P1 | **Facts in code, judgement in config** | The library has no threshold parameter. Code emits raw values. Per-job schedule facts (expected interval, missed-after) are job *configuration* in `config/scheduled_jobs.json` and are exported as metrics, so one generic rule covers every job. |
| P2 | **One vendor-neutral path out** | Components only know `OTEL_EXPORTER_OTLP_ENDPOINT`. |
| P3 | **Never a product dependency** | Background export with a bounded queue. Drops are counted, never blocking. A local copy is written first. Telemetry off means a no-op. |
| P4 | **Privacy** | Closed vocabularies are validated in the library. A Collector OTTL `keep_keys` allowlist gives defence in depth. Tests show text, emails, paths and tokens are refused. |
| P5 | **Security** | Private network only. TLS and token across hosts. Secrets as files. Hardened containers (§13). |
| P6 | **Actionable alerts** | Each rule carries `severity`, `summary`, `runbook_url`, `for:` and `minimoi_alert_key`. Warnings go to dashboards; only `critical` notifies. Resolved notices are sent. |
| P7 | **Self-monitoring** | Watchdog to an external dead-man's switch, plus heartbeat staleness and pipeline-failure rules. |
| P8 | **Bounded cardinality** | Label sets are fixed. Prometheus `sample_limit` and `label_limit` per scrape. An alert fires on limit hits. |
| P9 | **Versioned contracts** | `minimoi.telemetry.schema_version`. Renames dual-run for one release. |
| P10 | **Tested end to end** | Library unit tests, `promtool`, `amtool`, a pipeline test, failure injection (§11). |

## 5. Signals and conventions

### 5.1 Resource attributes (every signal)

| Attribute | Value |
|---|---|
| `service.name` | `minimoi-<component>`: `minimoi-portal`, `minimoi-curator`, `minimoi-german`, `minimoi-portuguese`, `minimoi-cos-scheduler`, `minimoi-model-gateway`, `minimoi-standing-checks`, `minimoi-observability-bridge`, `minimoi-mac-jobs` |
| `service.version` | git sha (12 characters) or image tag |
| `deployment.environment.name` | `dev` or `prod` |
| `host.name` | `ec2-prod`, `mac-dev`, … |
| `minimoi.telemetry.schema_version` | `1` |

### 5.2 Metric catalogue, O1

| Metric | Type | Unit | Labels | Source |
|---|---|---|---|---|
| `minimoi_job_last_success_timestamp_seconds` | gauge | s | `job_id` | bridge (job status) |
| `minimoi_job_last_finish_timestamp_seconds` | gauge | s | `job_id` | bridge |
| `minimoi_job_last_exit_code` | gauge | 1 | `job_id` | bridge |
| `minimoi_job_running` | gauge | 1 | `job_id` | bridge |
| `minimoi_job_status_readable` | gauge | 1 | `job_id` | bridge (status file present and valid) |
| `minimoi_job_expected_interval_seconds` | gauge | s | `job_id` | bridge (job registry) |
| `minimoi_job_missed_after_seconds` | gauge | s | `job_id` | bridge (job registry) |
| `minimoi_job_stuck_after_seconds` | gauge | s | `job_id` | bridge (job registry) |
| `minimoi_service_up` | gauge | 1 | `service` | bridge (health snapshot) |
| `minimoi_host_filesystem_used_ratio` | gauge | 1 | `mountpoint` | bridge (host snapshot) |
| `minimoi_host_memory_used_ratio` | gauge | 1 | — | bridge (host snapshot) |
| `minimoi_deploy_last_timestamp_seconds` | gauge | s | — | bridge (release status) |
| `minimoi_deploy_last_outcome` | gauge | 1 (0 ok, 1 failed, 2 in progress) | — | bridge |
| `minimoi_guest_requests_pending` | gauge | 1 | — | standing checks (count) |
| `minimoi_guest_requests_oldest_age_seconds` | gauge | s | — | standing checks |
| `minimoi_check_source_readable` | gauge | 1 | `check_id` | standing checks |
| `minimoi_check_last_run_timestamp_seconds` | gauge | s | `check_id` | standing checks |
| `minimoi_telemetry_heartbeat_timestamp_seconds` | gauge | s | — | every emitter (via library) |
| `minimoi_telemetry_dropped_total` | counter | 1 | `signal` | library |

**Estimated series per environment:** about 30 jobs × 8, 10 services, 3 mountpoints, 7 checks × 2, and singletons, so **≈ 280**. The cap is 2,000.

### 5.3 Event schema `minimoi.event/1` (OTel log records)

| Field | Type | Notes |
|---|---|---|
| `event.name` | string | for example `minimoi.job.failed` |
| `minimoi.alert_key` | string | stable deduplication key, `<kind>:<id>`, for example `job:backup-s3` |
| `minimoi.state` | enum | `opened`, `cleared` or `info` |
| `severity_number` | int | per §5.4 |
| `minimoi.component` | enum | from the vocabulary |
| `minimoi.summary_code` | enum | from the vocabulary, for example `exit_nonzero`, `source_unreadable` |
| `observed_time` | timestamp | UTC |

**Code emits events only for facts that are binary by nature:**
- a job exited non-zero;
- a source could not be read;
- a deploy failed;
- a component restarted.

Threshold conditions are alerts from rules, not events from code.

### 5.4 Severity mapping

| MiniMoi | OTel SeverityNumber | RFC 5424 | Netcool OMNIbus | Splunk ITSI |
|---|---|---|---|---|
| clear | 9 INFO | 6 info | 0 Clear | 2 Normal |
| warning | 13 WARN | 4 warning | 2 Warning | 3 Low |
| major | 17 ERROR | 3 err | 4 Major | 5 High |
| critical | 21 FATAL | 2 crit | 5 Critical | 6 Critical |

### 5.5 Vocabulary file

`config/observability/vocabulary.yaml` is the single registry of the allowed values:
- service names;
- label keys and their allowed values or patterns;
- components;
- summary codes.

The library and CI both validate against it.

```yaml
schema: minimoi-telemetry-vocabulary/1
services: [minimoi-portal, minimoi-curator, minimoi-german, minimoi-portuguese, minimoi-cos-scheduler,
           minimoi-model-gateway, minimoi-standing-checks, minimoi-observability-bridge, minimoi-mac-jobs]
labels:
  job_id:     {pattern: "^[a-z0-9][a-z0-9-]{0,47}$", source: config/scheduled_jobs.json}
  service:    {values: [portal, curator, german, portuguese, cos-scheduler, cos-agent-a, model-gateway, postgres, nginx]}
  mountpoint: {values: ["/", "/opt/minimoi", "/var/lib/docker"]}
  check_id:   {pattern: "^[a-z0-9][a-z0-9-]{0,39}$", source: config/mc_standing/watchlist.json}
  signal:     {values: [metrics, logs, traces]}
components:   [jobs, services, host, deploy, guests, standing, telemetry]
summary_codes: [exit_nonzero, source_unreadable, deploy_failed, component_restarted, heartbeat]
```

### 5.6 Traces (O4, conventions fixed now)

- **Propagation:** W3C `traceparent` through portal → service → gateway → runner.
- **Content:** spans carry IDs and timings only.
- **Sampling:** 10% head, plus 100% of errors (tail sampling in the Collector).

## 6. Architecture

```
            ┌───────────── components (core/observability library) ──────────────┐
            │ local copy first (status files, JSON lines) ─▶ Operate tile, MC     │
            └──── OTLP/HTTP :4318 (private network "observability") ─────────────┘
                                         │
   status files ─▶ observability-bridge ─┤
                                         ▼
                     ┌──────── OpenTelemetry Collector (contrib 0.162.0) ────────┐
                     │ receivers: otlp, filelog(events)                           │
                     │ processors: memory_limiter, transform(allowlist),          │
                     │             resource(env), batch                           │
                     │ exporters: prometheus(:8889), file(rotated) [+ §16 later]  │
                     │ extensions: health_check(:13133), file_storage              │
                     └───────────┬───────────────────────────────────────────────┘
                                 ▼ scrape (sample_limit 2000)
                     Prometheus 3.15.0 ── rules/*.yaml ──▶ Alertmanager 0.34.1
                                                                ├─▶ Telegram (critical, resolved)
                                                                └─▶ dead-man's switch (Watchdog)
```

**Everything runs in one compose overlay,** `docker-compose.observability.yml`, on a private network. **No port is published to the internet;** dev binds the UIs to `127.0.0.1` only.

## 7. The `core/observability` library

### 7.1 Layout

```
core/observability/
  __init__.py       # public API below
  vocabulary.py     # loads/validates config/observability/vocabulary.yaml (cached)
  emitter.py        # OTel SDK setup, bounded export, local copy, drop counter, heartbeat thread
  bridge.py         # status-file bridge (§8), run as a service
  cli.py            # minimoi-telemetry CLI for shell jobs
```

### 7.2 Public API (Python)

```python
from core.observability import gauge, count, event, heartbeat, configure

configure(service_name="minimoi-curator")          # once at start; reads env (§7.4)
gauge("minimoi_job_last_success_timestamp_seconds", 1761036000, job_id="curator-daily")
count("minimoi_telemetry_dropped_total", 1, signal="metrics")
event(alert_key="job:curator-daily", state="opened", severity="major",
      summary_code="exit_nonzero", component="jobs")
heartbeat()                                          # also automatic every 60 s once configured
```

**Contract:**
- **Validation:** names, label keys and label values are validated against the vocabulary. An invalid call raises `TelemetryContractError` **in tests and dev**, and is dropped and counted **in prod**, so a telemetry bug can never break a product path.
- **No threshold arguments exist.**
- **Return value:** none. Calls are non-blocking.

### 7.3 Non-blocking export and local copy

- **SDK:** OTel Python SDK 1.45.1 (`opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`).
- **Metrics:** a `PeriodicExportingMetricReader` (60 s) over OTLP/HTTP.
- **Logs:** a `BatchLogRecordProcessor` with `max_queue_size=2048`, `max_export_batch_size=512`, `export_timeout_millis=5000`. Overflow increments `minimoi_telemetry_dropped_total`.
- **Local copy, written before export:**
  - events are appended as JSON lines to `$MINIMOI_TELEMETRY_LOCAL_DIR/events-YYYYMMDD.jsonl` (0600, rotated daily, 14 days kept);
  - the latest gauges go to `metrics-latest.json`;
  - a local-copy failure is counted, never raised.

### 7.4 Configuration (environment)

| Variable | Default | Meaning |
|---|---|---|
| `MINIMOI_TELEMETRY_ENABLED` | `0` | `1` turns export on; off is a no-op except the local copy |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://otel-collector:4318` | standard OTel variable |
| `OTEL_EXPORTER_OTLP_HEADERS_FILE` | unset | path to a file containing `Authorization=Bearer …` (cross-host only) |
| `MINIMOI_TELEMETRY_LOCAL_DIR` | `/var/lib/minimoi/telemetry` | local copy |
| `MINIMOI_ENV` | required | `dev` or `prod` |

### 7.5 CLI for shell jobs

```bash
minimoi-telemetry gauge minimoi_job_last_success_timestamp_seconds "$(date +%s)" --label job_id=backup-local
minimoi-telemetry event --key job:backup-local --state opened --severity major --summary exit_nonzero --component jobs
```

The CLI uses the same validation, local copy and exporter. Its exit code is always 0, so a monitoring failure never fails the job.

## 8. The status-file bridge

**Why:** existing jobs already write the job status contract (`core/jobs/status.py`) and the registry (`config/scheduled_jobs.json`). The bridge turns them into metrics, so **no existing job changes** in O1.

**What it does:**
- **Runs** `python -m core.observability.bridge` every **60 s**, as its own small service.
- **Reads:**
  - the registry;
  - each job's status file, through the existing `read_status`, where unknown never means healthy;
  - the health snapshot `jobs/health/services.json`;
  - the host snapshot `jobs/health/host.json`;
  - the release status.
- **Emits** the §5.2 metrics. `minimoi_job_status_readable=0` means missing or invalid, never healthy.
- **Mounts:** read-only on every source. It writes nothing except its own local copy.

**Mac jobs:**
- **O1:** monitored on the Mac dev stack.
- **O2:** a Mac-side bridge forwards over TLS with a token to the EC2 Collector (decision D5, §17).

## 9. Alert rules (O1 set) and tests

### 9.1 `config/observability/rules/minimoi_o1.yaml`

```yaml
groups:
- name: minimoi-jobs
  rules:
  - alert: JobMissed
    expr: (time() - minimoi_job_last_success_timestamp_seconds) > minimoi_job_missed_after_seconds
    for: 10m
    labels: {severity: critical, minimoi_alert_key: 'job:{{ $labels.job_id }}:missed'}
    annotations:
      summary: 'Job {{ $labels.job_id }} has not succeeded within its missed-after window'
      runbook_url: 'docs/runbooks/job-missed.md'
  - alert: JobStuck
    expr: minimoi_job_running == 1 and (time() - minimoi_job_last_finish_timestamp_seconds) > minimoi_job_stuck_after_seconds
    for: 5m
    labels: {severity: major, minimoi_alert_key: 'job:{{ $labels.job_id }}:stuck'}
    annotations: {summary: 'Job {{ $labels.job_id }} has been running too long', runbook_url: 'docs/runbooks/job-stuck.md'}
  - alert: JobFailed
    expr: minimoi_job_last_exit_code != 0
    for: 1m
    labels: {severity: major, minimoi_alert_key: 'job:{{ $labels.job_id }}:failed'}
    annotations: {summary: 'Job {{ $labels.job_id }} last run exited non-zero', runbook_url: 'docs/runbooks/job-failed.md'}
  - alert: JobStatusUnreadable
    expr: minimoi_job_status_readable == 0
    for: 30m
    labels: {severity: warning, minimoi_alert_key: 'job:{{ $labels.job_id }}:unreadable'}
    annotations: {summary: 'Status of {{ $labels.job_id }} cannot be read (unknown, not healthy)', runbook_url: 'docs/runbooks/job-status-unreadable.md'}
- name: minimoi-services-host
  rules:
  - alert: ServiceDown
    expr: minimoi_service_up == 0
    for: 5m
    labels: {severity: critical, minimoi_alert_key: 'service:{{ $labels.service }}:down'}
    annotations: {summary: 'Service {{ $labels.service }} is down', runbook_url: 'docs/runbooks/service-down.md'}
  - alert: DiskCritical
    expr: minimoi_host_filesystem_used_ratio >= 0.95
    for: 10m
    labels: {severity: critical, minimoi_alert_key: 'host:disk:{{ $labels.mountpoint }}'}
    annotations: {summary: 'Disk {{ $labels.mountpoint }} at {{ $value | humanizePercentage }}', runbook_url: 'docs/runbooks/disk.md'}
  - alert: DiskHigh
    expr: minimoi_host_filesystem_used_ratio >= 0.80
    for: 30m
    labels: {severity: warning, minimoi_alert_key: 'host:disk:{{ $labels.mountpoint }}'}
    annotations: {summary: 'Disk {{ $labels.mountpoint }} at {{ $value | humanizePercentage }}', runbook_url: 'docs/runbooks/disk.md'}
  - alert: MemoryCritical
    expr: minimoi_host_memory_used_ratio >= 0.95
    for: 10m
    labels: {severity: critical, minimoi_alert_key: 'host:memory'}
    annotations: {summary: 'Memory at {{ $value | humanizePercentage }}', runbook_url: 'docs/runbooks/memory.md'}
  - alert: MemoryHigh
    expr: minimoi_host_memory_used_ratio >= 0.85
    for: 30m
    labels: {severity: warning, minimoi_alert_key: 'host:memory'}
    annotations: {summary: 'Memory at {{ $value | humanizePercentage }}', runbook_url: 'docs/runbooks/memory.md'}
  - alert: DeployFailed
    expr: minimoi_deploy_last_outcome == 1
    for: 5m
    labels: {severity: major, minimoi_alert_key: 'deploy:last'}
    annotations: {summary: 'The last deployment failed', runbook_url: 'docs/runbooks/deploy-failed.md'}
- name: minimoi-guests-standing
  rules:
  - alert: GuestRequestWaiting
    expr: minimoi_guest_requests_oldest_age_seconds > 172800
    for: 1h
    labels: {severity: warning, minimoi_alert_key: 'guests:waiting'}
    annotations: {summary: '{{ with query "minimoi_guest_requests_pending" }}{{ . | first | value }}{{ end }} guest request(s), oldest over 48 h', runbook_url: 'docs/runbooks/guest-waiting.md'}
  - alert: CheckSourceUnreadable
    expr: minimoi_check_source_readable == 0
    for: 30m
    labels: {severity: warning, minimoi_alert_key: 'check:{{ $labels.check_id }}:unreadable'}
    annotations: {summary: 'Check {{ $labels.check_id }} cannot read its source', runbook_url: 'docs/runbooks/check-unreadable.md'}
- name: minimoi-self
  rules:
  - alert: Watchdog
    expr: vector(1)
    labels: {severity: none, minimoi_alert_key: 'self:watchdog'}
    annotations: {summary: 'Always firing; its absence means the alert pipeline is broken', runbook_url: 'docs/runbooks/watchdog.md'}
  - alert: TelemetryHeartbeatStale
    expr: (time() - max by (service_name) (minimoi_telemetry_heartbeat_timestamp_seconds)) > 900
    for: 5m
    labels: {severity: critical, minimoi_alert_key: 'self:heartbeat:{{ $labels.service_name }}'}
    annotations: {summary: 'No telemetry heartbeat from {{ $labels.service_name }} for 15 min', runbook_url: 'docs/runbooks/heartbeat.md'}
  - alert: TelemetryDropping
    expr: increase(minimoi_telemetry_dropped_total[15m]) > 0
    labels: {severity: warning, minimoi_alert_key: 'self:dropping'}
    annotations: {summary: 'Telemetry is being dropped', runbook_url: 'docs/runbooks/telemetry-dropping.md'}
  - alert: CollectorExportFailing
    expr: sum(rate(otelcol_exporter_send_failed_metric_points_total[10m])) + sum(rate(otelcol_exporter_send_failed_log_records_total[10m])) > 0
    for: 10m
    labels: {severity: major, minimoi_alert_key: 'self:collector-export'}
    annotations: {summary: 'The Collector cannot export', runbook_url: 'docs/runbooks/collector.md'}
  - alert: ScrapeLimitHit
    expr: prometheus_target_scrapes_exceeded_sample_limit_total > 0
    labels: {severity: warning, minimoi_alert_key: 'self:cardinality'}
    annotations: {summary: 'Series cap hit; new labels are being dropped', runbook_url: 'docs/runbooks/cardinality.md'}
```

**Where the threshold values come from:**
- Robert's approved MC watchlist (20 Oct): disk 80/95%, memory 85/95%, guest wait 48 h.
- The job registry's own windows.

**Robert confirms them again in D2.**

### 9.2 Rule tests: `config/observability/rules/tests/minimoi_o1_test.yaml` (excerpt)

```yaml
rule_files: [../minimoi_o1.yaml]
evaluation_interval: 1m
tests:
- interval: 1m
  input_series:
  - series: 'minimoi_host_filesystem_used_ratio{mountpoint="/"}'
    values: '0.90x20 0.96x20'
  alert_rule_test:
  - eval_time: 15m
    alertname: DiskCritical
    exp_alerts: []
  - eval_time: 35m
    alertname: DiskCritical
    exp_alerts:
    - exp_labels: {severity: critical, mountpoint: '/', minimoi_alert_key: 'host:disk:/'}
      exp_annotations: {summary: 'Disk / at 96%', runbook_url: 'docs/runbooks/disk.md'}
- interval: 1m
  input_series:
  - series: 'minimoi_job_last_success_timestamp_seconds{job_id="backup-s3"}'
    values: '0x200'
  - series: 'minimoi_job_missed_after_seconds{job_id="backup-s3"}'
    values: '3600x200'
  alert_rule_test:
  - eval_time: 75m
    alertname: JobMissed
    exp_alerts:
    - exp_labels: {severity: critical, job_id: backup-s3, minimoi_alert_key: 'job:backup-s3:missed'}
```

**Every rule has at least one firing case and one non-firing case.** CI fails otherwise (a coverage script compares rule names with tested names).

### 9.3 `config/observability/alertmanager.yml`

```yaml
global:
  resolve_timeout: 5m
route:
  receiver: telegram-critical
  group_by: [minimoi_alert_key]
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 12h
  routes:
  - matchers: ['alertname="Watchdog"']
    receiver: deadman
    group_wait: 0s
    group_interval: 1m
    repeat_interval: 1m
  - matchers: ['severity=~"warning|none"']
    receiver: blackhole            # dashboards only
  - matchers: ['severity=~"critical|major"']
    receiver: telegram-critical
inhibit_rules:
- source_matchers: ['severity="critical"']
  target_matchers: ['severity="warning"']
  equal: [minimoi_alert_key]
receivers:
- name: blackhole
- name: telegram-critical
  telegram_configs:
  - bot_token_file: /run/secrets/am_telegram_bot_token
    chat_id: ${TELEGRAM_CHAT_ID}            # substituted at deploy from a private file; never committed
    send_resolved: true
    parse_mode: HTML
    message: '{{ template "minimoi.telegram" . }}'
- name: deadman
  webhook_configs:
  - url_file: /run/secrets/deadman_ping_url
    send_resolved: false
templates: ['/etc/alertmanager/templates/*.tmpl']
```

**The Telegram template** prints only:
- severity;
- `summary`;
- `minimoi_alert_key`;
- the time;
- the runbook link.

It never prints label values beyond the vocabulary.

## 10. Installation

### 10.1 Pinned versions

**Pin by digest at install time** and record the digests in `config/observability/VERSIONS.md`.

| Component | Version | Released | Image |
|---|---|---|---|
| OpenTelemetry Collector (contrib) | 0.162.0 | 2026-09-29 | `otel/opentelemetry-collector-contrib:0.162.0@sha256:<record>` |
| Prometheus | 3.15.0 | 2026-09-25 | `prom/prometheus:v3.15.0@sha256:<record>` |
| Alertmanager | 0.34.1 | 2026-09-17 | `prom/alertmanager:v0.34.1@sha256:<record>` |
| OTel Python SDK and OTLP/HTTP exporter | 1.45.1 | 2026-10-06 | pip pins in `requirements` |
| OTel Semantic Conventions | 1.44.0 | 2026-08-04 | naming reference |

**Each version is at least two weeks old at writing,** and is the current stable line. For example, the Collector's v0.163.x was four days old, so 0.162.0 was chosen.

**Recording a digest:**

```bash
docker buildx imagetools inspect otel/opentelemetry-collector-contrib:0.162.0 --format '{{json .Manifest.Digest}}'
```

### 10.2 Repository layout added

```
config/observability/
  vocabulary.yaml  collector.yaml  prometheus.yml  alertmanager.yml  VERSIONS.md
  rules/minimoi_o1.yaml  rules/tests/minimoi_o1_test.yaml
  templates/telegram.tmpl  dashboards/ (optional in O1)
docker-compose.observability.yml
core/observability/   (§7)
docs/runbooks/<alert>.md   (one per rule)
.github/workflows/observability-config.yml   (§12.2)
```

### 10.3 `config/observability/collector.yaml`

```yaml
extensions:
  health_check: {endpoint: 0.0.0.0:13133}
  file_storage: {directory: /var/lib/otelcol/queue, timeout: 2s}
receivers:
  otlp:
    protocols:
      http: {endpoint: 0.0.0.0:4318}
  filelog/events:
    include: [/var/lib/minimoi/telemetry/events-*.jsonl]
    start_at: end
    storage: file_storage
    operators: [{type: json_parser}]
processors:
  memory_limiter: {check_interval: 1s, limit_mib: 200, spike_limit_mib: 50}
  resource/env:
    attributes:
    - {key: deployment.environment.name, value: ${env:MINIMOI_ENV}, action: upsert}
  transform/allowlist:
    metric_statements:
    - context: datapoint
      statements: ['keep_keys(attributes, ["job_id","service","mountpoint","check_id","signal"])']
    log_statements:
    - context: log
      statements: ['keep_keys(attributes, ["event.name","minimoi.alert_key","minimoi.state","minimoi.component","minimoi.summary_code"])']
  batch: {timeout: 5s, send_batch_size: 1024}
exporters:
  prometheus: {endpoint: 0.0.0.0:8889, resource_to_telemetry_conversion: {enabled: true}}
  file/archive:
    path: /var/lib/otelcol/archive/telemetry.jsonl
    rotation: {max_megabytes: 50, max_days: 14, max_backups: 10}
service:
  extensions: [health_check, file_storage]
  telemetry: {metrics: {level: normal}}
  pipelines:
    metrics: {receivers: [otlp], processors: [memory_limiter, resource/env, transform/allowlist, batch], exporters: [prometheus, file/archive]}
    logs:    {receivers: [otlp, filelog/events], processors: [memory_limiter, resource/env, transform/allowlist, batch], exporters: [file/archive]}
```

**Implementer's check:** confirm each component name and option against the 0.162.0 component docs, and run `otelcol-contrib validate --config` in CI. Component configs evolve between Collector minor releases.

### 10.4 `config/observability/prometheus.yml`

```yaml
global:
  scrape_interval: 30s
  evaluation_interval: 30s
  external_labels: {environment: '${MINIMOI_ENV}'}
rule_files: [/etc/prometheus/rules/*.yaml]
alerting:
  alertmanagers: [{static_configs: [{targets: ['alertmanager:9093']}]}]
scrape_configs:
- job_name: otel-collector
  sample_limit: 2000
  label_limit: 12
  static_configs: [{targets: ['otel-collector:8889']}]
- job_name: otel-collector-self
  static_configs: [{targets: ['otel-collector:8888']}]
- job_name: prometheus
  static_configs: [{targets: ['localhost:9090']}]
- job_name: alertmanager
  static_configs: [{targets: ['alertmanager:9093']}]
```

**Run flags:**
- `--storage.tsdb.retention.time=15d`;
- `--storage.tsdb.retention.size=2GB`;
- `--web.enable-lifecycle` (for config reload over the private network only).

### 10.5 `docker-compose.observability.yml`

```yaml
networks:
  observability: {internal: true}
services:
  otel-collector:
    image: ${OTELCOL_IMAGE:?pinned image@digest}
    command: ["--config=/etc/otelcol/collector.yaml"]
    environment: [MINIMOI_ENV=${MINIMOI_ENV:?}]
    volumes:
      - ./config/observability/collector.yaml:/etc/otelcol/collector.yaml:ro
      - ${MINIMOI_ROOT:?}/data/observability/otelcol:/var/lib/otelcol
      - ${MINIMOI_ROOT:?}/data/telemetry:/var/lib/minimoi/telemetry:ro
    networks: [observability, default]
    user: "10001:10001"
    read_only: true
    cap_drop: [ALL]
    security_opt: [no-new-privileges:true]
    mem_limit: 256m
    cpus: 0.25
    healthcheck: {test: ["CMD", "/otelcol-contrib", "--version"], interval: 60s}
    restart: unless-stopped
  prometheus:
    image: ${PROMETHEUS_IMAGE:?}
    command: ["--config.file=/etc/prometheus/prometheus.yml", "--storage.tsdb.path=/prometheus",
              "--storage.tsdb.retention.time=15d", "--storage.tsdb.retention.size=2GB", "--web.enable-lifecycle"]
    volumes:
      - ./config/observability/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - ./config/observability/rules:/etc/prometheus/rules:ro
      - ${MINIMOI_ROOT:?}/data/observability/prometheus:/prometheus
    networks: [observability]
    ports: ["127.0.0.1:9090:9090"]          # dev only; omit in prod (use SSH tunnel)
    user: "65534:65534"
    read_only: true
    cap_drop: [ALL]
    security_opt: [no-new-privileges:true]
    mem_limit: 512m
    restart: unless-stopped
  alertmanager:
    image: ${ALERTMANAGER_IMAGE:?}
    command: ["--config.file=/etc/alertmanager/alertmanager.yml", "--storage.path=/alertmanager"]
    volumes:
      - ${MINIMOI_ROOT:?}/config/observability/alertmanager.rendered.yml:/etc/alertmanager/alertmanager.yml:ro
      - ./config/observability/templates:/etc/alertmanager/templates:ro
      - ${MINIMOI_ROOT:?}/data/observability/alertmanager:/alertmanager
    secrets: [am_telegram_bot_token, deadman_ping_url]
    networks: [observability, default]       # default: outbound to Telegram/dead-man only
    user: "65534:65534"
    read_only: true
    cap_drop: [ALL]
    security_opt: [no-new-privileges:true]
    mem_limit: 128m
    restart: unless-stopped
  observability-bridge:
    image: ${MINIMOI_APP_IMAGE:?image containing core/observability}
    command: ["python", "-m", "core.observability.bridge", "--interval", "60"]
    environment:
      - MINIMOI_ENV=${MINIMOI_ENV:?}
      - MINIMOI_TELEMETRY_ENABLED=1
      - OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318
    volumes:
      - ./config/scheduled_jobs.json:/app/config/scheduled_jobs.json:ro
      - ${MINIMOI_ROOT:?}/data/jobs:/app/data/jobs:ro
      - ${MINIMOI_ROOT:?}/data/telemetry:/var/lib/minimoi/telemetry
    networks: [observability]
    read_only: true
    cap_drop: [ALL]
    security_opt: [no-new-privileges:true]
    mem_limit: 128m
    restart: unless-stopped
secrets:
  am_telegram_bot_token: {file: ${MINIMOI_ROOT:?}/secrets/am_telegram_bot_token}
  deadman_ping_url: {file: ${MINIMOI_ROOT:?}/secrets/deadman_ping_url}
```

**Resource budget:** 1.0 GB memory and 0.6 CPU at most, all four services together. This is checked against EC2 headroom before O2.

### 10.6 Install steps

**Dev first (Mac, Colima), then prod (EC2).** Each step is a command and an expected result.

1. **Prerequisites.**
   - Docker Compose v2.
   - About 3 GB free disk for the observability data.
   - The host clock synced with NTP: `timedatectl` on EC2, `sntp -t 1 time.apple.com` on the Mac.
2. **Directories** (owner-only):

   ```bash
   sudo install -d -m 0700 -o 10001 -g 10001 $MINIMOI_ROOT/data/observability/otelcol
   sudo install -d -m 0700 -o 65534 -g 65534 $MINIMOI_ROOT/data/observability/prometheus
   sudo install -d -m 0700 -o 65534 -g 65534 $MINIMOI_ROOT/data/observability/alertmanager
   sudo install -d -m 0750 $MINIMOI_ROOT/data/telemetry
   ```

3. **Secrets.** Robert types the values; agents never see them. The Telegram bot token needs a dedicated alert bot, separate from the CoS bots.

   ```bash
   install -m 0600 /dev/null $MINIMOI_ROOT/secrets/am_telegram_bot_token && $EDITOR $MINIMOI_ROOT/secrets/am_telegram_bot_token
   ```

   Do the same for `deadman_ping_url`, from the dead-man's-switch service (D3). On prod the source is SSM `/minimoi/production/observability/*`.
4. **Render the Alertmanager config.** This substitutes the chat ID from a private file and validates the result:

   ```bash
   envsubst < config/observability/alertmanager.yml > $MINIMOI_ROOT/config/observability/alertmanager.rendered.yml
   docker run --rm -v $MINIMOI_ROOT/config/observability:/c ${ALERTMANAGER_IMAGE} amtool check-config /c/alertmanager.rendered.yml
   ```

5. **Validate everything:**

   ```bash
   docker run --rm -v $PWD/config/observability:/c --entrypoint promtool ${PROMETHEUS_IMAGE} check rules /c/rules/minimoi_o1.yaml
   docker run --rm -v $PWD/config/observability:/c --entrypoint promtool ${PROMETHEUS_IMAGE} test rules /c/rules/tests/minimoi_o1_test.yaml
   docker run --rm -v $PWD/config/observability:/c ${OTELCOL_IMAGE} validate --config=/c/collector.yaml
   ```

6. **Start the stack:**

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.observability.yml up -d otel-collector prometheus alertmanager observability-bridge
   ```

7. **Enable emitters, one component at a time:** set `MINIMOI_TELEMETRY_ENABLED=1` in that service's environment and restart only that service.
8. **Run §11 in full,** recording the results in `docs/specs/reviews/spec_161/install_evidence_<env>_<date>.md`.

## 11. Verification (acceptance tests)

All steps must pass on dev, then on prod. The evidence is recorded.

| # | Test | Expected |
|---|---|---|
| V1 | `curl -s otel-collector:13133` (from inside the network) | `{"status":"Server available"…}` |
| V2 | Prometheus targets | `otel-collector`, `otel-collector-self`, `prometheus` and `alertmanager` all UP |
| V3 | Bridge metrics present | `minimoi_job_last_success_timestamp_seconds` has one series per registered job |
| V4 | `promtool test rules` | PASS |
| V5 | **Synthetic job failure:** a test job's status file is set to `failed` (dev only) | `JobFailed` fires in ≤ 3 min; Telegram arrives with the runbook link; resolved arrives after restore |
| V6 | **Disk rule:** inject `minimoi_host_filesystem_used_ratio{mountpoint="/"} 0.96` through the CLI on dev | `DiskCritical` fires after 10 min; the `DiskHigh` warning is inhibited |
| V7 | **Dead-man's switch:** stop Alertmanager for 10 min | The external service alerts Robert; it recovers when restarted |
| V8 | **Collector down:** stop the Collector | Product endpoints are unaffected (latency unchanged); the local copy grows; `TelemetryHeartbeatStale` fires after restart catch-up, or the dead-man's switch fires |
| V9 | **Privacy:** call `event(...)` with a free-text label or an email | Refused in dev (exception); dropped and counted in prod; nothing in the archive file |
| V10 | **Cardinality:** emit 2,500 synthetic series on dev | `ScrapeLimitHit` fires; no crash |
| V11 | **Restart persistence:** restart the whole stack | Prometheus history kept; no duplicate notifications for already-notified alerts |
| V12 | **Resource budget** | `docker stats` shows ≤ 1 GB in total |

## 12. Operations

### 12.1 Change control

**Every threshold, route or rule change is a pull request** to `config/observability/`, reviewed and validated by CI. Deploying it means a config reload:

```bash
curl -X POST http://prometheus:9090/-/reload
curl -X POST http://alertmanager:9093/-/reload
```

**No application image is rebuilt.** If a GUI tool is adopted later, git stays the source of truth (§16.6).

### 12.2 CI: `.github/workflows/observability-config.yml`

```yaml
name: observability-config
on:
  pull_request: {paths: ['config/observability/**', 'core/observability/**', 'docs/runbooks/**']}
  push: {branches: [main], paths: ['config/observability/**']}
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v4
    - run: docker run --rm -v $PWD/config/observability:/c --entrypoint promtool prom/prometheus:v3.15.0 check rules /c/rules/minimoi_o1.yaml
    - run: docker run --rm -v $PWD/config/observability:/c --entrypoint promtool prom/prometheus:v3.15.0 test rules /c/rules/tests/minimoi_o1_test.yaml
    - run: docker run --rm -v $PWD/config/observability:/c --entrypoint amtool prom/alertmanager:v0.34.1 check-config /c/alertmanager.yml
    - run: docker run --rm -v $PWD/config/observability:/c otel/opentelemetry-collector-contrib:0.162.0 validate --config=/c/collector.yaml
    - run: python scripts/observability/check_rules_meta.py   # every rule: severity, key, runbook exists, has tests; labels in vocabulary
```

**In CI, the image tags are replaced by the pinned digests** recorded in `VERSIONS.md`.

### 12.3 Upgrades

1. Read the upstream changelog for breaking changes.
2. Bump the version and digest in `VERSIONS.md` and compose.
3. CI validates.
4. Deploy to dev and run V1–V6.
5. Deploy to prod in a quiet window.
6. Keep the previous digest for rollback.

**Cadence:** quarterly, or for security fixes.

### 12.4 Rollback

**Configuration:** revert the PR and reload.

**Stack:** `docker compose -f … -f docker-compose.observability.yml stop <services>`. The product is unaffected (P3), because emitters drop silently and the local copies continue.

**Removal:** stop the services and leave the data directories in place for audit. Nothing in the product depends on them.

### 12.5 Parallel run and retiring old alerts

**Existing alerts keep running in parallel:**
- the Curator ledger;
- the guest and health nudges;
- the Mac jobs watchdog.

**Retiring one:**
1. For each condition, keep both until 14 days show parity: the same incidents, no misses.
2. Retire one condition at a time, with Robert's approval.
3. Record each retirement in `docs/specs/reviews/spec_161/`.

### 12.6 Retention and backup

| Data | Retention |
|---|---|
| Prometheus | 15 days or 2 GB |
| Collector archive | 14 days / 500 MB |
| Library local copy | 14 days |

**Backups:** these are operational data, not records of truth, so they are **not backed up**. The rules and config that define them are in git.

### 12.7 Runbooks

`docs/runbooks/<name>.md` covers every rule's `runbook_url`. Each runbook has:
- what the alert means;
- the likely causes;
- the checks (read-only first);
- the safe fixes;
- when to escalate;
- how to verify the clear.

## 13. Security

- **Network:**
  - OTLP, Prometheus and Alertmanager are only on the internal `observability` network.
  - Nothing is routed through nginx.
  - Dev UIs bind to `127.0.0.1`; prod UIs are reached only through an SSH tunnel.
- **Cross-host (O2 Mac → EC2):**
  - an OTLP/HTTP endpoint behind TLS, reached only through a dedicated nginx location with a per-sender bearer token held in a 0600 file;
  - a rate limit and a body-size limit;
  - denied by default.
- **Secrets:**
  - the Telegram token and dead-man URL are Docker secrets from 0600 files (SSM on prod);
  - they are never in compose, env files committed to git, or logs.
- **Containers:**
  - pinned digests, non-root UIDs, `read_only`, `cap_drop: [ALL]`, `no-new-privileges`;
  - memory and CPU limits;
  - CIS Docker Benchmark items 4.1, 5.3, 5.10–5.12, 5.25 addressed.
- **Data:**
  - closed vocabularies in the library;
  - an OTTL allowlist in the Collector;
  - the Telegram template prints only safe fields;
  - per OWASP and NIST SP 800-92, no message text, names, emails, paths, prompts or tokens.
- **Supply chain:** official images only, pinned by digest, with digests recorded in git.

## 14. Failure modes (each is a test in §11 or the library suite)

| Failure | Required behaviour |
|---|---|
| Collector down | Library drops after its queue and counts drops; product unaffected; local copy continues; dead-man's switch or heartbeat alert |
| Prometheus or Alertmanager down | Dead-man's switch alerts within its period |
| Telegram down | Alertmanager retries; history visible in the Alertmanager UI |
| Backend down (O3) | Collector `file_storage` queue holds up to 24 h, then drops oldest and counts it |
| Disk full | Library never blocks; local copy fails closed; product unaffected |
| Clock skew | NTP; skew metric (O2); rule windows tolerate ±2 min |
| Bad rule or config | Rejected in CI; running config unchanged |
| Content leak attempt | Library refuses, Collector drops, test proves both |
| Cardinality spike | `sample_limit` drops it; `ScrapeLimitHit` alerts |

## 15. Adoption checklist (any component, new or existing)

1. **Vocabulary:** add its `service.name`, and any new label keys and values, to `vocabulary.yaml`.
2. **Configure the library:** call `configure(service_name=…)` at start-up, and set `MINIMOI_TELEMETRY_ENABLED=1` in its environment.
3. **Emit facts:**
   - liveness (automatic heartbeat);
   - last success;
   - errors;
   - the RED metrics for request paths (O4).
4. **Rules and tests:** add the alert rules and their `promtool` tests.
5. **Runbooks:** one per alert.
6. **Privacy test** for its signals.
7. **Retire any hand-rolled alert** for the same condition, after parity (§12.5).

## 16. Future direction: connecting tools

**The design keeps three things stable when a tool is connected:**
- the telemetry: OTel, unchanged;
- the rules: the Prometheus format, the source of truth in git;
- the alert keys and severities (§5.4).

**Only Collector exporters and rule import change.**

### 16.1 Grafana (OSS or Grafana Cloud)

**Data path:**
- **Metrics:** `prometheusremotewrite`, or `otlphttp` to Grafana Cloud's OTLP gateway [R20].
- **Logs and events:** `otlphttp` to Loki.
- **Traces (O4):** `otlphttp` to Tempo.

**Alerting:**
- Grafana Mimir and Prometheus evaluate the same `rules/*.yaml` unchanged (ruler API or `mimirtool rules sync`).
- Or Grafana-managed alerting imports them.
- Alertmanager routing moves to Grafana's Alertmanager (the same format).

**Dashboards:** stored as JSON in `config/observability/dashboards/` and provisioned from git.

### 16.2 Splunk (Enterprise/Cloud, Observability, ITSI)

**Data path:**
- **Collector:** Splunk's OpenTelemetry Collector distribution [R21], or the contrib `splunk_hec` exporter for events and logs.
- **Metrics:** Splunk Observability over OTLP.

**Alerting:** Splunk detectors or ITSI correlation searches, translated from the rules. Keep:
- the `minimoi_alert_key` as the episode or grouping key;
- the severity mapping (§5.4).

**The translation is scripted and tested** against the same synthetic series used in §9.2, so parity can be proven.

### 16.3 IBM Netcool (OMNIbus / Cloud Pak for AIOps)

**Events:**
- forward Alertmanager notifications through a `webhook_configs` receiver to the **Netcool Probe for Message Bus** (webhook transport); or
- send the Collector's log stream to IBM Cloud Pak for AIOps, which accepts OTel [R22].

**Field mapping:**

| Netcool field | Source |
|---|---|
| `Identifier` | `minimoi_alert_key` |
| `Severity` | §5.4 |
| `Type` | 1 Problem / 2 Resolution, from `firing` / `resolved` |
| `Node` | `host.name` |
| `Summary` | `summary` |
| `AlertKey` | `alertname` |

**Behaviour:** Netcool's deduplication and Generic Clear then work natively.

### 16.4 Others (same pattern)

| Tool | How it connects |
|---|---|
| Datadog | OTLP ingest or the `datadog` exporter |
| Elastic | APM OTLP intake |
| New Relic | OTLP |
| PagerDuty, Opsgenie | Alertmanager receivers, for paging and on-call |

### 16.5 Fleet management

**OpAMP** [R19] can manage Collector configuration remotely once more than a few hosts exist. It is not needed in O1/O2.

### 16.6 Tool-adoption rule

**When a tool is adopted:**
1. Git remains the source of truth for rules, routing and dashboards.
2. If the tool's UI edits rules, a scheduled export back to git runs, or UI editing is disabled.
3. Run a **parity test** before switching notification ownership: the same synthetic failures (§11 V5–V7) must fire the same alerts with the same keys and severities.
4. Run in parallel for 14 days.

## 17. Phases, ownership and decisions

| Phase | Deliverable | Owner | Gate |
|---|---|---|---|
| **O1 Foundation (dev)** | Library + CLI; vocabulary; bridge; rules + tests; runbooks; compose overlay; CI job; dead-man's switch | Claude Code: library, bridge, rules, tests, runbooks, CI script.<br>Codex: compose, container placement, Operate tile wiring | §11 V1–V12 pass on dev; Robert approves thresholds (D2) |
| **O2 Production** | Stack on EC2 prod; SSM secrets; Mac-to-EC2 forwarding (D5); Operate tile reads the same data; parallel run | Codex deploys; Claude Code reviews and verifies | §11 on prod with evidence; rollback rehearsed; 14-day parity |
| **O3 Tool** | Backend chosen and connected (§16); rules imported | Robert decides; both implement | Parity test (§16.6) |
| **O4 Requests and traces** | RED metrics and traces in portal, cos-scheduler, gateway, runner; model latency and cost | Both lanes, per component | Cardinality and privacy review per component |

**First adopters:**
- the scheduled-jobs registry (through the bridge);
- the health and host snapshots;
- MC's standing checks (MC 1.2 package P).

**For MC:** its thresholds move into §9 rules. Its 05:40 brief becomes an optional digest, and its site chat-posting path is not needed.

### Decisions for Robert

| # | Decision | Recommendation |
|---|---|---|
| D1 | Adopt the direction and standards (§3, §4) | Yes |
| D2 | Thresholds and detection times (§9.1) | As listed: they come from the watchlist you approved |
| D3 | Dead-man's-switch service | healthchecks.io: free tier, independent of AWS and of us |
| D4 | MC daily digest | Keep optional, off by default |
| D5 | Mac jobs in O2 | Mac bridge forwards over TLS with a token to the EC2 Collector |
| D6 | A dedicated Telegram alert bot | Yes: separate from the CoS bots |

## 18. References

**OpenTelemetry**

| Ref | Document |
|---|---|
| [R1] | OpenTelemetry Specification. https://opentelemetry.io/docs/specs/otel/ |
| [R2] | OTLP Specification. https://opentelemetry.io/docs/specs/otlp/ |
| [R3] | OpenTelemetry Semantic Conventions v1.44.0. https://opentelemetry.io/docs/specs/semconv/ |
| [R4] | OpenTelemetry Logs Data Model, SeverityNumber. https://opentelemetry.io/docs/specs/otel/logs/data-model/ |

**Prometheus, OpenMetrics and Alertmanager**

| Ref | Document |
|---|---|
| [R5] | OpenMetrics specification. https://github.com/prometheus/OpenMetrics |
| [R6] | Prometheus, Metric and label naming. https://prometheus.io/docs/practices/naming/ |
| [R7] | Prometheus, Alerting rules. https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/ |
| [R8] | Prometheus, Unit testing for rules. https://prometheus.io/docs/prometheus/latest/configuration/unit_testing_rules/ |
| [R9] | Alertmanager configuration. https://prometheus.io/docs/alerting/latest/configuration/ |
| [R10] | Prometheus, Alerting practices. https://prometheus.io/docs/practices/alerting/ |

**Site reliability practice**

| Ref | Document |
|---|---|
| [R11] | Google SRE Book, ch. 6 "Monitoring Distributed Systems". https://sre.google/sre-book/monitoring-distributed-systems/ |
| [R11b] | Google SRE Workbook, "Alerting on SLOs". https://sre.google/workbook/alerting-on-slos/ |
| [R12] | Brendan Gregg, The USE Method. https://www.brendangregg.com/usemethod.html |
| [R13] | Tom Wilkie, The RED Method (Grafana Labs). https://grafana.com/blog/2018/08/02/the-red-method-how-to-instrument-your-services/ |

**Security and logging**

| Ref | Document |
|---|---|
| [R14] | RFC 5424, The Syslog Protocol (severity levels). https://www.rfc-editor.org/rfc/rfc5424 |
| [R15] | OWASP Logging Cheat Sheet. https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html |
| [R16] | NIST SP 800-92, Guide to Computer Security Log Management. https://csrc.nist.gov/pubs/sp/800/92/final |
| [R17] | CIS Docker Benchmark. https://www.cisecurity.org/benchmark/docker |

**Tracing and fleet management**

| Ref | Document |
|---|---|
| [R18] | W3C Trace Context. https://www.w3.org/TR/trace-context/ |
| [R19] | OpenTelemetry OpAMP specification. https://opentelemetry.io/docs/specs/opamp/ |

**Connecting tools**

| Ref | Document |
|---|---|
| [R20] | Grafana Cloud, Send data using OpenTelemetry (OTLP). https://grafana.com/docs/grafana-cloud/send-data/otlp/ |
| [R21] | Splunk Distribution of the OpenTelemetry Collector. https://github.com/signalfx/splunk-otel-collector |
| [R22] | IBM documentation: Netcool/OMNIbus Probe for Message Bus (webhook); IBM Cloud Pak for AIOps OpenTelemetry ingestion. IBM Docs (URL per product version) |

**Pinned releases (verified 20 Oct 2026 from the GitHub releases API and PyPI)**

| Component | Version | Released |
|---|---|---|
| opentelemetry-collector-releases | v0.162.0 | 2026-09-29 |
| prometheus | v3.15.0 | 2026-09-25 |
| alertmanager | v0.34.1 | 2026-09-17 |
| opentelemetry-python | v1.45.1 | 2026-10-06 |
| semantic-conventions | v1.44.0 | 2026-08-04 |
