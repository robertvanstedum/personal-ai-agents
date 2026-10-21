# MiniMoi observability platform: specification v0.3

**20 October 2026 · Claude Code design, revised by Codex · platform-wide · issue #364**

**Status: revised implementation contract for Claude re-review.** Robert requested this revision now, rather than another findings-only handoff. The predecessor is v0.2 at `cc72288c7588f3501abf0dc8592336441e1011b0`; its SHA-256 is `a8dadc4087a2cc24654b665c93753f4f0123cd86f29c4b7312ea3716300134a6`. This is the second drafting pass requested by Robert; the version is v0.3 because v0.2 already exists. Earlier documents and attributed reviews remain in Git history.

Robert's latest execution direction authorizes independent monitoring-base development alongside MC. **Usable MC comes first; O1 is additive, not a new dependency on ordinary MC actions.** The authorizations do not approve a production diff, notification destinations, a new external account, or retirement of an existing product workflow. The resulting code/config still needs independent review, dev acceptance and applicable owner release approval.

## What changed and whose decisions these are

The [Codex v0.2 review](reviews/spec_161/02_codex_review_v0.2_2026-10-20.md) is retained unchanged. This revision proposes its repairs as explicit build contracts; reviewer advice does not become owner approval by inclusion here.

| Input | Disposition in v0.3 | Verification still required |
|---|---|---|
| Robert: facts emitted once, standard tools own thresholds/alarms | Retained: OTel SDK/Collector, Prometheus, Alertmanager. No custom runtime rules engine | Actual pipeline, reload, delivery |
| Robert: MC first, monitoring base can build now | O1 additive; two lanes continue independently | Existing MC acceptance remains its own gate |
| Codex F1: silent loss of telemetry | Independent expected inventory, missing/stale rules, monitor-target alerts; host/environment identity | Failure injection while components remain down |
| Codex F2: privacy boundary incomplete | Finite metric/event registry, generated standard Collector filters and sanitization, raw-ingress canary tests | Pinned Collector tests, no unsafe debug exporter |
| Codex F3: job/snapshot semantics | Current-start timing; explicit nulls, first-run/grace, source time and source producer ledger | Real source wiring, parity fixtures |
| Codex F4: blocking/local bounds | Bounded asynchronous worker, disabled behavior, byte caps and short CLI flush | Slow disk/network and process-exit tests |
| Codex F5: duplicate ingestion/permissions | OTLP is the sole O1 delivery path; local files are bounded diagnostics, no automatic replay; explicit UID mounts | Native Linux and Mac access tests |
| Codex F6: install/CI failures | Synthetic config renderer, validation entrypoints, rule tests, complete trigger paths | Clean-runner and pinned-container validation |
| Codex F7: routing ambiguity | Scoped keys/grouping; proposed critical+major delivery, isolated dev receiver, usable runbook URLs | Owner notification decisions and two-host tests |
| Codex F8: MC role regression | Tools judge thresholds; MC investigates and acts through authorized product operations | MC integration acceptance, no workflow deletion |
| Codex F9: deployment boundaries | Explicit ingress/egress networks, non-root bridge, budgets on every service | Resource and reachability evidence |
| Codex future notes | Correct sample-limit recovery, tracing proposal and vendor migration claims | O4/O3 review at those phases |

Numeric defaults newly specified below are **Codex proposals for Claude re-review**, not independently approved owner decisions. Existing approved host/guest thresholds are identified separately. All activation choices are listed in §17.


### Reconciliation with Claude's first O1 build

Claude reports `claude/observability-o1` at `f9a03753`, preceded by `e4b4ccee` and `145ccac9`. Codex inspected the dependency file, vocabulary, bridge metric uses, local-copy names and Collector configuration while revising; this is **not** independent clearance of the whole build or its reported248 tests.

| Claude implementation input | Proposed disposition | Remaining delta |
|---|---|---|
| Separate `requirements-observability.txt` | Adopt for images that opt in; no blanket app rebuild | Resolve and hash-lock PyYAML as well as OTel; current `PyYAML>=6.0` is not a reproducible pin |
| Catalogue includes metric type/unit/labels | Adopt | Add finite membership/ranges and Collector enforcement, not only Python validation |
| `minimoi_bridge_source_readable{source}` | Retain as a compatibility diagnostic while adding unified `source_id` coverage/time contract | Map job_registry->jobs, health_snapshot->services, host_snapshot->host, release_status->release; forbid conflicting dual writers |
| `DeployNotFinished` warning | Adopt the intent/name: unverified or in-progress deployment older than1 h is not healthy | Keep explicit outcome3 for unverified; source freshness and original event time remain distinct |
| OTLP-only O1 receiver | Adopt; agrees with F5 repair | Remove unused file_storage and complete ingress privacy controls |
| Per-service latest file; daily event file | Modify for concurrency/disk bounds | Same-service multiple processes still collide; process-private directories and aggregate byte caps in §7 |

The compatibility diagnostic may continue to be emitted for one release if needed; it must not create a second independent coverage state or alert route. The canonical coverage rules use the unified source contract. Nothing in this table treats an author's report as a passed independent review.

## 1. In plain words

MiniMoi components report small, structured facts. OpenTelemetry carries them to a Collector. Prometheus evaluates reviewed rules, and Alertmanager groups and routes notifications. The same facts and coverage state are available to Operate and MC. Changing an alert threshold changes configuration, not application code.

The MiniMoi library validates and emits facts; the bridge adapts existing status files. These are small integrations with the standard platform, not a replacement monitoring product. No LLM is needed for detecting an outage. MC remains responsible for understanding what happened, explaining it, coordinating work, and performing supported actions within its delegated authority.

## 2. Goals, boundaries and delivery

1. One vendor-neutral instrumentation contract for all MiniMoi components, starting with scheduled jobs and existing health snapshots.
2. Unknown, absent, stale and healthy must be distinguishable. Loss of the monitor is itself observable independently of the data it monitors.
3. Product execution never waits for telemetry network or filesystem I/O. Disabling the library stops its I/O and workers.
4. Closed vocabularies and raw-ingress sanitization prevent private text from reaching telemetry stores or notification payloads. This is verified behavior, not a claim based on a key allowlist alone.
5. Rules, routing and deployment are reviewed versioned configuration, with standard tooling and failure/recovery tests.
6. Deterministic monitoring does not grant authority and does not prohibit MC's authorized operational role. MC can read evidence, ask necessary questions, act through supported APIs and verify the result; arbitrary SQL or production code edits remain outside that authority.

O1 includes the library, bridge, vocabulary, deterministic config generation, rules/tests, runbooks, Compose and dev installation. O2 adds production rollout, cross-host transport and Operate integration. O3 selects/adapts other backends; O4 adds request tracing. No existing alerts, brief delivery, learning lines, findings spool, Board follow-through or MC ingress are retired in O1. Retirement is separately tracked under §12. Existing MC release acceptance does not wait for O1.

## 3. Engineering standards adopted

Each row names the adopted design target, not a claim of deployed conformance. Implementation evidence is required by §11. Each row states how the design intends to conform. Full references are in §18.

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

## 4. Invariants

| Invariant | Enforcement |
|---|---|
| Facts in code, judgement in configuration | Library has no alarm thresholds. Prometheus rules contain comparisons/severity; registry contains schedule facts/windows. Bridge never calls `core.jobs.judge` to decide alert severity |
| One O1 delivery path | OTLP/HTTP for metrics/logs; no filelog receiver on the same event stream |
| Independent expectations | Prometheus recording rules materialize enabled emitter/source/target inventory from reviewed configuration, independent of Collector availability |
| No missing-data green | Operate checks coverage before values. Nulls are omitted, never silently replaced by success or epoch zero |
| Nonblocking product | Bounded in-memory enqueue on caller; all I/O on worker; worker failure/drop state observable without recursive telemetry |
| Privacy before persistence/export | Library validates before enqueue; Collector validates and sanitizes before **any** exporter; debug payload logging disabled |
| Stable identity | `(environment, host, service_name)` identifies emitter; job/check/source/condition adds bounded identity, never user names or run IDs |
| No implicit authorization | Metrics/alerts do not create execution grants or approve a deployment |
| Honest evidence | Parsed YAML is not installed software; unit pass is not dev acceptance; dev pass is not production acceptance |

