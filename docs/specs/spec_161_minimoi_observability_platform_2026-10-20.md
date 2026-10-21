# MiniMoi observability platform: specification v0.1

**October 20, 2026 · Claude Code for Robert · platform-wide (not MC-specific)**

**Status: draft v0.1 for two reviews; authorizes no build.** It authorizes no code, container, credential, deployment, external service, merge or production change. It replaces the MC-only design note `OBSERVABILITY_TELEMETRY_DESIGN_v0.2_2026-10-20.md` (INIT-2026-0007/mc-onboarding/design), which is now its input.

**Bar** (Robert, 20 Oct): *"a production quality solution with engineering principles that stand up to review. That's the standard, whatever the solution in whatever area for minimoi."*

## In plain words

MiniMoi has many moving parts:
- **on EC2:** the portal, Curator, German and Portuguese, the Chief of Staff, Guild/MC, the model gateway and Postgres;
- **on the Mac:** scheduled jobs, the Vault sync and backups;
- **soon:** the delegation runner.

Today each part watches itself differently. Some send their own Telegram messages, some write a status file, and some aren't watched at all. Thresholds are written into code, so changing an alarm means changing code.

**This spec makes observability one reusable platform capability.** Robert's words:

> We put lots of them on different parts of the solution. Some tool listens or consumes them. In that tool, where we build it or use another, the thresholds, alarms etc are set up. Otherwise we are opening up code all the time.

**How it works:**
1. **Every part reports facts** through one small shared library: numbers (metrics), things that happened (events) and, later, request timings (traces).
   - It uses the open standard **OpenTelemetry**, so no vendor is built in.
   - Facts look like "disk 83% used" or "backup finished 03:12, exit 0", never "disk is bad".
2. **One relay (the OpenTelemetry Collector)** forwards those facts to whatever tool is chosen: Grafana, Splunk, Netcool, or a file, by configuration.
3. **Thresholds, alarms and notifications live in reviewed configuration files,** in the standard Prometheus rule format, with automatic tests. Moving from today's small built-in evaluator to a full tool needs no code change.
4. **The monitoring watches itself.** If it goes quiet, an independent check raises the alarm.
5. **Nothing private ever leaves** in telemetry: no message text, names, prompts or secrets.

**Honest limits:**
- The first phase covers what already exists: job status, service health, host headroom, deployments and guest-request counts.
- Request and model timings and traces come later.
- Choosing a commercial tool is Robert's later decision. Until then a small, standard, open-source evaluator (Prometheus and Alertmanager) sends Telegram alerts.

## 1. Goals and non-goals

### Goals

| ID | Goal |
|---|---|
| G1 | **One way to instrument** any MiniMoi component, in Python and in shell-run jobs, through a shared library and documented conventions |
| G2 | **Vendor neutrality:** OpenTelemetry out; backends swapped by Collector configuration only |
| G3 | **Operations owns judgement:** thresholds, severities, routing and silences are reviewed config, tested in CI, and never in application code |
| G4 | **Actionable alerting:** deduplicated, open and clear, a runbook for every alert, and notifications only for problems that need a person |
| G5 | **Self-monitoring** through an independent dead-man's switch |
| G6 | **Privacy and security by construction:** closed vocabularies, an allowlist at the Collector, authenticated transport |
| G7 | **The product never depends on telemetry:** emission is non-blocking and bounded, and every signal has a local copy |
| G8 | **Reusable adoption:** a per-component checklist so any new feature or job is observable on day one |

### Non-goals (v0.1)

- **Choosing or paying for a commercial backend.** That is O3, Robert's decision.
- **Full APM or tracing for every request.** That is O4.
- **Replacing existing alerts on day one.** They run in parallel until parity is proven (§12).
- **Any AI model in the monitoring path.** Agents such as MC or CoS may *read* the data; they never evaluate alerts or act on them.

## 2. Standards adopted

