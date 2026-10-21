# Codex review — Spec 161 v0.2

Reviewed source: `cc72288c7588f3501abf0dc8592336441e1011b0`,
`docs/specs/spec_161_minimoi_observability_platform_2026-10-20.md`.
SHA-256: `a8dadc4087a2cc24654b665c93753f4f0123cd86f29c4b7312ea3716300134a6`.
Reviewer: Codex, 20 October 2026. Issue: #364.

**Verdict: changes before installation/activation approval.** The standard-tool architecture is appropriate. Continue the authorized independent O1 build while repairing the contracts below. This review is not approval of a resulting implementation or deployment. Robert's latest instruction permits the base build alongside MC; the spec's older “authorizes no build” header no longer describes that limited current authorization. Main/production and notification activation retain their applicable approvals.

Review passes: platform/owner intent; source and configuration contracts; targeted executable checks. Source baseline for existing jobs: `b05bb9b5`, `core/jobs/status.py` and `config/scheduled_jobs.json`. Reviewed the actual document, not the handoff's stale v0.1 hash. The findings below are reviewer recommendations, not new owner decisions.

## Findings

### F1 — P1: loss of telemetry can remain silent (§9.1, §10.3–4, §11 V8, §14)

The Collector Prometheus exporter expires unrefreshed series after five minutes by default. `TelemetryHeartbeatStale` waits until a timestamp is 15 minutes old, then another five minutes. A stopped emitter's series disappears before the threshold. Missing series produce no comparison result. Stopping the Collector also does not stop Prometheus's `vector(1)` Watchdog: Alertmanager can keep pinging the external monitor while all application telemetry is gone. V8's stated alternatives therefore do not reliably detect this failure.

The `max by (service_name)` additionally lets a healthy host hide a failed host of the same service. `CollectorExportFailing` adds two sums; if one metric family is absent, the sum has no result even when the other records failures. The default Collector internal metrics omit `_total`, whereas the rule requires it; names and the internal scrape listener must be explicitly configured and tested for the pinned image.

**Required:** configure a bounded expected-emitter inventory by environment/host/service; detect missing as well as stale series; alert on failed scrapes of Collector and other monitor targets; preserve host/environment dimensions. Align internal metric names and listener with actual exposition and handle an absent metric family. Define distinct failure acceptance for emitter, bridge, Collector, Prometheus, Alertmanager and external delivery. V8 must pass during the outage, without waiting for recovery.

**Evidence:** four synthetic promtool expression cases confirm (a) expired heartbeat yields no result at 25 minutes, (b) a healthy host masks a stale peer, and (c) a missing log-failure family suppresses the metric-failure sum. See executable evidence below. Export expiration and internal names are documented upstream [S1, S2].

### F2 — P1: the Collector does not enforce the promised privacy boundary (§4 P4, §5, §10.3, §13, V9)

`keep_keys` filters datapoint/log attribute **keys only**. It does not validate their values, metric names, resource attributes, scope attributes, or log bodies. The filelog JSON parser can preserve the original JSON in the body. Arbitrary OTLP senders can supply content there. The archive exporter retains those fields, and `resource_to_telemetry_conversion: enabled` promotes all resource attributes to Prometheus labels. A library-only validation test cannot prove Collector rejection. The example vocabulary also lacks a metric-name/type/unit registry despite claiming to validate names.

**Required:** define the full finite metric/event schema and validate it in the library. At Collector ingress, constrain resource/scope fields, signal names, permitted values and body representation; reject unsupported records before every exporter. Do not use a regex alone as a closed set of job IDs: validate membership in the bounded registry. Limit resource label promotion explicitly. Add raw OTLP and raw filelog tests bypassing the library, with synthetic canaries in body, resources, scope, allowed-key values and metric names. Assert absence from archive, metrics and notifications. No real private content is needed.

### F3 — P2: job and snapshot facts are insufficient or incorrectly interpreted (§5.2, §8, §9.1)

`JobStuck` measures time since **last finish**, not the current start. The actual status contract clears `finished_at` and `exit_code` while running. Mapping null to zero creates a false alarm; omitting it makes the rule unable to fire; retaining an old finish measures the gap between runs. Emit `started_at` and use it while running. Define null/never-success, `active_from`, grace and host filtering explicitly, consistent with the job registry. Do not require a fabricated last-success timestamp for every job in V3.

Health and host snapshots need source timestamps and readable/freshness indicators. Re-emitting an old healthy snapshot with a fresh bridge heartbeat must not look current. Define concrete producers, source paths, schemas and read-only mounts for health, host and release snapshots. The example only mounts `data/jobs`; it does not establish the release source. A Docker bridge on the Mac must see the native jobs' real status location, not assume it matches a container path. Existing `read_status` is a convenient schema reader, but uses an unbounded blocking file read: bound size, reject links/nonregular files and avoid FIFO blocking in this long-running bridge.

