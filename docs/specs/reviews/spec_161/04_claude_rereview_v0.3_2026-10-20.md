# Spec 161 v0.3: Claude Code re-review

**20 October 2026 · reviewer: Claude Code (Opus 5.5) · author of v0.3: Codex · review only, no edits to Codex's branch or worktree**

| Input | Ref | Verified |
|---|---|---|
| Spec v0.3 | `origin/codex/spec161-review` @ `fc5d82d8` | SHA-256 `53953e9d…01745636`, matches the expected value |
| Spec v0.2 (predecessor) | `cc72288c` | diff v0.2 to v0.3: 417 insertions, 762 deletions |
| Codex review of v0.2 | `5b4b765d:…/02_codex_review_v0.2_2026-10-20.md` | read |
| Re-review request | `…/03_codex_revision_v0.3_claude_rereview_2026-10-20.md` | read (five priorities) |
| Rule fixtures | `evidence_v0.3/rules.yaml`, `acceptance-tests.yaml` | run with promtool, see §1 |
| O1 build being compared | `origin/claude/observability-o1` @ `f9a03753` | read in full: core/observability, config/observability, workflow, requirements, runbooks |

Attribution: Codex authored v0.3 and its fixtures. Every finding, scenario and recommendation below is Claude's. None of it is an owner decision.

## Verdict

**Make changes first, then send to the second reviewer.** v0.3 is a real improvement on v0.2. The independent expectation inventory, the source-time contract, the fail-closed `minimoi_source_current`, scoped identity joins and the honest-evidence language all hold up. The repaired v0.2 bugs (stuck timing based on the previous finish time, a healthy host masking another host, one absent exporter family erasing the other) are fixed, and my own promtool scenarios confirm it.

Four problems should be fixed before a second reviewer spends time on the spec:

- **CR3-F1, F2:** missing-data alerts reach nobody under the proposed routing, and losing the bridge makes every job report "never ran".
- **CR3-F3:** the Collector privacy fragment contains exporter keys I believe are invalid for the contrib exporter.
- **CR3-F4:** the 3-minute JobFailed detection budget is arithmetically unreachable.

None of these needs a redesign. Each fix is a rule, routing or config change of a few lines.

## 1. Tool evidence (exact output)

Tools: promtool 3.15.0 (revision `5241a27f`) and amtool 0.34.1, checksum-verified copies in the scratchpad. No otelcol binary was available, so **no Collector config was validated by me**.