| Concern | Standard / practice | Why |
|---|---|---|
| Telemetry model and transport | **OpenTelemetry** (OTLP), with the OTel semantic conventions for names | The CNCF vendor-neutral standard; supported by Grafana, Splunk, IBM, Datadog and Elastic |
| Metric naming | **OpenMetrics/Prometheus** conventions (base units, `_seconds`, `_ratio`, `_total`) | Portable naming that every backend understands |
| Alert rules | **Prometheus alerting-rule format**, unit-tested with `promtool test rules` | The most portable rule language (Grafana, Mimir, Thanos, Cortex), with first-class tests |
| Notification | **Alertmanager** (grouping, deduplication, silences, inhibition, open/clear) | The reference implementation for that rule format; Telegram receiver built in |
| Event model | **Open/clear with a stable key** (the Netcool Identifier/Severity model) and OTel log SeverityNumber | Lets any event tool deduplicate and auto-clear |
| What to measure | **Golden signals / RED** for request paths; **USE** for hosts; **freshness** for batch jobs | Recognized coverage models |
| Alert hygiene | **SRE practice:** alert on symptoms; page only on actionable items; a runbook per alert | Avoids alert fatigue |
| Self-monitoring | A **dead-man's switch** (an always-firing Watchdog that a separate service expects) | Detects a total pipeline failure |
| Change control | **Monitoring as code:** rules and dashboards in git, validated in CI | Reviewable, reversible, testable |

## 3. Principles and how they are met

1. **Facts in code, judgement in config.** The library offers no API that takes a threshold. A review checklist rejects code that emits "degraded" for a numeric value.
2. **One path out.** Components emit OTLP to the Collector. The library hides the transport, so a component never names a vendor.
3. **Never a product dependency.**
   - Emission happens on a background exporter with a bounded queue (default 2,048 items).
   - On overflow it drops and increments `minimoi_telemetry_dropped_total`.
   - The product thread never waits.
   - The **local copy** (status file or JSON lines) is written first.
4. **Privacy.**
   - Attribute keys and values are validated against closed vocabularies in the library; free text is refused.
   - The Collector applies an attribute **allowlist** as defence in depth.
   - Tests prove that message text, emails, names, paths and tokens are rejected.
5. **Security.**
   - OTLP listens only on the private Docker network, plus loopback on the Mac.
   - Anything crossing hosts uses TLS with a per-sender bearer token, stored as a secret file (0600) and never in the environment of model-running processes.
   - Backend credentials live only in the Collector.
   - The containers are pinned by digest, run non-root with a read-only rootfs, drop all capabilities, and have memory limits.
6. **Actionable alerts.** Every rule carries:
   - `severity`;
   - `summary`;
   - `runbook_url`;
   - a `for:` duration;
   - `minimoi_alert_key`.

   Warnings go to dashboards only. Critical alerts notify, and the cleared notice is sent too.
7. **Self-monitoring.** The Watchdog goes to an external dead-man's switch. Separately, the existing Mac jobs watchdog checks that the EC2 pipeline's heartbeat file is fresh.
8. **Bounded cardinality.**
   - Labels come only from fixed sets.
   - No per-request, per-user or free-text labels are allowed.
   - The Collector enforces a series cap (default 2,000 per environment) and alerts on overflow.
9. **Versioned contracts.**
   - `minimoi.telemetry.schema_version` is carried on every signal.
   - A breaking rename runs old and new names side by side for one release.
10. **Tested end to end.**
    - library unit tests;
    - `promtool` rule tests;
    - an `amtool` config check;
    - a pipeline test (emit, Collector, file exporter, assert);
    - failure-injection tests (§9).

## 4. Signals and conventions

### 4.1 Resource attributes (every signal)

| Attribute | Value |
|---|---|
| `service.name` | `minimoi-<component>`, e.g. `minimoi-portal`, `minimoi-curator`, `minimoi-cos-scheduler`, `minimoi-standing-checks`, `minimoi-mac-jobs` |
| `service.version` | git sha or image tag |
| `deployment.environment` | `dev` or `prod` |
| `host.name` | the host |
| `minimoi.telemetry.schema_version` | `1` |

### 4.2 Metric naming

**Form:** `minimoi_<area>_<thing>_<unit>`, for example `minimoi_job_last_success_timestamp_seconds`.

**Rules:**
- **Types:** use gauges for states and timestamps, counters (`_total`) for events, and histograms for durations (O4).
- **Units:** keep them in the name, never in labels.
- **Labels:** every label key must be registered in `config/observability/vocabulary.yaml`, with its allowed values or pattern.

### 4.3 Event schema `minimoi.event/1` (OTel log records)

**Fields:**
- `event.name`;
- `minimoi.alert_key` (stable, e.g. `job:backup-s3`);
- `minimoi.state`: `opened`, `cleared` or `info`;
- `severity_number`;
- `minimoi.component`;
- `minimoi.summary_code` (closed vocabulary);
- `observed_time`.