**Acceptance:** a newly started job after a long idle gap does not alert; an actually overlong run does; first run, disabled/wrong-host job, stale/missing snapshot, future timestamp, malformed/oversize/FIFO source all produce defined outcomes. Synthetic promtool confirms the finish-based expression returns “stuck” for `running=1`, finish=0 after 180 seconds with a 120-second window; it has no current-start input with which to distinguish a fresh run.

### F4 — P2: nonblocking calls and local persistence need one implementable contract (§7.3–5, §12.6, §14)

Writing a local file synchronously before export can block the product on a slow disk, even if exceptions are caught. The SDK's bounded log batch queue does not bound the library's gauge cardinality, local JSONL bytes, or local persistence work. Fourteen days is not a byte bound. The off switch is called both a no-op and a local writer. One-shot CLI processes also need a bounded flush/shutdown policy; a 60-second periodic reader alone does not guarantee their metrics leave before exit.

**Required:** product calls validate and enqueue bounded work; background processing attempts the local copy before export. Specify queue and disk byte caps, gauge key cardinality, overflow counters, finite/NaN handling, disabled behavior, and whether export continues after local-copy failure. Avoid recursively emitting a drop event while handling an emission failure. Define per-process files or synchronization so concurrent processes cannot overwrite `metrics-latest.json`. Give CLI a small bounded exit-time flush or a local agent intake, with loss observable separately from the job exit status. Prove latency remains bounded with slow/full disk and an unreachable Collector.

### F5 — P2: local-copy ingestion has ownership, duplication and replay gaps (§7.3, §10.3, §10.5–6)

Both OTLP and filelog deliver the same events to `file/archive`; no event identity/deduplication or authoritative path is defined. Filelog starts at end, so initial offline backlog can be skipped. Local-copy JSON does not establish per-file resource identity, timestamp or severity mapping. The writer's 0600 files are not automatically readable by Collector UID 10001. The bridge has no explicit UID, while installation creates telemetry directories without assigning that UID. Root-owned 0600 files are a likely mismatch. Likewise owner-created 0600 Compose secret files are not automatically readable by Alertmanager UID 65534 on native Linux bind mounts.

**Required:** choose a single primary event delivery path and an explicit bounded recovery policy. Do not promise replay or exactly-once delivery from the current two paths. Define file layout and source identity; enforce least-privilege read access via deliberate ownership/group/ACL or another standard receiver. Verify secret/source access as each real container UID on Linux and Mac, without loosening every file to world-readable. Test first start, offline accumulation, restart and cursor recovery. `file_storage` declared as an extension supplies filelog offsets; it does not itself create the claimed 24-hour exporter queue (§14). Configure an actual supported exporter queue when O3 needs one, and size it by bytes/items as well as age.

### F6 — P2: installation and CI examples are not yet executable (§9.2, §10.6, §12.2)

Reproduced with the supplied pinned macOS tools:
- All **17 rule definitions parse** with promtool 3.15.0.
- The provided rule-test excerpt fails: `JobMissed` expects no annotations but the rule returns two.
- `amtool 0.34.1 check-config` on the exact CI input fails parsing `${TELEGRAM_CHAT_ID}` as int64.

CI needs a rendered synthetic configuration and synthetic readable secret files/templates at the paths it validates, with no real credentials. The install `docker run ... ${ALERTMANAGER_IMAGE} amtool ...` must select the amtool entrypoint rather than pass it to the image's normal server entrypoint. Collector validation needs required environment and applicable temporary storage paths. CI must validate Prometheus configuration and the Compose rendering too; changes to Compose, dependency pins and validation scripts currently do not all trigger the job. The metadata checker requires `for:` for every alert while intentional immediate alerts omit it; define exceptions. Count both firing and non-firing/recovery assertions rather than merely matching alert names.

**Acceptance:** the complete documented validation sequence succeeds in a clean, credential-free runner at pinned versions, and rejects deliberately bad configs. Tests should use the full emitted labels, not only handcrafted minimal labels. Check internal Collector endpoint access from Prometheus, not a version-only healthcheck. Pin digests in executable config, not only a prose file.

### F7 — P2: notification scope, severity and links are inconsistent (§4 P6, §9, §10, §16)

P6 says only critical notifies; Alertmanager routes major too. V5 explicitly expects a major job-failure notification. Reconcile as one explicit policy, preserving Robert's decision separately. Grouping only by `minimoi_alert_key` can mix environments/hosts; inhibition equality omits them too. A future shared backend could suppress one host's warning because another is critical. `GuestRequestWaiting` uses an unscoped query and its first result, so it can display another source's count. Relative repository runbook paths are not usable links in Telegram.