## 5. Signals and vocabulary

### 5.1 Resource and series identity

Every emitted signal has these validated resources:

| OTel attribute | Allowed value / treatment |
|---|---|
| `service.name` | Exact service in vocabulary and enabled emitter inventory |
| `deployment.environment.name` | `dev` or `prod`, assigned/checked by trusted receiver configuration |
| `host.name` | Exact host ID in inventory, e.g. `mac-dev`, `ec2-prod`; never a user-supplied hostname |
| `service.version` | Approved 12-character Git revision or bounded release tag; useful locally, stripped from O1 shared telemetry to avoid uncontrolled values/churn |
| `minimoi.telemetry.schema_version` | Literal `1` |

Collector exports only the first three resources as metric labels. Prometheus's rendered scrape configuration maps `deployment_environment_name` to `environment` and `host_name` to `host`; retains `service_name`. Normalize before rule evaluation and drop the original aliases. Drop scope/SDK labels after ingress sanitization; set `without_scope_info: true` and a fixed schema-defined instrumentation scope. No `service.instance.id`, process IDs, request IDs, context IDs, email, URL, document path or content labels.

The ingest boundary assigns environment and host from its trusted listener, rather than accepting a remote sender's assertion. O1 uses one local-host receiver. O2 uses a separate authenticated receiver route per remote host; it must set the host identity and reject service identities outside that sender's allowlist. A shared bearer token for unrelated hosts is insufficient.

The library's closed metric catalogue is `config/observability/vocabulary.yaml`: for each name, instrument type, unit, finite numeric range, exact required labels and allowed values/membership source. Unknown names, extra/missing labels, wrong types, nonfinite values or bool-as-number are refused. Job IDs come from the registry, not merely a matching regex. Mountpoint values are fixed operational tokens from an approved set; no arbitrary filesystem paths. Per-metric catalogue entries specify whether omitted/null observations are legitimate.

### 5.2 O1 measurement catalogue

All names below are the final exposed Prometheus names: configure Collector `translation_strategy: UnderscoreEscapingWithoutSuffixes` so units are not appended twice. Gauges have unit `s` for seconds/timestamps, otherwise `1`. Counters are monotonic and nonnegative. `E` below is the three resource-derived identity labels; `source_id` is a finite configured source (`jobs`, `services`, `host`, `release`, `guests`, `standing`).

| Name (`minimoi_` prefix) | Type | Additional labels | Semantics |
|---|---|---|---|
| `job_last_success_timestamp_seconds` | gauge | job_id | Last successful finish; absent if never successful |
| `job_last_start_timestamp_seconds` | gauge | job_id | Current/last run start; required for a readable status |
| `job_last_finish_timestamp_seconds` | gauge | job_id | Absent while running |
| `job_last_exit_code` | gauge | job_id | Present only when known; never invent 0 |
| `job_running` | gauge | job_id | 1 exactly when state=running, else 0 |
| `job_failed` | gauge | job_id | 1 exactly when state=failed, else 0; captures failures even with an inconsistent zero exit code |
| `job_has_success` | gauge | job_id | 1 iff last_success_at exists; 0 is an explicit fact |
| `job_status_readable` | gauge | job_id | 1 valid matching status; 0 missing/invalid/inaccessible |
| `service_up` | gauge | service | 1 for explicit ok; 0 for explicit fail/unreachable; omitted for absent service |
| `host_filesystem_used_ratio` | gauge | mountpoint | `1 - avail_bytes/total_bytes`, finite [0,1] |
| `host_memory_used_ratio` | gauge | — | Same ratio, validated denominators |
| `deploy_last_timestamp_seconds` | gauge | — | Original `observed_at`, not bridge poll time |
| `deploy_last_outcome` | gauge | — | 0 verified success, 1 explicit failure, 2 in progress, 3 unverified; image presence alone is not success |
| `guest_requests_pending` | gauge | — | Integer count from authoritative complete summary |
| `guest_requests_oldest_age_seconds` | gauge | — | Oldest pending age; 0 only when a complete summary confirms no pending requests |
| `check_source_readable` | gauge | check_id | Explicit coverage fact |
| `check_last_run_timestamp_seconds` | gauge | check_id | Original check timestamp |
| `bridge_source_readable` | gauge | source | Transitional diagnostic: job_registry/health_snapshot/host_snapshot/release_status, mapped to canonical coverage; not a second alarm truth |
| `source_readable` | gauge | source_id | Last read/schema result; fresh poll does not refresh underlying source data |
| `source_timestamp_seconds` | gauge | source_id | Original source observation timestamp; jobs/release metadata availability use bounded-reader poll completion as specified in §8, not last success/deploy time |
| `telemetry_heartbeat_timestamp_seconds` | gauge | — | Completed worker tick in UTC, never copied from an old file |
| `telemetry_dropped_total` | counter | signal,reason | signal=metrics/logs; reason in queue_full,invalid_contract,series_cap,local_io,export_failure |

Resource labels `E` are implicit on every emitted metric. Numeric ranges: ratios [0,1], binary values {0,1}, enum exact values above, timestamp finite nonnegative and not more than 60 s ahead of receiver time, counts integer [0,2^53-1]. Exit codes integer [-255,255]. A future timestamp becomes invalid coverage, never a healthy zero.

Threshold/expectation metrics are generated **recording rules**, not facts dependent on a live bridge: `minimoi_emitter_expected`, `minimoi_source_expected`, `minimoi_monitor_target_expected`, `minimoi_source_max_age_seconds`, `minimoi_service_expected`, `minimoi_check_expected`, `minimoi_job_expected`, `minimoi_job_missed_after_seconds`, `minimoi_job_stuck_after_seconds`, `minimoi_job_overdue_after_seconds`, and optional `minimoi_job_first_due_timestamp_seconds`. They have explicit identity labels, values from inventory/registry and literal vector expressions. They persist while a sender is down. They are not accepted from OTLP.

### 5.3 Events

OTel log body is the fixed literal `minimoi.event/1`. Severity text is a fixed mapping from SeverityNumber; no free-form message. Attributes: `event.name`, `minimoi.component`, `minimoi.summary_code`, `minimoi.state`, `minimoi.alert_key`. Each is finite/enumerated; alert key is generated from an approved condition plus registered target, not accepted as arbitrary input. Body, scope, resource values and event attributes all pass ingress validation. No log trace/span IDs or free-text exception fields in O1.

Allowed initial facts: job failed/completed, source unreadable/readable, deployment failed/verified and component restarted. Thresholds do not generate events in application code. Proposed severity mapping remains clear=INFO9, warning=WARN13, major=ERROR17, critical=FATAL21; application event severity does not decide notification routing. Prometheus alert labels do.

### 5.4 Bounded inventory and configuration generation

`emitters.yaml` lists environment/host/service identity, enabled flag and allowed metric families. `sources.yaml` binds each source to its emitter, read-only path, schema, size cap and freshness window. `monitor-targets.yaml` lists expected Prometheus scrape jobs/instances. Initial enablement is the dev bridge only plus the four monitor targets; component emitters are added explicitly when installed. Job expectations are the active, host-filtered registry subset. Disabled/future installations are not falsely expected.

`render_config.py` is a deterministic **build-time renderer**, not a monitoring daemon or evaluator. It validates cross-references, expands finite memberships into standard Collector filter configuration, renders Prometheus recording rules, and renders environment-specific target/routing config. It does not contact an LLM, read credentials, invent thresholds or run application actions. Generated public artifacts contain no secret values. Changes are reviewable diffs; CI requires deterministic output and tests the rendered files, not just templates. Rendered secret paths may be synthetic in CI; secret contents are provisioned separately.