**Code emits events only for facts that are binary by nature:**
- a job exited non-zero;
- a source could not be read;
- a deploy failed;
- a component restarted.

Threshold conditions become alerts from rules, not from code.

**Severity mapping:**

| MiniMoi | OTel SeverityNumber | Netcool | syslog |
|---|---|---|---|
| clear | 9 INFO | 0 Clear | info |
| warning | 13 WARN | 2 Warning | warning |
| major | 17 ERROR | 4 Major | err |
| critical | 21 FATAL | 5 Critical | crit |

### 4.4 Traces (O4)

- **Propagation:** W3C `traceparent` across portal → service → gateway → runner.
- **Content:** span attributes carry IDs and timings only.
- **Sampling:** 10% head sampling, plus 100% of error traces through tail sampling in the Collector.

## 5. Architecture

```
 components (shared library: core/observability)        Mac jobs (status files)
   │ local copy first (status file / JSON lines)                │
   └──OTLP (private net)──▶ OpenTelemetry Collector ◀──filelog / status bridge
                              │  processors: memory_limiter, allowlist, series cap, batch, file_storage queue
                              ├─▶ file exporter (rotated; always on)
                              ├─▶ prometheus exporter ─▶ Prometheus (rules) ─▶ Alertmanager ─▶ Telegram
                              │                                                └─▶ dead-man's switch (external)
                              └─▶ later: Grafana (Mimir/Loki), Splunk (HEC), Netcool (webhook/OTel), …
 readers of the same data: Guild Operate tile, MC/CoS (read-only), dashboards
```

### Components

| # | Component | What it is |
|---|---|---|
| 1 | **`core/observability/` library** (new; Python) | **Functions:**<br>• `gauge(name, value, **labels)`;<br>• `count(name, n=1, **labels)`;<br>• `event(alert_key, state, severity, summary_code, **labels)`;<br>• `heartbeat()`.<br>**Behaviour:**<br>• validates against the vocabulary;<br>• writes the local copy;<br>• exports through the OTel SDK on a background thread;<br>• is a safe no-op when telemetry is disabled.<br>**Also:** a tiny CLI, `minimoi-telemetry`, for shell jobs. |
| 2 | **Status-file bridge** | Turns existing job status files (`config/scheduled_jobs.json` and the status contract) into metrics, so existing jobs need no change. |
| 3 | **Collector** | `otel/opentelemetry-collector-contrib`, pinned by digest. |
| 4 | **Prometheus + Alertmanager** | Pinned versions. They evaluate `config/observability/rules/` and route through `config/observability/alertmanager.yaml`. |
| 5 | **Config folder** `config/observability/` | • `vocabulary.yaml`;<br>• `rules/*.yaml`;<br>• `rules/tests/*.yaml`;<br>• `alertmanager.yaml`;<br>• `collector.yaml`;<br>• `dashboards/*.json`. |
| 6 | **Runbooks** | `docs/runbooks/<alert_key>.md`, linked from every rule. |

## 6. Rules and change control

**A threshold change is a pull request to `config/observability/`.**

**CI runs:**
- `promtool check rules`;
- `promtool test rules`;
- `amtool check-config`;
- a vocabulary check, so every label in a rule exists;
- a runbook-link check.

**Deployment:** the change reloads Prometheus and Alertmanager without restarting them. No application image is rebuilt.

**If a GUI tool is adopted later:**
- git stays the source of truth;
- GUI edits are exported back, or the GUI is made read-only for rules.

## 7. Detection targets (defaults; Robert confirms)

| Condition | Detect within | Severity |
|---|---|---|
| Service down | 5 min | critical |
| Daily job missed | 2 h after its window | critical |
| Job stuck running | as `stuck_after_s` in the job registry | major |
| Disk ≥ 95% / ≥ 80% | 10 min / 30 min | critical / warning |
| Memory ≥ 95% / ≥ 85% | 10 min / 30 min | critical / warning |
| Deploy failed | 5 min | major |
| Guest request waiting > 48 h | 1 h | warning |
| Monitoring silent | 15 min | critical (independent path) |
| Telemetry dropped or series cap hit | 15 min | warning |

These values come from MC's approved watchlist and the job registry. They now live as rules, not in code.

## 8. Self-monitoring