**Required:** a stable environment/host/service/condition identity throughout rules, grouping, inhibition and future adapters; source-scoped guest counts; deployable authorized runbook links. Test two hosts and dev/prod independently. Test watchdog cadence and actual external recovery, with notification enablement gated by configured/approved destinations. Do not reinterpret the approved watchlist as approval for a new bot or external service.

### F8 — P2: the MC role and existing delivery contracts must survive adoption (§2, §17)

“MC and CoS may read data; they never evaluate alerts or act” is correct only if scoped to the deterministic **monitoring pipeline**. As a blanket product rule it contradicts the owner's MC role: MC investigates, explains, performs authorized supported operations and verifies outcomes. Prometheus evaluates thresholds; MC can respond through its existing authority/receipt discipline. Telemetry is evidence, not a grant of authority.

Do not delete the existing standing findings/delivery path merely because alert notifications move to Alertmanager. Learning lines, brief artifacts, digest delivery, Board/follow-through and owner explanations remain product obligations; this document does not provide replacements for them. Optional digest defaults, bot choice and retirement of each older condition need their stated decisions/parity evidence. Keep the MC 1.2 build moving and make O1 additive. The monitoring platform is not a prerequisite for ordinary MC actions or morning acceptance. Operate must display freshness/unavailable states from the same source/query contract, never invent “healthy” from absent metrics.

### F9 — P2: deployment boundaries and resource claims need concrete enforcement (§6, §10.5, §13)

The Collector and Alertmanager join `default` as well as `observability`, contrary to “only on the internal observability network.” This may be an intentional ingress/egress choice, but the default network is not a Telegram-only egress policy. Name trusted emitter membership, authorized UI query access and outbound restrictions accurately. Reconcile “nothing through nginx” with O2's dedicated nginx OTLP location.

Only Collector has a CPU limit, so “0.6 CPU at most, all four services” is not enforced. The bridge image is unspecified and no non-root user is set. The example Docker healthcheck executes `--version`, which can succeed when the running pipeline is unhealthy. Identify a minimal existing app image containing the bridge/dependencies/config, UID, read-only mounts and all source locations; give each service actual budgets and test on measured host headroom. Avoid a heavy new app image just for the bridge. Keep production/private ports closed; a localhost dev binding is an explicit dev override.

## Nonblocking future corrections

- §5.6: 10% head sampling followed by 100% error tail sampling cannot recover errors discarded at the head. Choose a consistent O4 sampling strategy and bound Collector memory. This does not block O1.
- §9: `ScrapeLimitHit > 0` remains firing after one historical limit breach. Use a recent increase for recovery, plus scrape health. A sample/label limit rejects the **whole scrape**, not only new series [S3].
- §16: OTLP portability is useful, but vendor rule translation, identity mappings and alerting behavior still need their own tested adapters. Do not promise every move is configuration-only. No O3 custom rule engine is needed for O1.
- The review request still pins v0.1 commit/hash and suggests a v0.1 output name. Correct the index to this v0.2 review; retain predecessor attribution.

## Executable evidence and limits

`evidence_v0.2/edge-tests.yaml` contains four expression tests that deliberately assert the problematic current outputs, not desired acceptance. They **all pass** under promtool 3.15.0. Run them with `promtool test rules .../edge-tests.yaml`. The spec's rule-test excerpt and amtool failure above were run on the exact extracted YAML; no source application, service, secret, notification, model, dev or production was touched. Docker/Colima remained stopped. These checks are not a full pipeline acceptance run. Collector configuration has source/document review, not pinned-container validation yet.

## Primary references used

- [S1: pinned Collector Prometheus exporter](https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector-contrib/v0.162.0/exporter/prometheusexporter/README.md): expiration and resource conversion.
- [S2: Collector internal telemetry](https://opentelemetry.io/docs/collector/internal-telemetry/): listener configuration and suffix behavior. Confirm against the pinned runtime during installation.
- [S3: Prometheus scrape configuration](https://prometheus.io/docs/prometheus/latest/configuration/configuration/): whole-scrape limits.
- [S4: Alertmanager configuration](https://prometheus.io/docs/alerting/latest/configuration/): matching, grouping, routing and receivers.
- [S5: Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/): file-mounted access.
- [S6: pinned Collector file exporter](https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector-contrib/v0.162.0/exporter/fileexporter/README.md): archive configuration.

## Ownership and next steps

Claude's O1 library/bridge/rules/runbook work can consume F1–F7 immediately. Codex owns Compose/integration/Operate and will address F9 in that work. Both preserve F8's MC authority and delivery boundaries. Update the spec with dispositions before the second design review; distinguish fixes made from deferred work and unresolved owner decisions. Independently review the resulting code/config before shared-dev activation. MC repairs and user-journey integration remain the first priority; no new owner response is needed for the authorized independent work.