Budget: at most 2,000 application series per environment, 256 active metric keys per emitting process by default, 100 jobs and 32 services maximum unless a reviewed sizing change increases them. Renderer calculates worst-case cardinality from finite memberships, not an estimate alone. Scrape limit is 2,000 samples on the application target; exceeding it rejects the whole scrape. Internal monitor metrics have a separate measured cap (initial proposal 10,000), with `up` and recent-limit alerts. Source-data limits are in §8.

### 5.5 Future tracing

O4 uses W3C Trace Context and standard OTel spans. Proposal: send all bounded eligible spans to tail sampling, retain all errors and 10% of successful traces, with explicit collector capacity/drop evidence. Do **not** first head-drop 90% and then promise all errors. This proposal remains an O4 review item; no trace receiver/pipeline is enabled in O1.

## 6. Architecture

```text
component -> bounded OTel library -> OTLP -> Collector -> Prometheus -> Alertmanager
                         |              |                         |       |
                  local diagnostics    sanitized archive      Operate   approved notifier
existing status files -> bounded bridge                            |       + external Watchdog
                                                                  MC
reviewed inventory -> Prometheus expectation recording rules (independent of Collector)
```

The local diagnostics are not a second ingest path. O1 does not add a durable custom delivery broker, a custom rule evaluator or automatic local-file replay. Best-effort telemetry loss is counted; original job/application records remain the records of truth. Collector archive is a diagnostic sink, not an exactly-once audit ledger. A permanent Collector outage does not remove expected-emitter records or stop its scrape-down alarm; a Prometheus/Alertmanager outage is covered by the external Watchdog path.

## 7. Library and shell CLI

### 7.1 Files and API

`core/observability/{__init__,vocabulary,emitter,bridge,cli}.py`, with dependencies in `requirements-observability.txt` and a resolved/hash-locked dependency set. Only adopting images install them. API retains `configure`, `gauge`, `count`, `event`, `heartbeat`. `configure` loads and validates the finite registry once before product serving. No API accepts a notification threshold, shell command, arbitrary exporter destination per call or arbitrary label dict outside the catalogue.

### 7.2 Caller behavior

- `MINIMOI_TELEMETRY_ENABLED=0` is the default and is a true no-op: no worker, network, local-copy writes or implicit heartbeat. Existing application status files continue as before.
- Enabled calls perform bounded validation and a nonblocking enqueue only. Queue: 2,048 records, encoded record cap 4 KiB; maximum total queued payload 8 MiB. Full queue drops the incoming record and increments a fixed in-memory counter without recursively calling this API.
- Invalid calls raise `TelemetryContractError` in tests/dev; production drops/counts a fixed reason code. No rejected payload is logged. A configuration failure in production disables telemetry and emits only a fixed diagnostic code to stderr; it must not prevent the application from starting. Monitoring expectations expose the loss.
- Numeric validation, label membership and depth are bounded before serialization; no user object methods or arbitrary mapping traversal. Metric keys are capped before SDK instrument creation.
- Gauge observations use a bounded latest-value map/observable callback; a value not refreshed for 180 seconds is removed from emission. The snapshot bridge replaces a source's current observations as one logical batch so an omitted/invalid old fact is not re-exported forever. This is housekeeping, not a threshold judgement. Counts use normal monotonic SDK aggregation.

### 7.3 Worker, local copy and export

One daemon worker per configured process serializes bounded data; all filesystem I/O is here. It attempts the local copy before passing a record to the SDK. If local persistence fails, count local_io and continue best-effort network export of the already validated record. Do not block export indefinitely or claim a durable receipt.

Use a process-private subdirectory under `MINIMOI_TELEMETRY_LOCAL_DIR`, mode0700; files0600, no symlinks/hardlinks, atomic latest-snapshot replacement, process-unique names. Local events rotate at 5 MiB with maximum four files **per process**, latest snapshot <=1 MiB. A janitor in the telemetry worker enforces a 100 MiB total directory cap and 14-day age cap with a bounded scan; at cap/refused cleanup, skip local writes and count loss. Never delete application files or follow links. Include crashed-process directories in capacity accounting. Define a maximum number of process directories (64); if exhausted, network-only mode plus local_io diagnostic. No shared `metrics-latest.json` overwrite race.

OTel metrics: PeriodicExportingMetricReader at60 s, export timeout5 s. Logs: BatchLogRecordProcessor queue2048, batch512, timeout5 s. SDK queue losses must be observable or covered by the wrapper's own accounting; do not assume the SDK exposes a public drop callback. Shutdown has a total2 s deadline and never blocks product process termination on a stuck I/O worker. Heartbeat advances only when the worker makes progress, not merely because the application process exists. Product latency test: no filesystem/network calls from producer functions; p99 enqueue <5 ms under synthetic normal load and <10 ms with blocked worker, measured separately from app latency. These are proposed acceptance budgets, not achieved measurements.

### 7.4 Configuration and CLI

Required when enabled: `MINIMOI_ENV`, `MINIMOI_HOST_ID`, catalogue-approved service name. Endpoint is fixed operator configuration `OTEL_EXPORTER_OTLP_ENDPOINT`; O1 private local-network HTTP is allowed, cross-host is TLS-only. `MINIMOI_TELEMETRY_HEADERS_FILE` is a MiniMoi extension translated to the SDK's headers configuration; do not call it a standard OTel `_HEADERS_FILE` variable. Validate UID/private mode and refuse symlinks; do not print values. No provider tokens or application credentials are needed for telemetry.

`minimoi-telemetry gauge ...` and `event ...` retain the v0.2 CLI interface. A one-shot command explicitly performs a bounded2 s SDK flush/shutdown, not a wait for the60 s timer. Exit remains0 for telemetry failure when used by jobs; optional `--validate-only` is a separate CI/admin command with nonzero on invalid contracts. The no-op switch applies to CLI too. Short-lived CLI emits no persistent emitter heartbeat and is not added to expected-emitter inventory; scheduled jobs are monitored through their authoritative status files and bridge. Tests prove a one-shot metric reaches a local synthetic OTLP receiver before exit, or a fixed loss diagnostic is produced within2 s.

## 8. Bridge and source contracts

The bridge polls every60 s, emits facts and heartbeat, and never evaluates alerts. Reuse the job registry loader and status validator; **do not directly call the unbounded `read_status` file reader**. Reads use a confined read-only source root, descriptor-based no-follow open, nonblocking open before regular-file check, size+1 bounded read, strict JSON with duplicate/nonfinite rejection, matching schema/identity. Per-source read budget3 s; one unavailable source must not prevent other sources from reporting. Do not create, chmod or repair source directories.

### 8.1 Required bindings and producer ownership

| Source | Binding / schema | Maximum | Producer / delivery status |
|---|---|---|---|
| Job registry | `/app/config/scheduled_jobs.json`, schema_version1 | 256 KiB /100 jobs | Existing checked-in registry, read-only |
| Job files | `/sources/jobs/<registered status_file>`, core/jobs/status schema1 | 64 KiB each | Existing jobs; bind actual host jobs directory, not a guessed container path |
| Services | `/sources/jobs/health/services.json`, `mc-health-snapshot/1`, sampled_at, environment, services[{name,status}] |16 KiB /32 services | Host health exporter wiring owned by Codex; existing MC adapter has fixtures, not proof of a live producer |
| Host | `/sources/jobs/health/host.json`, `mc-host-snapshot/1`, sampled_at, filesystems[{mount,total_bytes,avail_bytes}], mem_total_bytes, mem_available_bytes |8 KiB /32 mounts | Host probe/exporter wiring owned by Codex; measure actual host, not container limits |
| Release | `/sources/release/status.json`, `minimoi.release-observation/1` |16 KiB | Bind deployment's real release-status directory read-only; integration owner verifies producer |
| Guests | authoritative portal pending-summary adapter |1 KiB | Claude domain adapter + Codex integration; counts/age/coverage only, no account file mount in bridge |
| Standing | bounded current check observations |16 KiB | Existing standing service; raw facts only, existing delivery unchanged |