| Command | Result |
|---|---|
| `promtool check rules evidence_v0.3/rules.yaml` | `SUCCESS: 26 rules found` (25 alerts + 1 recording rule, matching Codex's claim) |
| `promtool test rules evidence_v0.3/acceptance-tests.yaml` | `SUCCESS`: 5 test groups. Only 1 is an `alert_rule_test` (TelemetryEmitterMissingOrStale). The other 4 are `promql_expr_test`s that **copy** the rule expression, so they can drift from the rule file |
| `promtool test rules cr3_scenarios.yaml` (Claude, 9 groups, 12 assertions) | `SUCCESS`: all 9 pass. 7 groups demonstrate defects in the current rules (S1–S6, S9). 2 confirm intended behaviour (S7 fail-closed max-age, S8 stale host snapshot gates DiskCritical) |
| For comparison: O1 `promtool check rules rules/minimoi_o1.yaml` / `test rules` | `SUCCESS: 18 rules found` / `SUCCESS` |
| amtool | Not run on v0.3. There is no rendered Alertmanager file, only a fragment in §9.3 that ends at `receivers: - name: blackhole` |

The scenario file is `/private/tmp/claude-501/-Users-vanstedum-Projects-personal-ai-agents/835a3ae3-8150-473b-91dd-3588e113c34b/scratchpad/rr3/cr3_scenarios.yaml`. Each scenario asserts what the v0.3 rules **currently** do, so a passing test demonstrates the finding.

Rule coverage today: 1 of 25 alerts is tested through `alert_rule_test`. My scenarios add direct tests for JobNeverRan, TelemetryDropping, JobStatusUnreadable, JobFailed, JobNoSuccess, MonitorTargetUnavailable, SourceUnavailable, DiskCritical and ScrapeLimitHit. That leaves 15 alerts without any direct test.

## 2. Findings

P1 means the stated invariant fails or the spec makes a false claim. P2 means it must be fixed before dev activation. P3 is quality.

### CR3-F1 (P2, §9.1/§9.3): missing-data alerts never notify anyone

**Problem.** Under the proposed routing (critical and major notify, warning stays in Prometheus/Operate), every coverage alert except the emitter heartbeat is a **warning**: SourceUnavailable, JobStatusUnreadable, ServiceObservationMissing, CheckObservationMissing and CheckSourceUnreadable. Scenario S8 shows the consequence. When the health or host producer stops while the bridge stays alive:

- DiskCritical and ServiceDown are correctly suppressed.
- Only a warning remains, and it is never delivered.

A real outage that coincides with producer loss can therefore stay silent for as long as the producer is down. That reopens Codex F1 at the notification layer.

**Fix.** Escalate source loss when it hides a critical rule. Add a second rule, SourceUnavailableProlonged (severity `major`), which fires on `minimoi_source_expected unless minimoi_source_current` for 2× the source max-age, for the `services` and `host` sources at minimum. Alternatively, Robert approves a daily warning digest (owner choice O-5 below). State in §9.3 which coverage losses notify.

### CR3-F2 (P2, §9.1 JobNeverRan/JobStatusUnreadable, §14 "Emitter dies"): bridge loss causes a storm and wrong claims

**Problem.**

- `JobNeverRan = first_due < time() unless status_readable == 1` is not gated on emitter or source presence.
- **S1:** when the bridge stops, every job with a past `first_due` fires JobNeverRan (major), and every job also fires JobStatusUnreadable (warning).
- **S2:** when a long-running job's status file becomes corrupt (readable=0), it is reported as "never ran". This matches the old `judge.py` conflation, but the Prometheus version adds the bridge-down variant, which `judge.py` could never produce.
- **S4:** a job that is not yet due and has no status file raises JobStatusUnreadable after 30 minutes.
- The only inhibit rule has `equal: [..., minimoi_alert_key]`, so in practice it only pairs disk and memory alerts. It does nothing to collapse a storm.

**Fix.**

- (a) Gate JobNeverRan, JobStatusUnreadable, ServiceObservationMissing and CheckObservationMissing with `and on(environment,host,service_name) (minimoi_source_current{source_id="<src>"} == 1)`, so their absence is explained by SourceUnavailable or emitter-missing instead.
- (b) Add `unless on(...) (minimoi_job_first_due_timestamp_seconds > time())` to JobStatusUnreadable.
- (c) Either emit a distinct fact `minimoi_job_status_present` (0 only for ENOENT), so "never ran" means the file is absent rather than unreadable, or rename the alert to JobNoReadableRunAfterDue.
- (d) Add an inhibit rule in which `alertname=~"TelemetryEmitterMissingOrStale|SourceUnavailable"` inhibits `severity=~"major|warning"` with `equal: [environment, host, service_name]`.
- Add S1, S2 and S4 as regression tests that should pass **quiet** after the fix.

### CR3-F3 (P2, §10.2 exporter fragment): likely invalid Collector keys

**Problem.** Under `exporters.prometheus`, the fragment sets `without_scope_info: true` and `resource_constant_labels: {included: [...]}`. I cannot confirm that either key exists in the contrib `prometheusexporter` at 0.162.0. They resemble the OpenTelemetry declarative-config fields for the **internal-telemetry** pull exporter (`without_scope_info`, `with_resource_constant_labels`), which v0.3 also uses correctly under `service.telemetry`. If they are not valid exporter keys, `otelcol-contrib validate` fails with "invalid keys".

**Fix.** Use the known-good pattern:

- `transform` with `keep_keys(resource.attributes, ["service.name","deployment.environment.name","host.name"])`, before export;
- `resource_to_telemetry_conversion: {enabled: true}`;
- Prometheus `metric_relabel_configs` that drop `otel_scope_.*` labels and the `target_info` series.

Prove the exact keys with `validate` on the pinned image before the spec states them as fixed settings.

**Other items I cannot confirm for 0.162.0**, each to be proven by `validate` plus a direct-ingress test:

- `translation_strategy: UnderscoreEscapingWithoutSuffixes` (I believe it exists in recent versions, replacing `add_metric_suffixes`).
- OTTL paths `scope.*` versus the older `instrumentation_scope.*`, `resource.schema_url`, `scope.schema_url`, `log.event_name`.
- Whether `datapoint.exemplars` can be cleared or tested with `Len()`.
- Whether the `batch` processor is still present or has been superseded by exporterhelper batching.
- The internal metric names `otelcol_exporter_send_failed_{metric_points,log_records}_total`.

**Confirmed to the best of my knowledge:** filter and transform processors with `error_mode: ignore|silent|propagate`; `keep_keys`, `set`, `delete_key`, `Len`, `IsMatch`; `memory_limiter` with `limit_mib`/`spike_limit_mib`/`check_interval`; otlp/http `max_request_body_size`; the `file` exporter `rotation` keys; `health_check`.

### CR3-F4 (P2, §9.1 table, V5): JobFailed "≤3 m" cannot be met

**Problem.** The worst-case path from a status-file change to firing is the sum of:

| Step | Worst case |
|---|---|
| Bridge poll | 60 s |
| SDK `PeriodicExportingMetricReader` | 60 s |
| Scrape | 30 s |
| Rule evaluation | 30 s |
| `for: 1m` | 60 s |
| **Firing** | **≈ 240 s** |
| Alertmanager `group_wait` before notification | + 30 s, ≈ 270 s |

V5 measures from "bridge-visible", which removes the poll and still leaves about 210 s.

**Fix.** Either set the bridge's metric export interval to 15 s (cheap, single process), which gives about 2.5–3 minutes from bridge-visible, or restate the bound as ≤ 5 minutes. Apply the same arithmetic to every row of the table. The emitter (8 m 30 s) and scrape-down (3 m) bounds are correct.

### CR3-F5 (P2, §9.1 TelemetryDropping, §7.2): first drops in a new process are invisible

**Problem.** The OTel SDK creates a counter series on its first `add()`. `increase()` needs two samples, so a counter whose first sample is already 40 gives 0 (scenario **S3**). The most interesting drops are startup and backpressure bursts in a fresh process, and those are never alerted.

**Fix.** The library pre-creates every `(signal, reason)` combination at 0 when telemetry is enabled: 2 × 5 = 10 series, which stays within budget. Add S3 inverted (should fire) as a regression test.

### CR3-F6 (P2, §10.3 and MonitorTargetUnavailable): identity labels on `up` are not guaranteed

**Problem.** The rule joins on `(environment,host,job,instance)`, but `up` only has target labels. If the application scrape omits environment/host target labels (which §10.3's "do not apply the monitoring host label to remote emitters" points toward), the alert fires forever (scenario **S6**). If they are present, a naive `metric_relabel` can overwrite emitter identity.

**Fix.** Specify that every scrape job carries static target labels `environment`, `host` and `service_name=minimoi-observability`. Application identity is then set by `metric_relabel_configs` with `replace` from `host_name`/`deployment_environment_name` using regex `(.+)`, which runs after target labels are attached and so wins for application samples. Add a test with two hosts' emitters behind one Collector, asserting that `up` matches the expectation and each emitter keeps its own host.

### CR3-F7 (P3, §9.1 self-monitor rules): missing identity labels on self-monitor alerts

**Problem.**

- CollectorExportFailing (`sum by (environment,host,job,instance)`) and ScrapeLimitHit drop or lack `service_name`.
- Watchdog has no environment or host at all.
- ScrapeLimitHit's key resolves to `self:cardinality:prometheus` (scenario **S9**), because the counter is global, not per target.

§9.3 says self-monitor alerts carry the deployment's explicit environment/host and `service_name=minimoi-observability`, but no rule does this.

**Fix.** Set Prometheus `external_labels: {environment, host}`, which are attached to alerts that lack them. Add `service_name` to the `sum by` and to the static labels of the self rules. Use the fixed key `self:cardinality`. Rely on `up==0` on the specific target to name the offender.

### CR3-F8 (P3, §9.1): duplicate notification on a first-run failure

**Problem.** A first-ever failed run fires both JobFailed and JobNoSuccess, two major notifications with different keys (scenario **S5**). `judge.py` returned one verdict ("failed") in this case.

**Fix.** `JobNoSuccess … unless on(…) (minimoi_job_failed == 1)`, or drop JobNoSuccess (see S-4 in §4).

### CR3-F9 (P3, §9.1 DeployNotFinished): permanent warning if the producer never verifies

**Problem.** Outcome 3 (unverified) older than 1 hour is a permanent warning. If the current release-status producer never writes a complete `verification` block, this warning is always on.

**Fix.** Before adopting the rule, record in §8.1 whether today's producer writes `verification.services[].ok`. Otherwise the warning becomes noise and trains people to ignore it.

### CR3-F10 (P3, §9.2): acceptance tests copy expressions instead of testing rules

**Problem.** Four of the five fixtures are `promql_expr_test` copies of the expressions, not tests of the rules themselves.

**Fix.** Convert them to `alert_rule_test` against `rules.yaml`, so a rule edit cannot leave a stale copy passing.

### CR3-F11 (P3, §5.2/§8, heartbeat freshness margin): little slack before pending flaps

**Problem.** Heartbeat age at evaluation can reach poll period plus loop work, plus up to 60 s reader delay, plus 30 s scrape, plus 30 s evaluation, which is about 200 s against a 180 s freshness bound. `for: 5m` prevents a false alert, but pending will flap.

**Fix.** Either use 240 s freshness, or specify that the worker heartbeat ticks every ≤ 30 s independently of the bridge loop. This is safe because bridge-loop hangs are already caught by the `jobs` `source_timestamp`, which is the poll-completion time.

### CR3-F12 (P3, document quality)

- (a) About 51 places lose the space between a word and a number ("for5 m", "promtool3.15.0", "contrib0.162.0", "mode0700"). This hurts readability in the spec and in the 03 handoff.
- (b) §16 cites "§5.4" three times for severity and alert keys. They are now in §5.3.
- (c) §7.4 says "retain the v0.2 CLI interface" but v0.3 no longer contains it. Either inline the synopsis or link the v0.2 commit section.
- (d) [R22] still has no URL.
- (e) §17.2 ("Tonight's agreed priorities", MC repair SHAs, planning import) is session coordination, not platform specification. Move it to the handoff, so the spec does not date on day one.
- (f) The runbook `docs/runbooks/source-unavailable.md`, referenced by SourceUnavailable and ServiceObservationMissing, does not exist yet (O1 has the other 14).

### Priority 1 summary: are F1 and F2 closed?

**F1 (silent loss).** Closed at the **evaluation** layer:

- The expectations are recording rules, independent of the Collector.
- The emitter rule handles a vanished series (Codex's test plus S1).
- Source time is original.
- `source_current` fails closed (S7).
- A stale snapshot gates its consumers (S8).

It is **not closed at the notification layer** (CR3-F1), and the failure mode is noisy and partly wrong (CR3-F2).

**F2 (privacy).** The design is right:

- validation before every exporter, with `error_mode: propagate`;
- resource, scope and body normalization;
- no debug exporter;
- raw-ingress canaries (V9).

The only concrete syntax shown is the exporter fragment, which has probable invalid keys (CR3-F3). No generated OTTL exists to review. F2 therefore stays open until `render_config.py` emits OTTL that passes `validate` and the V9 canary test. S-1 and S-2 below shrink that surface considerably.

### Caps on an EC2 host of about 4 GB

The 1 GiB total is a ceiling, not a reservation:

| Service | Cap | Expected actual use |
|---|---|---|
| Prometheus | 512 MiB | ≈ 150–250 MiB at ≤ 2k application series and ≤ 10k internal series; 256 MiB is likely enough |
| Collector | 256 MiB | ≈ 80–120 MiB RSS for contrib |
| Alertmanager | 128 MiB | < 40 MiB |
| Python bridge with SDK | 128 MiB | ≈ 50–80 MiB |
| **Total** | **1 GiB** | **≈ 350–500 MiB** |

Notes:

- With memory_limiter at 200/50 MiB, set `GOMEMLIMIT≈200MiB`.
- On a t3-class host, 0.6 CPU of caps is above the burst baseline but irrelevant at idle. Actual load is about 0.02–0.05 CPU.
- **It fits 4 GB only if measured free memory with all 8+ application containers running is at least about 1.2 GiB.**
- `docs/AWS_MIGRATION_PLAN.md` still records the host as `t3.small` (2 GB). At 2 GB it does not fit. Confirm the actual instance type (§10.6 step 1 already requires measurement).
- The 4 GiB disk reservation is reasonable. On the Mac, about 8 GB is free, so 4 GiB is half the remaining space; flag this to Robert.

## 3. O1 build (f9a03753) conformance delta

These are the code and config changes `claude/observability-o1` must make to conform to v0.3 with my fixes. They are listed precisely. Claude owns this lane; none of it has been made yet.

### `config/observability/vocabulary.yaml`
1. Add metrics `minimoi_job_last_start_timestamp_seconds`, `minimoi_job_failed`, `minimoi_job_has_success`, `minimoi_source_readable{source_id}` and `minimoi_source_timestamp_seconds{source_id}`. Add label `source_id` with values `[jobs, services, host, release, guests, standing]`.
2. Remove `minimoi_job_expected_interval_seconds`, `minimoi_job_missed_after_seconds` and `minimoi_job_stuck_after_seconds`. These become rendered recording rules and must not be accepted from OTLP.
3. `minimoi_telemetry_dropped_total` labels become `[signal, reason]`. `signal` is `[metrics, logs]` (drop `traces`). `reason` is `[queue_full, invalid_contract, series_cap, local_io, export_failure]`.
4. Add a per-metric `range` (ratio [0,1], binary {0,1}, outcome {0,1,2,3}, exit code [-255,255], timestamp ≥ 0 and ≤ now+60, count integer) and `nullable`.
5. Replace the `job_id` pattern with registry membership. Replace the `mountpoint` pattern (arbitrary paths) with a fixed token set, for example `[root, data]`.

### `core/observability/vocabulary.py`
6. Enforce item 4's ranges in `check_metric`, plus registry-sourced `job_id` membership.

### `core/observability/emitter.py`
7. **Disabled means a true no-op.** Currently the local copy is written when disabled (L112, L162) and the heartbeat thread starts regardless of `enabled` (L56–60).
8. **Move all file I/O off the caller.** `_local_metric` writes and `json.dump`s the whole snapshot under the lock on every `gauge()` call (L228–247). `_local_event` appends synchronously (L249–261). Route both through the worker queue, or drop the local copy (S-2).
9. If the local copy is kept, use a process-private directory (0700) under `MINIMOI_TELEMETRY_LOCAL_DIR` with `O_NOFOLLOW`, rotation at 5 MiB × 4, a 100 MiB aggregate janitor and a 64-directory cap. The current `metrics-latest.<service>.json` still collides between processes of the same service.
10. **Gauges via observable callbacks over a bounded latest map**, with 180 s expiry and per-source batch replace. The current synchronous `create_gauge` re-exports the last value forever, including stale `service_up` values and finish times that should be absent.
11. Cap active metric keys at 256 per process (reason `series_cap`).
12. **Pre-create all 10 `dropped_total` series at 0** (CR3-F5). `_drop(signal)` gains a reason.
13. **Heartbeat only on worker progress** (bridge: after a completed pass). Remove the free-running `_heartbeat_loop`, which masks a hung worker.
14. `host.name` comes from required `MINIMOI_HOST_ID` (inventory value). Remove the `os.uname().nodename` fallback (L73).
15. Shutdown has a total 2 s deadline across both providers. `logger_provider.shutdown()` currently has no timeout. `flush()` caps at 2 s.
16. Add encoded record cap 4 KiB and total queued 8 MiB.

### `core/observability/__init__.py`
17. Rename `OTEL_EXPORTER_OTLP_HEADERS_FILE` to `MINIMOI_TELEMETRY_HEADERS_FILE`. Open with `O_NOFOLLOW`, check UID and mode, and use bounded parsing.
18. In production, configuration errors must not raise. Today `configure()` raises on an invalid env or header-file mode. Instead, disable telemetry and print a fixed stderr code. Require an explicit endpoint when enabled, rather than a silent `http://otel-collector:4318` default.

### `core/observability/bridge.py`
19. **Stop calling `core.jobs.status.read_status`**, which does an unbounded `read_text` (status.py L157). Read with the bounded reader and validate through `status.build`-equivalent checks.
20. **`_read_json` must open with `O_NONBLOCK`** before the `S_ISREG` check. Today a FIFO at a source path blocks the bridge indefinitely (L42). Also reject duplicate keys and NaN/Infinity (`object_pairs_hook`, `parse_constant`), apply per-source size caps (§8.1), confine paths to a root, and apply a 3 s per-source budget.
21. Filter jobs with `Registry.for_host(<mac|ec2 mapping>)` intersected with the monitoring inventory. Currently all jobs are emitted.
22. Emit `job_last_start` from `started_at`. Emit `finished` only when present. **Remove the `finished_at or started_at` fallback** (L107). Emit `job_failed` and `job_has_success`.
23. Emit `source_readable{source_id}` and `source_timestamp_seconds{source_id}`:
    - services and host: original `sampled_at`;
    - jobs and release: completed-read time.
    Keep `bridge_source_readable` only if S-3 is not adopted.
24. Health: require `sampled_at`, a matching `environment`, and ≤ 32 services. An empty list is valid with coverage 1, not unreadable, if the schema allows it; decide with the producer. Host: fixed mount tokens, `sampled_at`.
25. Release: unverified becomes **3**, not 2 (L171). Leave `observed_at` unchanged.
26. Remove threshold emission (L90–92).
27. On a loop exception, count `reason=invalid_contract` or similar, and don't heartbeat on a failed pass.

### `core/observability/cli.py`
28. Flush deadline 2 s (L65), no-op when disabled, and a `--validate-only` subcommand with a nonzero exit on an invalid contract.

### `requirements-observability.txt`
29. Pin `PyYAML==<x>` and generate a hash-locked full transitive set (`pip-compile --generate-hashes`) covering opentelemetry-api/proto, protobuf, requests and so on. Install with `--require-hashes`.

### `config/observability/collector.yaml`
30. Remove the `file_storage` extension.
31. Add `translation_strategy: UnderscoreEscapingWithoutSuffixes`. **Today unit `'1'` gauges such as `minimoi_job_running` are most likely exported as `minimoi_job_running_ratio`**, so O1's rules would never match. Verify this with the live exporter either way.
32. Add `keep_keys` on resource attributes before `resource_to_telemetry_conversion`. Today any raw-OTLP resource attribute, including `service.version` and canaries, becomes a Prometheus label: both a privacy leak and a cardinality risk.
33. Assign `host.name` as well as environment.
34. Add filters for metric names, types and label membership; normalize scope, description and logs body; set `error_mode: propagate`.
35. Add `max_request_body_size: 1048576` and the internal telemetry pull reader on 8888.
36. If S-1 is adopted, delete the logs pipeline and `file/archive`. Otherwise use `max_backups: 9`.

### `config/observability/prometheus.yml`
37. Render the file (no `${MINIMOI_ENV}`). Use `external_labels: {environment, host}`.
38. Static target labels on every job (CR3-F6).
39. `metric_relabel_configs` for identity normalization, dropping `deployment_environment_name`, `host_name`, `otel_scope_*` and `target_info`.
40. Add `body_size_limit: 4MB` and `label_value_length_limit: 128`, plus a separate `sample_limit` for internal jobs.
41. `rule_files` include the generated expectations directory.

### `config/observability/alertmanager.yml`
42. Root receiver `blackhole`; telegram and deadman routes and receivers are rendered only when configured.
43. `group_by: [environment, host, service_name, minimoi_alert_key]`; inhibit `equal` gets the same set, plus the CR3-F2 inhibit rule.
44. Render `chat_id` as an integer from private input. Drop envsubst.
45. Remove the explicit `warning|none` to blackhole route. It is unnecessary once the root receiver is blackhole.

### `config/observability/rules/`
46. Replace `minimoi_o1.yaml` (18 rules) with v0.3 `rules.yaml` (26) plus the CR3 fixes, and add `rules/generated/expectations.yaml` from the renderer.
47. Rewrite `rules/tests/` as `alert_rule_test` per alert: firing, quiet and recovery, plus S1–S9.
48. `scripts/observability/check_rules_meta.py`: allow only the named no-`for` exceptions (TelemetryDropping, ScrapeLimitHit, Watchdog), check that the runbook file exists, and check that every alert has a test.

### Files that don't exist yet
49. `scripts/observability/render_config.py` and `check_config.sh`.
50. `config/observability/{emitters,sources,monitor-targets}.yaml`.
51. `docs/runbooks/source-unavailable.md`.
52. Bridge minimal Docker target. The Compose overlays belong to Codex's lane.

### `.github/workflows/observability-config.yml`
53. Add triggers for `tests/observability/**`, `requirements-observability*`, the workflow file, both Compose overlays and runbooks (push as well as PR).
54. Add the render and determinism check, `amtool check-config` on the rendered file, `promtool check config`, `docker compose config` and the privacy integration job.
55. Pin images by digest. Use pinned `actions/*` SHAs. Install `--require-hashes`.

### Native producer wiring (not Claude's lane, but blocks V16)
- The services and host snapshots have fixtures only.
- Release-status `verification` presence is unproven (CR3-F9).
- Guest and standing emitters don't exist.

## 4. Simplifications that keep the invariants

| # | Change | Invariant preserved because | Removes |
|---|---|---|---|
| S-1 | **Drop the O1 logs/events pipeline and the file archive.** Keep `event()` as a validated no-op until O3 picks a log backend | No rule consumes events. Alert history already exists in Prometheus `ALERTS` and the Alertmanager notification log. Original job and release records stay the records of truth | Body, severity, trace-id and event_name sanitization; exemplars and scope reaching the archive; the 500 MiB archive; about half of V9; the `file` exporter, the only exporter where `send_failed` is meaningful |
| S-2 | **Drop the library local-copy files.** Count loss in memory and export it | §7.3 already says the local copy is not an ingest path, has no replay and is not a receipt. It has no consumer | Process-private directories, janitor, 64-directory cap, 100 MiB budget, the `local_io` reason, and all producer-side filesystem risk |
| S-3 | Drop `minimoi_bridge_source_readable` now | O1 isn't deployed anywhere, so there are no consumers and no need for a compatibility release | A second coverage vocabulary and a mapping table |
| S-4 | Drop JobNoSuccess (covered by JobFailed plus the stuck and never-ran rules), or suppress it when failed | Same conditions remain alerted, once | Duplicate notification (CR3-F8) |
| S-5 | With S-1, drop CollectorExportFailing | The pull-based Prometheus exporter does not increment `send_failed`. Loss end to end is caught by the emitter heartbeat that travels the same pipeline | A rule that is effectively dead, plus an internal-metric-name dependency |
| S-6 | Limit generated OTTL to **string** privacy: metric-name membership, exact attribute key set via `keep_keys` plus `Len`, finite string values, resource and scope normalization. Leave **numeric** range checks to the library and the rules | Numeric values cannot carry free text. Ranges are a correctness issue, already enforced twice | Most of the generated OTTL volume and its validation burden |
| S-7 | Defer the shell CLI until a shell job adopts it | §7.4 already excludes the CLI from the inventory; scheduled jobs are covered by status files and the bridge | The flush/exit test matrix in V12 |
| S-8 | Set `OTEL_METRICS_EXEMPLAR_FILTER=always_off` in the library | No traces in O1 | The exemplar question in CR3-F3 |

The renderer (expectations from inventory) is essential, and so are the independent recording-rule expectations. Keep both.

## 5. Priority 4 summary: identity, source time, never-ran, startup and recovery

**Identity.** The scoped joins are correct (Codex's two-host test passes). Gaps:

- `up` and target labels (CR3-F6);
- self-alert identity (CR3-F7);
- `service_name` on job alerts is the **bridge's**, so runbooks and MC should map `job_id` to its owner.

**Original source time.** Correct for services and host (`sampled_at`), and for the deploy event (`observed_at`). The jobs and release read-availability exception is clearly stated. S8 confirms a stale snapshot gates its consumers and does not refresh the timestamp.

**Never-ran.** The due time is `active_from + expected_every + grace`, matching `judge.py`'s `overdue_after_s`. The conflation with unreadable status and the bridge-down storm are in CR3-F2.

**Startup.**

- Expectation recording rules evaluate on the first cycle.
- Every coverage alert has `for` ≥ 2 m, so an empty TSDB plus the first ≤ 90 s of scrapes cannot fire.
- TelemetryDropping and ScrapeLimitHit need two samples.
- Rule groups evaluate independently, so an alert may see the previous cycle's `minimoi_source_current` (30 s lag), which `for` absorbs.

**Recovery.**

- Prometheus restores `for` state within `--rules.alert.for-outage-tolerance` (1 h default).
- Losing Alertmanager's notification log causes repeats, which is acknowledged.
- When a stale source suppresses a critical alert, Alertmanager sends a **resolved** notice (S8 at 75 m). The §9.3 wording requirement ("condition no longer firing; check coverage…") is therefore mandatory for the template, and V5/V13 should assert it.

## 6. Priority 5: decisions only Robert can make

These are additions or changes only. The existing disk 80/95 %, memory 85/95 % and guest 48 h thresholds are already approved and need no re-approval.

1. **O-1 (D2): notification policy.** Do critical and major alerts notify, with warnings left on the dashboard? This includes the new severity assignments: JobMissed critical, JobFailed/JobStuck/JobNeverRan/DeployFailed major.
2. **O-2 (D2): new timing defaults.** Emitter freshness 180 s (or 240 s per CR3-F11); source max-age jobs 180 s, health/host/standing/guest 3600 s; DeployNotFinished 3600 s; the `for` durations in §9.1; the JobFailed bound of ≤ 5 m or a 15 s bridge export (CR3-F4).
3. **O-3 (D3): external dead-man service.** Provider (healthchecks.io or another), account owner, recipient, and ping period/grace of 1 m/5 m.
4. **O-4 (D6): Telegram.** A dedicated alert bot or the existing outbound bot, and which chat. Dev stays blackhole or fake until then.
5. **O-5 (new, CR3-F1): coverage-loss notification.** Should prolonged source or producer loss notify (a major after 2× max-age), or should warnings go into a daily digest or the existing brief? §9.3 "optionally the existing owner-approved brief" is currently undecided.
6. **O-6 (new, S-1/S-2): O1 event archive and library local copy.** Should O1 keep an event archive and a library local copy at all? This is a scope choice with no invariant at stake.
7. **O-7: resource budget.** Accept about 1 GiB of memory caps and 4 GiB of disk on EC2 (after measurement and confirming the instance type), and 4 GiB on the Mac with about 8 GB free. Accept retention of 15 d/2 GB plus 14 d archive (if kept).
8. **O-8 (D5/D7): later gates, not needed tonight.** O2 Mac-to-EC2 TLS forwarding and the exact production release.

## 7. Does v0.3 meet the bar?

| Criterion | Assessment |
|---|---|
| Standards named | Yes. S1–S14 are mapped to R1–R22 with an explicit "design target, not conformance claim". Pinned upstream README links are a good addition. [R22] has no URL (CR3-F12d) |
| References intact | The review, fixture and README links resolve on the branch. The §16 internal cross-references are stale (CR3-F12b). The "v0.2 CLI interface" reference dangles (CR3-F12c). Runbook URLs point at `main` and only resolve after merge, which the spec acknowledges. One runbook is missing (CR3-F12f) |
| Installation executable | **Not yet, and the spec says so honestly** (§10.6 "implementation interface to deliver"). The image entrypoints shown are correct (`/bin/promtool`, `/bin/amtool`, the collector `validate`). Steps 2–4 depend on `render_config.py` and `check_config.sh`, which don't exist. The §10.2 exporter fragment needs CR3-F3 before it can be called "fixed settings" |
| V1–V16 testable | Mostly yes. They are concrete, with failure injection and evidence levels separated. Fix V5's bound (CR3-F4). Make V15 concrete: name the MC operation or journey that proves "MC can inspect coverage and explain". V8c and the real-delivery parts of V13 are gated on O-3 and O-4 and should be marked "fake-harness now, real later" in the matrix itself. Add S1–S9 to the V4 regression set |
| Engineering principles | Strong: the invariants table, honest evidence, fail-closed coverage, no custom daemon, Git as source of truth, explicit owner/author attribution. The residual weakness is notification coverage of missing data (CR3-F1), the property the whole revision exists to deliver |

**Recommended path.**

1. Codex applies CR3-F1 through F4 to the spec and fixtures. The CR3-F2 regression tests should pass quiet after the fix.
2. Claude applies conformance items 1–48 in the O1 lane.
3. The second reviewer reviews the spec together with the first generated `collector.yaml`, validated on the pinned 0.162.0 image, because that is where F2 is actually closed or not.