**The Watchdog rule** fires always and goes to an external dead-man's switch. The service is Robert's choice, for example healthchecks.io or Grafana OnCall. It alerts if the heartbeat stops (Prometheus, Alertmanager, the Collector or the host down, or notifications broken).

**A second, independent path:** the Mac's 09:00 jobs watchdog also checks that EC2's heartbeat is fresh.

## 9. Failure modes (each is a test)

| Failure | Required behaviour |
|---|---|
| Collector down | The library drops after its bounded queue and counts drops; the product is unaffected; the local copy continues; heartbeat absence alerts |
| Backend down | The Collector `file_storage` queue holds up to 24 h, then drops the oldest and counts it |
| Disk full | The library never blocks; the local copy fails closed; product paths unaffected |
| Clock skew | NTP on hosts; a skew metric (O2); rules tolerate skew |
| Bad rule or config | Rejected in CI; the running config is unchanged |
| Notification channel down | Alertmanager retries; the dead-man's switch catches total failure |
| Content leak attempt | The library refuses it, the Collector drops it, and a test proves both |
| Cardinality explosion | The series cap drops excess and alerts |

## 10. Adoption checklist (for every component, new or existing)

1. Register its `service.name`, plus any new label keys and values, in `vocabulary.yaml`.
2. Emit facts through `core/observability`: liveness, last success, errors and, for request paths, the RED metrics.
3. Write the alert rules and their `promtool` tests in `config/observability/rules/`.
4. Write one runbook per alert.
5. Add or update a dashboard (optional in O1).
6. Retire any hand-rolled Telegram alerts for the same condition **after** parity is shown (§12).
7. Pass the privacy test: no content in labels or events.

## 11. Phases and ownership

| Phase | Deliverable | Owner | Gate |
|---|---|---|---|
| **O1 Foundation** | `core/observability` library + CLI; vocabulary; status-file bridge; rules + tests for jobs, services, host, deploy and guests; local Prometheus + Alertmanager + Telegram; dead-man's switch; runbooks | Claude Code: library, bridge, rules, tests, runbooks.<br>Codex: Collector, Prometheus and Alertmanager containers, compose, CI job | Dev end-to-end test; failure-injection suite; Robert approves the §7 targets |
| **O2 Production** | The O1 stack on EC2 prod; persistent queue; skew metric; Guild Operate tile reads the same data | Codex deploys; Claude Code reviews | Prod evidence; rollback rehearsed; parallel run with existing alerts |
| **O3 Tool choice** | Backend selected; exporters configured; rules and dashboards imported | Robert decides; both implement | Parity test: the same synthetic failures fire the same alerts |
| **O4 Requests and traces** | RED metrics and traces in portal, cos-scheduler, gateway and runner; model latency and cost metrics | Both lanes, per component | Cardinality and privacy review per component |

**First adopters:**
- MC's standing checks (MC 1.2 package P), already built as bounded, read-only checks;
- the scheduled-jobs registry;
- the existing health and host snapshots.

## 12. Migration from today

**What exists today:**
- the Curator's delivery ledger and Telegram;
- guest and health nudges;
- the Mac jobs watchdog;
- the Operate "Scheduled jobs" tile;
- MC's standing brief design.

**How they move:**
1. They keep running. The new alerts run **in parallel** until each condition shows parity over 14 days.
2. Only then is a duplicate alert retired, one condition at a time, each with Robert's approval.
3. MC's planned 05:40 brief becomes an **optional digest** rendered from the same data. Its thresholds move to rules, and its site chat-posting path is not needed.

## 13. Decisions for Robert

1. **Direction:** facts in code, judgement in config, OTel plus Prometheus-format rules as the standard.
2. **The §7 detection targets.**
3. **A dead-man's-switch service:** a free tier is enough.
4. **The MC daily digest:** keep it optional, or drop it.
5. **Where the Collector and evaluator run:** on EC2 only (Mac jobs report through the bridge), or also on the Mac.

## 14. Review plan

**Codex:** platform fit, containers and compose, CI, the Operate tile, and whether the bridge is a Collector receiver or a sidecar.

**Second reviewer** (Claude Chat or Grok), on industry alignment:
- rule portability to Netcool and Splunk;
- the sampling, retention and cardinality defaults;
- the self-monitoring design.

**Then:** v0.2 with the review changes, followed by Robert's approval. Building starts only after that.