`sources.yaml` records actual producer revision, schedule, native absolute binding and expected freshness. Native paths are private deployment config; public config uses mount variables. Missing producer is an explicit unavailable source, not a reason to synthesize healthy data. Guest/standing emit directly from their owning service or via a reviewed sanitized snapshot; no direct guest DB credentials in the bridge.

Source max-age proposals: jobs collection180 s; health/host3600 s initially to match current watchlist; standing/guest3600 s; release uses an on-demand observation source (freshness indicates observation availability, not “a deployment must happen hourly”). For release metadata, `source_timestamp_seconds` is the bridge's completed validated read time (availability of the last-deployment record); `deploy_last_timestamp_seconds` remains original `observed_at`. An old completed deployment is not an outage merely because no new deployment occurred. This availability measurement does not assert that deployed services are currently healthy: only the fresh services source can establish that. A last-known in-progress record retains its original event time and can fire DeployNotFinished; the bridge never resets its duration. Document this availability-vs-event-time exception explicitly in the emitter and tests. Health sources may later refresh faster by reviewed config; do not silently claim a faster detection SLA than their collection cadence allows.

### 8.2 Field mapping and lifecycle

- Filter jobs by `Registry.for_host` with an explicit `mac-dev -> mac`, `ec2-prod -> ec2` deployment mapping. Only configured enabled jobs create expectations. Registry has no generic enabled field today; the finite monitoring inventory selects its subset. Do not silently add fields to the existing registry loader.
- Preserve started_at/finished_at/last_success_at exactly; finished/exit absent while running. Emit failed from state, and has_success explicitly. State `warn` is a completed run under the existing contract; it does not erase last success. A source can be readable yet have never succeeded.
- `active_from + expected_every_s + grace_s` produces first_due when active_from exists. Without active_from and without a readable status, coverage is unknown; do not invent an installation time or epoch. A running first attempt is not a missed-success alarm until its configured stuck duration is exceeded. A finished run with has_success=0 is a separate failed condition.
- Reject timestamps >60 s in future and impossible numeric values; emit unreadable/invalid coverage. A stale but structurally valid snapshot retains its **original** source timestamp. Never refresh it with the poll clock.
- Services: explicit ok=1; fail/unreachable=0; absent expected service is unavailable coverage, not down or healthy. Host ratios use total>0, 0<=available<=total. Fixed mounts only.
- Release: selected_image_check_failed ->1; deployment_in_progress ->2; selected_images_observed plus complete successful verification ->0; failed verification ->1; no complete verification ->3. Unknown status ->unreadable. A stale in-progress deployment is an explicit warning rule, not a synthetic success.
- Guest summary must be complete across stores; partial counts are not complete pending totals. No names/emails/event payloads enter metrics.

Use shared parsing helpers where available, but not the old judgement layer. Preserve reference fixtures from `core/jobs/judge.py` for late/missed/stuck/first-run parity; differences are recorded, not silently retired. Source adapters are independently testable without live hosts or secrets.

## 9. Prometheus rules, routing and recovery

### 9.1 Executable rule baseline

The complete proposed rule set is [evidence_v0.3/rules.yaml](reviews/spec_161/evidence_v0.3/rules.yaml): 25 alert rules plus the `minimoi_source_current` recording rule. These are standard Prometheus rules, supplied as a versioned spec fixture for the implementation to adopt into `config/observability/rules/`. They replace the v0.2 rule block. Review changes to the resulting configuration against this baseline; do not run two independent copies in production.

The renderer also emits expectation/threshold recording rules from §5.4. Include `minimoi_service_expected` and `minimoi_check_expected` for each enabled service/check. Fixture constants are **not** a production inventory. All binary joins match explicit identity labels, so a different scrape target or host cannot silently match. No aggregate drops environment or host.

Examples of the repaired core expressions (the linked YAML is authoritative):

```promql
# An expected emitter must be present AND fresh. Its absence is a result.
(minimoi_emitter_expected == 1)
unless on (environment,host,service_name)
  ((minimoi_telemetry_heartbeat_timestamp_seconds >= time() - 180)
   and (minimoi_telemetry_heartbeat_timestamp_seconds <= time() + 60))

# Actual start time, never previous finish time; full rule also gates readable source.
(minimoi_job_running == 1)
and on (environment,host,service_name,job_id)
  ((time() - minimoi_job_last_start_timestamp_seconds)
   > on (environment,host,service_name,job_id) minimoi_job_stuck_after_seconds)

# One missing failure family must not erase failures in the other.
sum by (environment,host,job,instance)
  (rate({__name__=~"otelcol_exporter_send_failed_(metric_points|log_records)_total"}[10m])) > 0
```

`minimoi_source_current` is computed from explicit readable=1, original timestamp within source max age, and no future skew. Missing source max age fails closed. Rule evaluation, not bridge code, determines freshness. Emitters retain/omit data as described in §7; Prometheus source coverage gates prevent stale healthy values being displayed as current.

| Rule family | Proposed `for` / detection bound after observations reach Prometheus |
|---|---|
| Emitter missing/stale | Freshness180 s + for5 m; <=8 m30 s after last heartbeat, including a vanished series |
| Collector/monitor scrape unavailable | for2 m; <=3 m with30 s scrape/evaluation |
| Source coverage unavailable | for5 m after configured freshness window or explicit unreadable |
| Job failed / no success | for1 m; <=3 m including bridge poll and scrape |
| Job stuck / missed / late / never-ran | configured runtime/window + for5 m /10 m /10 m /10 m |
| Job status unreadable | for30 m; UI unknown immediately |
| Service explicit down / missing observation | for5 m; service cadence is additional, never omitted from SLA |
| Disk critical/high; memory critical/high | 95%/80% and95%/85%, for10 m/30 m; source cadence additional |
| Deployment failed / unverified-or-in-progress over3600 s | for5 m; status remains unavailable if no complete observation |
| Guest oldest pending >48 h | for1 h, warning; no unscoped first-result query for count |
| Standing check missing/stale | max age3600 s + for5 m; source unreadable for30 m |
| Drops/sample-limit breach | recent increase (15 m/10 m); no forever-fired historical counter |
| Collector export failures | rate window10 m, for10 m |
| Watchdog | always firing, no `for`; independent external failure deadline |

Disk/memory/guest threshold values came from Robert's approved watchlist. This revision preserves them; routing and new timing defaults remain proposals under D2. Older late/never-ran/warn semantics remain visible in parity evidence. Any non-equivalent condition needs an explicit retirement disposition, rather than claiming full parity from the common subset.

### 9.2 Test requirements

[acceptance-tests.yaml](reviews/spec_161/evidence_v0.3/acceptance-tests.yaml) has five targeted scenarios: missing emitter fires and recovers; fresh run does not appear stuck; overlong run does; a healthy second host does not hide loss; one absent exporter-failure family does not erase the other. They assert desired v0.3 behavior, unlike the preserved v0.2 bug reproductions.

Implementation must add firing, non-firing and recovery checks for **every** alert, including missing series, absent-from-first-install, disabled inventory, future clock, different environment/host, stale source and threshold boundaries. Coverage tooling must check assertions and expected labels/annotations, not just presence of an alert name. Watchdog is the explicit exception: always-firing plus external delivery absence/recovery test replaces a mathematical non-firing case. Immediate drop/limit alerts deliberately have no `for`; metadata validation permits these named exceptions.

### 9.3 Alertmanager policy

Proposed notification policy: **critical and major** notify; warning is visible in Prometheus/Operate and optionally the existing owner-approved brief. This resolves v0.2's contradiction with JobFailed acceptance. It remains D2 for activation. Until destinations/policy are approved, route to blackhole; use a local fake webhook for integration tests. Dev must not send to production recipients.

Rendered routing uses:

```yaml
route:
  receiver: blackhole
  group_by: [environment, host, service_name, minimoi_alert_key]
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 12h
  routes:
  - matchers: ['alertname="Watchdog"']
    receiver: deadman
    group_wait: 0s
    group_interval: 1m
    repeat_interval: 1m
  - matchers: ['severity=~"critical|major"']
    receiver: telegram-critical
inhibit_rules:
- source_matchers: ['severity="critical"']
  target_matchers: ['severity="warning"']
  equal: [environment, host, service_name, minimoi_alert_key]
receivers:
- name: blackhole
```

The renderer includes the two active routes/receivers **only when configured and authorized**. Without a real or synthetic destination, omit that route rather than leave an invalid/insecure placeholder. Self-monitor alerts receive the deployment's explicit environment/host and fixed service_name `minimoi-observability`; application alerts retain emitter identity. `minimoi_alert_key` is a bounded condition key; the full identity includes group dimensions, and downstream adapters must concatenate/encode those dimensions before deduplication.

Telegram config uses `bot_token_file`, an integer chat_id rendered from private deployment input, `send_resolved: true`, and a fixed HTML-escaped template. Messages show bounded summary, severity, scoped identity, timestamp and an absolute authorized runbook URL. No raw label dump, free-text log body or guest identity. A resolved notification says **“condition no longer firing; check coverage for recovery confirmation”** when the system cannot prove current healthy evidence. It must never imply a source outage is recovery. Operate gives coverage precedence and links related source/emitter alarms. No custom alert-state daemon is introduced to obtain this behavior.

Runbook URLs resolve in the release being activated; the spec fixture uses the intended public repository paths. CI checks each local runbook exists. Owner-authenticated internal runbooks may be used with a working absolute site URL; no tokenized links in telemetry. D3 chooses the external heartbeat destination. Its period1 m/grace5 m is proposed; verify actual down/recovery behavior, not merely HTTP200. The Watchdog tests monitor Prometheus/Alertmanager and delivery, while emitter/target alerts monitor the Collector and sources. One does not replace the other.

## 10. Configuration, images and installation

### 10.1 Pinned dependencies and ownership

Keep the v0.2 selected versions: Collector contrib0.162.0, Prometheus3.15.0, Alertmanager0.34.1, OTel Python SDK/exporter1.45.1. Semconv1.44.0 remains the naming reference. Their selection provenance is retained in §18; this revision did not independently repeat the release-age audit. Pin platform-compatible image digests and Python package hashes before installation; record source/version/platform/digest in `config/observability/VERSIONS.md` and executable lock/env files. A `<digest>` placeholder is not an installable pin. Do not float versions while repairing config.

Claude owns library/bridge/rules/rendering/CI/runbooks; Codex owns Compose, image placement, source-producer integration and Operate. Shared paths have one editor at a time in the integration worktree. Both continue owned work using the contracts here, and independently review the other lane before dev activation. A missing secret, production source or approval stops only its dependent activation.

### 10.2 Collector pipeline and privacy generation

Use standard `otlp` receiver (HTTP4318), `memory_limiter`, `filter` and `transform` processors, `batch`, `prometheus` and `file` exporters, and `health_check`. **No O1 filelog receiver and no unused file_storage extension.** Disable unused protocols/profiling/debug endpoints. Nothing reads local-copy files into Collector.

The generated Collector configuration must implement this order:

1. Memory/body bounds before expensive processing. Receiver request body limit1 MiB; bounded batch size1024 and batch timeout5 s; verify actual component option names on0.162.0. Container cap256 MiB, memory limiter200 MiB/spike50 MiB. Reject oversize payloads; do not print them.
2. Assign/check trusted environment/host per receiver. Validate permitted service identity and schema1. This does not make telemetry an authorization system; a trusted compromised emitter can lie about facts, but cannot use unregistered service identities or arbitrary strings to leak content.
3. Drop unsupported metric names/types/units, invalid value ranges or label combinations. Enforce exact finite label memberships and required keys. Do the equivalent for event attributes/severity/body. Configure OTTL `error_mode: propagate` so a failing filter cannot silently pass an unsafe record.
4. Strip all resource fields except the fixed approved identity/schema fields. Strip scope attributes/version/schema URL; use a fixed instrumentation scope name. For metrics, clear arbitrary descriptions and normalize schema URLs; unsupported exemplars are removed or the point is dropped. O1 has no histograms/traces. For logs, normalize body to the fixed literal, severity_text to the fixed mapping, event_name to the finite catalogue; remove arbitrary trace/span IDs and unknown attributes. Unknown resource/scope/log fields must not survive into archive protobuf JSON just because no metric label uses them.
5. Batch only sanitized data. Both Prometheus **and** archive receive the same validated pipeline output. No debug exporter or payload-level debug logs in tests with canaries or in production.

The build-time generator emits explicit OTTL conditions from the finite catalogue using the pinned processor syntax. The exact generated YAML is part of the implementation diff and must pass `otelcol-contrib validate` plus direct-ingress tests. A prose allowlist or `keep_keys` alone does not satisfy this contract. Unknown supported-field values are dropped; extraneous metadata is removed; either outcome is acceptable only if no canary reaches an exporter or diagnostic log. Invalid numeric records must not be recast as healthy defaults.

Exporter settings that must be explicit:

```yaml
exporters:
  prometheus:
    endpoint: 0.0.0.0:8889
    translation_strategy: UnderscoreEscapingWithoutSuffixes
    metric_expiration: 5m
    without_scope_info: true
    resource_constant_labels:
      included: [service.name, deployment.environment.name, host.name]
  file/archive:
    path: /var/lib/otelcol/archive/telemetry.jsonl
    rotation: {max_megabytes: 50, max_days: 14, max_backups: 9}
service:
  telemetry:
    metrics:
      level: normal
      readers:
      - pull:
          exporter:
            prometheus:
              host: 0.0.0.0
              port: 8888
              without_type_suffix: false
              without_units: false
```

This fragment specifies fixed settings, **not a runnable full Collector config** without the generated privacy processors/receivers/pipelines. The implementation must supply that resolved config and its validation evidence. Archive cap is approximately500 MiB including active file; account for filesystem overhead in capacity. Metrics expiration remains5 m; the expectation-based rule handles expiry correctly. Verify exposed internal metric names against the rules before activation; if a distribution differs, change config/rule together and test, never infer health from an empty query.

### 10.3 Prometheus configuration

Render30 s scrape and evaluation intervals; rule files include both generated expectations and generic rules. Application scrape `otel-collector:8889`: sample_limit2000, label_limit12, bounded label-value length128, response body limit4 MiB. Internal scrape targets: Collector8888, Prometheus9090 and Alertmanager9093 with a separately sized cap. No public target URLs are accepted from telemetry input.

Application scrape metric relabeling performs the identity normalization in §5.1. Target labels for internal monitor scrapes carry `environment`, `host`, `service_name=minimoi-observability`; expected-target recording rules include exact job/instance labels. Do not apply the monitoring host label to remote application emitters and overwrite their identity. Tests compare actual exposition, including Collector scope/helper series, against the declared sample/label limits.

Set retention15 d or2 GB, whichever is reached first; data dir persistent. `--web.enable-lifecycle` is available only on the internal admin network. Validate the **rendered** prometheus.yml with promtool check config using its real rule-file mount layout. No unresolved `${MINIMOI_ENV}` or secret substitution assumptions. CI renders dev values; production rendering is separately verified.

### 10.4 Compose and image contract

`docker-compose.observability.yml` is an additive overlay; `docker-compose.observability.dev.yml` adds only local UI binding. Production has no published UI or OTLP port in this overlay. Use SSH tunnel or a reviewed authenticated query route for operations. O2's authenticated nginx OTLP location is a specific future exception, not permission to expose monitoring UIs.

| Service | Image / command | UID and writable data | Memory / CPU |
|---|---|---|---|
| Collector | pinned contrib image; `--config=/etc/otelcol/collector.yaml` |10001:10001; only `/var/lib/otelcol` |256 MiB /0.20 |
| Prometheus | pinned image; config+retention flags |65534:65534; `/prometheus` |512 MiB /0.20 |
| Alertmanager | pinned image; config and storage flags |65534:65534; `/alertmanager` |128 MiB /0.10 |
| Bridge | existing MiniMoi Python base, minimal target containing core/jobs, core/observability, catalogue and pinned dependencies; `python -m core.observability.bridge --interval 60` |configured **nonzero** host-compatible UID/GID; local diagnostics only |128 MiB /0.10 |

Total service caps1 GiB and0.60 CPU, excluding Docker VM/OS/application overhead. This is a proposed constrained baseline and must pass measured load on dev and EC2; caps are not evidence of adequate headroom. Use a minimal image target, not a full second portal runtime or an unpinned arbitrary `MINIMOI_APP_IMAGE`. Non-root, read_only, cap_drop ALL, no-new-privileges, pids_limit and bounded writable tmpfs are explicit for all services. No Docker socket, host filesystem root or credential directories in bridge.

Networks:
- `observability` internal: all four services, monitoring/query traffic.
- `telemetry_ingress` internal: Collector plus explicitly enabled trusted app emitters; no blanket app-default membership. An emitter may also use its existing application network.
- `notification_egress`: Alertmanager only, providing outbound HTTPS access. This network alone does not implement destination allowlisting; document/enforce any required host/proxy egress restriction separately. Collector does not need public egress in O1.

Read-only mounts: resolved config directories, finite job registry, host's actual jobs root at `/sources/jobs`, actual release observations at `/sources/release`; no writable source mounts. Guest/standing data is emitted by its owner, not granted to bridge as broad data-directory access. Collector does not mount library local-copy files.

Health verification uses Collector health_check13133, Prometheus `/-/ready`, Alertmanager `/-/ready`, plus successful scrape and round-trip acceptance. Do not use `--version` as the running service healthcheck. If an upstream image has no HTTP client, use an explicit external readiness probe in deployment/monitoring rather than pretending an absent curl binary works.

### 10.5 Ownership and secrets

Bind mounts preserve real host file permissions; Compose's file-secret declarations do not magically change owner-created0600 files into readable files for65534. Provision private parent directories, and service-specific files owned/readable by the actual service UID (or narrowly granted ACL) at the mount. Keep files0600 unless a reviewed group-only arrangement is required. Verify reads as the actual UID without printing content. In CI use synthetic secrets and fake webhooks only.

For source mounts, discover/document the actual producer owner first. Prefer running the bridge under the same non-root host UID where possible. If a producer writes root-only files, do not make the bridge root or chmod broad directories. Supply a reviewed sanitized export owned by the bridge account, or fix the producer's narrowly scoped read contract in its normal build lane. Preserve atomic writer semantics; chmod0600 on each rewrite can invalidate an assumed group/ACL arrangement. An inaccessible source remains unknown until the mapping is proven on the native host. Mac shared-mount success alone does not prove Linux access.

Telegram bot token and external heartbeat URL are provisioned from owner-approved secret storage (production SSM namespace `/minimoi/production/observability/*`). Renderer gets integer chat_id and secret **paths** from private deployment input; it never substitutes token contents into committed files. Agent diagnostics must not print tokens or webhook URLs. Active config rendering is atomic: validate temp output, then replace; never overwrite a live known-good config with an invalid render.

### 10.6 Installation sequence

Commands below are the implementation interface to deliver; they are not claims that the scripts/images already exist. `scripts/observability/render_config.py` and `check_config.sh` must be implemented and tested before this spec is called installation-verified.

1. Measure disk/memory/CPU headroom with existing applications running. Reserve at least4 GiB additional disk for images/data/rotation overhead; budget further if image sizes require it. Verify host time (read-only NTP status), architecture and approved exact image digests. Never free disk by resetting a shared checkout.
2. Render a credential-free dev bundle from inventory. Validate cross-references, finite cardinality, image digests, non-root UIDs, source paths, CPU/memory caps and that notification routes are disabled/fake. CI uses the same renderer.
3. Prepare only the explicit data/config directories with the service owners from §10.4; verify permissions as those UIDs, including source reads. Create a temporary synthetic source tree for the first pipeline test; do not modify real job status to simulate failure.
4. Validate rule definitions, all rule tests, rendered Prometheus and Alertmanager config, Collector config, and combined Compose config. Use actual mount layouts; no missing templates/secrets. Example tool entrypoints:

   ```sh
   docker run --rm --entrypoint /bin/promtool <pinned-prometheus-image> --version
   docker run --rm --entrypoint /bin/amtool <pinned-alertmanager-image> --version
   docker run --rm <pinned-collector-image> validate --config=/etc/otelcol/collector.yaml
   ```

   `check_config.sh` supplies the validated read-only mounts, synthetic env/secrets and writable temporary directories needed by each command. Resolve real binary paths from the pinned images. A command printed here without its mounted config is illustrative, not evidence of a completed validation.
5. Start only the four observability services with the approved dev overlay. Leave existing MC/application configuration unchanged. Readiness must pass; emit synthetic raw OTLP and library records and query real Prometheus. Run §11.
6. Bind real dev source paths and enable one emitter at a time after its source/permissions/identity tests pass. Add its expected inventory with the same reviewed rollout. Confirm coverage state and failure/recovery; a source not yet wired is marked unavailable, not complete.
7. Record source revision, rendered-config hash, dependency digests, test evidence, failures/limitations, resource measurements and rollback in the review trail. Independent review precedes shared-dev activation, using the existing MC integration discipline.
8. O2 production is a separate reviewed-diff approval. Provision approved destinations and TLS/cross-host sender credentials only for that rollout. Never carry fake/dev identities or tokens into production. No main push from this installation procedure without release approval.

## 11. Acceptance and evidence

All rows describe required behavior; they do not imply tests already passed. Product reads/writes remain in their approved environments. Failure injection uses synthetic source trees and local fake sinks. Production checks avoid deliberate service outage or private-data mutation unless separately approved.

| ID | Scenario | Required evidence |
|---|---|---|
| V1 | Clean pinned config | Renderer reproducible; promtool rules+config, amtool rendered config, Collector validate, Compose config pass without real credentials |
| V2 | Live pipeline | Library and raw OTLP -> sanitized Collector -> actual Prometheus names/labels; all expected monitor targets UP; bounded archive readable |
| V3 | Job coverage | Exactly enabled host-specific registry subset; null success omitted; active_from/grace honored; missing/corrupt/FIFO/oversize/future/wrong-job sources unknown; no source writes |
| V4 | Rule behavior | Every rule fires, stays quiet and clears as specified (Watchdog exception); full labels/annotations; missing/disabled/two-host/two-environment cases |
| V5 | Synthetic failed job | Alert within3 m of bridge-visible failure, local fake notification, scoped runbook link; successful new observation clears; missing source never described as verified recovery |
| V6 | Fresh start vs stuck | Long idle gap followed by a fresh run stays quiet; duration past stuck_after+5 m fires; first run and never-run distinct |
| V7 | Host/guest/release facts | Boundary ratios and stale snapshots; complete vs partial guest counts; verified deploy vs merely selected images; no invented zero or new deploy from fresh poll |
| V8a | Stop emitter/bridge | Expected inventory remains; missing/stale fires within8 m30 s even after exported series expire; other host does not mask it |
| V8b | Stop Collector | Scrape-down within3 m while stopped; Watchdog still firing is expected, not considered detection of Collector loss; application path remains usable |
| V8c | Stop Prometheus/Alertmanager | Local fake external dead-man harness detects missing ping; approved real service later proves <=configured deadline and recovery; do not rely on an in-process check |
| V9 | Raw-ingress privacy | Synthetic canary in body/resource/scope/name/description/allowed-label value/unknown metadata through raw OTLP, bypassing library; no canary in exporter, archive or logs; unsupported traces rejected |
| V10 | Bounded pressure | Queue/series/disk/record/receiver caps, 2,500-series malicious input, sample/label cap causes explicit failed scrape and coverage warning; no unbounded memory or product I/O |
| V11 | Restart/permission | Correct service UIDs read only intended files and secret paths on Linux and Mac; source replacement retains access; history/Alertmanager notification log survives restart; within repeat window no duplicate; no exactly-once claim after lost storage |
| V12 | Resource/performance | Measure1 GiB/0.6 CPU service caps, host headroom and workload; enqueue latency budget with slow disk/unreachable endpoint; CLI <=2 s flush/loss diagnostic |
| V13 | Scoped notifications | Same key in two hosts/environments is distinct; critical inhibits warning only within same full identity; dev cannot reach prod receiver; HTML/template escaping and valid links |
| V14 | Reload/rollback | Invalid render/reload retains old active config; valid threshold change requires no application rebuild; previous digests/config restored; state compatibility checked |
| V15 | Product/MC continuity | MC can inspect coverage and explain/act through existing authorized operations; no automatic grants; brief/learning/Board delivery still works; no retirement by enabling O1 |
| V16 | Source provenance | Each producer is named with actual revision/path/cadence; all synthetic-only sources explicitly marked; Operate never combines stale data into a green overall state |

Every acceptance record distinguishes built, independently reviewed, verified on dev and verified in production. The five v0.3 spec regression scenarios and YAML parsing are only author verification of the rule design; the remaining pipeline/host acceptance is still work to perform.

## 12. Change control, operations and retention

### 12.1 Configuration control and CI

All catalogue/inventory/rule/routing changes are reviewed PRs. No application rebuild for a threshold-only change, but configuration validation/reload and resulting behavior are verified. Version the full active config set atomically; verify Prometheus and Alertmanager's reload success metrics/HTTP results. A failed reload does not update the recorded active-config hash. Cross-service rollout is not atomic; stage compatible configs and verify each before declaring success.

`.github/workflows/observability-config.yml` triggers for `config/observability/**`, `core/observability/**`, `scripts/observability/**`, observability tests, both Compose overlays, observability Docker/requirements files, dependency lock files, the workflow itself and runbooks. Include supporting shared changes in the normal full CI as well.

The clean-runner job renders finite dev fixtures and a dummy numeric chat ID, temporary synthetic token/heartbeat files and fake sink URLs. It runs catalogue/inventory validation; deterministic regeneration check; promtool check rules/test rules/check config; amtool check-config against the **rendered** file and actual mount layout; Collector validate; Compose config; rule metadata/coverage checks; library/bridge unit tests; and the bounded privacy/pipeline integration job. Pin tools by digest/hash. Never require production secrets merely to validate configuration. All fixture timestamps/IDs are synthetic. Use correct image entrypoints instead of passing `amtool` as a server argument.

### 12.2 Upgrade and rollback

Quarterly or security-driven upgrades: inspect upstream changelog; update pins; rerun full clean validation/privacy/identity suite; dev failure/recovery acceptance; independently review; request exact production release approval. Retain previous compatible images/config and data format notes. Test whether older versions can read current state before promising downgrade.

Stop only observability services for stack rollback. Telemetry-disabled product behavior stays usable; existing job records/alerts/brief remain. Config rollback restores prior reviewed files and verifies reload. Keep data for its bounded retention; no opportunistic deletion of other worktrees or application data. O3 future exporters need an explicit supported persistent sending queue sized by items/bytes and retry budget; declaring `file_storage` alone is not a24-hour queue. O1 promises no offline replay.

### 12.3 Parallel-run and retirement

Keep existing Curator ledger alerts, guest/health nudges and Mac jobs watchdog until **each condition** has14 days of parity and approved retirement. Count synthetic failures as evidence of coverage, but distinguish them from naturally observed incidents. Record missed/extra/delayed alerts, routing and recovery differences. Dedupe during overlap by explicit receiver policy, not by quietly disabling the old detector.

MC brief artifacts, learning lines, findings spool, delivery ingress and follow-through are not all “old alerts”; their continued product role must be mapped separately. This specification does not cancel them. Any future switch to an optional digest is a separate owner decision and preserves access to operational and learning information in MiniMoi.

### 12.4 Retention and runbooks

Prometheus15 d/2 GB; archive14 d/~500 MiB; per-process local events20 MiB plus snapshot1 MiB, aggregate local directory100 MiB/14 d. Total disk budget includes WAL, active files, indexes, images and filesystem overhead. Local diagnostic caps may drop data; original job/release/authority records remain governed by their existing backup policies.

Telemetry time-series/archives are operational diagnostics, not backed up by default. Rules, rendered nonsecret config, inventory and version pins are in Git. Losing Alertmanager persistent storage can cause repeat notifications; document this recovery limitation rather than promise exactly-once notices. Secrets are restored through their existing secure provisioning process.

One runbook per rule (shared runbook allowed for related rules): meaning, source/coverage caveats, read-only checks, supported repair, escalation, and verification of actual recovery. MC uses the same runbook/source references and its existing authority controls. No runbook grants blanket shell/SQL permission.

## 13. Security and trust boundaries

OTLP/metrics/UI endpoints are private by default. O1 receiver only accepts its trusted local-host emitter network. O2 cross-host traffic uses TLS, sender-specific authentication/identity binding, request size/rate limits and default deny; a specifically reviewed nginx route may proxy OTLP, never public Prometheus/Alertmanager/admin reload APIs. The old absolute “nothing through nginx” wording is superseded by this bounded exception.

The privacy catalogue limits *content*, not truthfulness: an authorized compromised emitter can supply false allowed numeric facts. The finite schema is not a general DLP guarantee against a fully compromised trusted emitter encoding information in allowed numeric values. Telemetry is not sufficient authorization for MC execution. Any product mutation still rechecks owner/grant/policy/target state and returns a verified receipt. MC cannot gain access by reporting an alarm or quoting a learned preference.

Source roots and credentials are separate from model-writable workspaces. No application account files, production environment files, cloud credentials or Docker socket are mounted for monitoring. Secret files are tested at the real UID; no plaintext secret in telemetry, template output, command line, config committed to Git or logs. Resource/scope/body validation applies to the archive as well as metrics; reject/strip before export, not after storage.

Document actual egress instead of calling a normal Docker network an allowlist. Place receiver request/connection rate limits at the trusted ingress if the selected receiver cannot enforce them. No trace/profiling input in O1. Minimum scrape metadata exposure is an explicit finite set reviewed with the emitted fixtures.

## 14. Failure and degraded modes

| Failure | Required behavior |
|---|---|
| Emitter dies | Independent expected identity remains; missing/stale alert fires while absent |
| Bridge hangs on source | Bounded source isolation; others still report; worker loss eventually heartbeat alarm; no unbounded thread spawning |
| Old healthy file | Retain original sample/event time; stale health/host source unknown; jobs/release read-availability exceptions do not assert current service health |
| Collector dies | Product API enqueues/drops without I/O wait; target alarm; local diagnostics bounded; external Watchdog is not claimed to detect this by itself |
| Prometheus/Alertmanager dies | External dead-man path detects absence; state restored before normal delivery claims |
| Telegram unavailable | Alertmanager retries; notification failure metrics/target-specific rules added and tested at activation; external heartbeat uses an independent provider/path |
| Local disk slow/full | Producer latency remains bounded; worker can stall/fail but caller does not; byte caps and drop counters; no application storage sweep |
| Unknown schema/identity/canary | Refused/dropped or metadata stripped before every sink; fixed diagnostic code only |
| Cardinality/scrape cap hit | Whole scrape failure is visible; coverage unknown; recent-limit alert clears after its window |
| Secrets/source unreadable | Service/config or source explicitly unavailable; never zero/healthy; no auto-chmod |
| Clock ahead >60 s | Invalid/unknown observation; never masks missing heartbeat; host clock checked separately |
| Config invalid | CI rejects; reload leaves old active config; recorded active hash unchanged |
| Restart/lost diagnostic state | Restore compatible persistent history; expose possible duplicate notifications/data loss; no exactly-once inference |

## 15. Adopting another MiniMoi component

Add an exact service identity and finite metric catalogue, name its source producer/schema/cadence, assign read/ingress identity, calculate cardinality, and implement library emission. Add rules/runbook and expected inventory only with its rollout. Run privacy, missing-data, boundedness and complete user-journey acceptance. Document coverage honestly in Operate/MC. Retire an old condition only through §12; no separate custom monitoring loop is introduced.

## 16. Future direction: connecting tools

**The design keeps three things stable when a tool is connected:**
- the telemetry: OTel, unchanged;
- the rules: the Prometheus format, the source of truth in git;
- the alert keys and severities (§5.4).

**OTLP instrumentation stays stable. Each new backend still needs reviewed configuration, identity mapping and tested rule/routing adaptation; configuration-only migration is not guaranteed.**

### 16.1 Grafana (OSS or Grafana Cloud)

**Data path:**
- **Metrics:** `prometheusremotewrite`, or `otlphttp` to Grafana Cloud's OTLP gateway [R20].
- **Logs and events:** `otlphttp` to Loki.
- **Traces (O4):** `otlphttp` to Tempo.

**Alerting:**
- Grafana Mimir and Prometheus evaluate the same `rules/*.yaml` unchanged (ruler API or `mimirtool rules sync`).
- Grafana-managed alerting is a separate rule model; an explicit tested migration is required.
- Alertmanager routing moves to Grafana's Alertmanager (the same format).

**Dashboards:** stored as JSON in `config/observability/dashboards/` and provisioned from git.

### 16.2 Splunk (Enterprise/Cloud, Observability, ITSI)

**Data path:**
- **Collector:** Splunk's OpenTelemetry Collector distribution [R21], or the contrib `splunk_hec` exporter for events and logs.
- **Metrics:** Splunk Observability over OTLP.

**Alerting:** Splunk detectors or ITSI correlation searches, translated from the rules. Keep:
- the `minimoi_alert_key` as the episode or grouping key;
- the severity mapping (§5.4).

**The proposed O3 translation must be scripted and tested** against the same synthetic series used in §9.2, so parity can be proven.

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
2. If the tool's UI edits rules, UI editing is disabled in production unless a reviewed import/reconciliation procedure preserves approval before deployment. Exporting after a live change is not equivalent to prior approval.
3. Run a **parity test** before switching notification ownership: the same synthetic failures (§11 V5–V7) must fire the same alerts with the same keys and severities.
4. Run in parallel for 14 days.

## 17. Build sequence, owners and decisions

### 17.1 Continuous lanes

| Increment | Claude Code | Codex | Completion gate |
|---|---|---|---|
| O1a contracts/base | Reconcile existing O1 library/bridge/rules with this revision; add tests/runbooks/rendering | Publish spec revision and review; confirm source/deployment interfaces | Independent diff review; no activation |
| O1b dev installation | Review Compose/source contracts; repair library findings independently | Compose, minimal image, source mounts, synthetic/dev installation | Clean config/pipeline/privacy/resource checks; only reviewed pieces enter shared dev |
| O1c first adopter | Add disabled-by-default standing-check telemetry, preserve existing outputs | Integrate reviewed adapter and coverage read path | Actual dev facts and failure/recovery; no production destinations |
| O2 production | Independent review and verification | Exact approved release, secret/TLS/source integration, Operate | Owner release approval; safe production checks; explicit coverage evidence |
| O3 backend | Adapter/rule parity work after choice | Deployment integration | Owner backend decision and tested parity |
| O4 tracing | Per-component instrumentation | Transport/query/capacity | Separate privacy/sampling/performance review |

The v0.3 rule/config contracts can be built immediately in the existing authorized lanes. Claude re-review is a gate to adopting this candidate and integrating its changed pieces, not a reason to halt unrelated MC development. New numeric defaults and tradeoffs are attributed proposals until reviewed/adopted. Codex remains the single shared-dev operator. Respect the host-wide heavy-job lock; reserve MC verification resources first, run monitoring build/tests when the lock is available. Do not start parallel Docker builds on the8 GB host.

### 17.2 Tonight's agreed priorities and limits

1. MC usability work remains first: close relevant review findings, prove runtime/action binding, wire reviewed domain/learning pieces, then exercise real dev conversation/clarification/action and failure/recovery journeys. A passing monitoring test suite does not satisfy MC acceptance or learning acceptance.
2. Finish this spec revision for Claude re-review, then O1 Compose/dev work once the relevant implementation changes are reviewed. Fake local alert receiver and blackhole routing let dev work proceed without Robert creating accounts tonight. Mark real Telegram/external-service checks pending; never label the whole production alert path verified.
3. Claude reviews the existing planning import inventory; Codex completes the private import/restore verification only after that clearance. The66 public planning files stay public for now. Their later migration is a separate reviewed PR, not tonight's prerequisite.
4. No main merge or production deployment tonight. Tomorrow's new-feature trial is on **dev**, unless Robert separately approves an exact production release. If runtime or journey gates remain open, report the specific unverified capability rather than claiming MC is ready from unit counts.

Already-pushed MC repair references (not observability deliverables): `a871adb7` learning-store repairs; `ed6d53a7` actions/grants; `5580050b` transport/credential/error semantics. Claude reviews these actual deltas plus S3a-7 `6b2bbb4b`. RC9-F1 still needs native initiator/hook proof: pinned OpenClaw plugin factory context has no independent runId, so a made-up ctx.runId comparison is not a solution. The full S0 gate remains separate.

### 17.3 Owner decisions

| ID | Current standing / proposed choice | When required |
|---|---|---|
| D1 | Standard-tool telemetry direction and parallel base build are owner-directed. This v0.3 repair design is a reviewer-proposed candidate for Claude re-review | Build can continue; changed implementation needs review before integration |
| D2 | Existing disk80/95%, memory85/95%, guest48 h thresholds retained. New timing/defaults and critical+major routing are proposed; do not ask Robert to reapprove unchanged watchlist facts as if lost | Final notification activation decision identifies only additions/changes |
| D3 | healthchecks.io remains a candidate external dead-man service; account, recipient and1 m/5 m timing not assumed approved | Before real external heartbeat acceptance/activation; fake dev harness now |
| D4 | Existing MC brief/learning/delivery behavior retained. No adoption of “optional off by default” by this revision | Any later workflow retirement/default change |
| D5 | Mac-to-EC2 authenticated TLS forwarding remains O2 design | Before cross-host activation |
| D6 | Dedicated Telegram alert bot remains proposed; dev routes stay blackhole/fake until provisioned | Before real Telegram activation |
| D7 | Exact reviewed production release | Required separately; no production changes tonight |

None of the external-account choices is needed for Robert to sign off tonight. The implementation owners keep moving and record limitations; they do not silently turn missing accounts into approvals or test passes.

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


**Additional pinned implementation references used in the v0.3 repair design:**
- [Collector filter processor0.162.0](https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector-contrib/v0.162.0/processor/filterprocessor/README.md).
- [Collector transform processor0.162.0](https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector-contrib/v0.162.0/processor/transformprocessor/README.md).
- [Collector Prometheus exporter0.162.0](https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector-contrib/v0.162.0/exporter/prometheusexporter/README.md).
- [Collector internal telemetry](https://opentelemetry.io/docs/collector/internal-telemetry/).

Live documentation is guidance; the pinned container's configuration validation and direct-ingress acceptance remain authoritative implementation checks.
