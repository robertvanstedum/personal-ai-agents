# Memory capture report: contract (v1)

**The one authority** for the counts-only memory capture report, the small Operate matrix, the classification and
selection rules behind them, and how the capture revision treats legacy records. Code: `core/memory_shelf/report.py`
(producer), `core/memory_shelf/migration.py` (preview), parsers in `core/memory_shelf/sessions.py` and `inbox.py`.
Synthetic payloads for every state: `docs/memory_capture_report_samples/` (kept in step with the producer by a test).

## 1. Purpose and boundaries
Make background capture **visible**, so an unnoticed interruption cannot cost continuity later, and keep the facts needed to judge
selection quality. It is **not** a transcript browser, an audit service or a guarantee of semantic completeness.
`coverage: clear` and same-parser fidelity are *storage/format checks*; this contract never presents them as semantic quality.

* **Counts and fixed codes only.** Every string in the payload matches `^[A-Za-z0-9_:.+\- ]{0,100}$` (checked before publication;
  a payload that breaks it is refused and nothing is written). No title, file name, path, credential, excerpt or free text.
  Omitted-kind names from a source's own structure are reduced to that alphabet.
* **Read-only.** Building the report never captures, repairs or rewrites a record or source. It only reads.
* **Units are never mixed.** *Raw items omitted* (file objects, lines, parts) and *normalized turns kept* are separate numbers. There is
  no retention percentage. *Records* are not unique conversations: nothing is deduplicated across sources.
* **Unknown is never healthy.** Missing, stale, unreadable or malformed state yields `unknown`.
* **Job freshness and capture quality are separate.** A green job can sit beside a Watch source.

## 2. Files and publication
Under the existing jobs root (`MINIMOI_JOBS_ROOT`, default `~/minimoi-staging/data/jobs`, the read-only Guild mount), 0600, written
atomically (temp file + fsync + rename):

| file | what | for |
|---|---|---|
| `memory-capture-report.json` | the full report (schema `minimoi.memory-capture-report.v1`) | the producer's consumers, audits, regression checks |
| `memory-capture-matrix.json` | the small matrix (schema `minimoi.memory-capture-matrix.v1`), identical to `report.operate` | the Operate page |

Both carry `run_id`, `generated_at`, and (report) the source snapshot boundary and the parser/policy versions.
Bounded history: `<shelf>/_status/report/history.jsonl` (see §8). Quality results: `<shelf>/_status/quality/{fidelity,semantic-audit}.json`.

**Commands** (`scripts/moi`): `moi report` (prints one summary line, writes nothing), `moi report --json` (also the full payload),
`moi report --fidelity N` (re-reads N random records from their kept sources and records the result), `moi report --publish
[--jobs-root DIR]` (writes the two files and the history). On demand, without recapture, any time. The daily memory job runs
`moi report --publish --fidelity 10` after the captures; a report failure is the warning code `report_failed`, never a failed capture.

## 3. Report schema (`report`)
Top level keys, exactly: `schema, run_id, generated_at, source_snapshot, versions, cadence, job, sources, totals, history, operate`.

* `source_snapshot`: `as_of` (latest ledger row time), `ledger_rows`, `shelf_records`, `basis: read_only_at_generated_at`.
* `versions`: `policy` (`capture-policy.2026-10-04.1`), `report_schema` (1), `parsers` (`{source: normalizer version}`).
* `cadence`: `expected_every_s` 86400, `missed_after_s` 180000 (the `memory-watch` registry values), `stale_report_after_s` 172800.
* `job`: `{state, finished_at, age_s, results}` from the jobs status file, or `{state: unknown, reason: no_jobs_root | no_status_file | stale}`.
* `sources`: one entry per source name (`claude-code`, `codex`, `rooms` for watched sources; `claude-ai`, `grok`, `paste` for the manual
  export source), each with exactly `label, mode (scheduled|manual), capture, selection, quality, evidence, status`.
* `totals`: sums of records and turns by role, with `note: records_not_deduplicated_across_sources`.
* `history`: see §8. `operate`: the matrix (§7).

### 3.1 `capture` (per source)
`records`; `turns {human, assistant, system, coordination, total}`; `redacted_turns` (a **subset** of the turns, not another total);
`by_class {approval_review|subagent|...: turns}` (turns in sessions of a non-dialogue class); `failures {code: n}` (ledger *missing*:
failed, unstable, disk_low, record_absent); `pending_outbox`; `held`, `excluded`, `refused` (`{reason code: n}`);
`changed_or_unverifiable_sources {changed, missing, checked, basis}` (scheduled sources: size and mtime compared with the capture,
`stat` only; `basis: size_and_mtime_since_capture | no_state | not_applicable`); `unreadable_records`.

### 3.2 `selection` (per source)
`raw_items_omitted {total, by_disposition {…}, by_kind {kind: n} (top 40), kinds_total, unknown_kinds [..]}`; `normalized_turns_kept`;
`units_note: raw_items_and_turns_are_different_units`. **Dispositions** (a kind not matched by a rule is `unknown`, and an unknown kind is a Watch):

| disposition | kinds (by rule) |
|---|---|
| `duplicate` | `response_item:message` (a copy of a turn already kept, or empty), `handoff:duplicate` |
| `machinery` | `line:*`, `event_msg:*`, `sidechain`, `item:ContextCompaction`, `response_item:compaction` |
| `tool_activity` | `tool_use:*`, `tool_result[:*]`, Codex tool items (`item:CommandExecution`, `FileChange`, `McpToolCall`, `FunctionCallOutput`, `WebSearch`, `Extension`, `SubAgentActivity`, `CollabAgentToolCall`), `response_item:custom_tool_call*`, `function_call*`, `tool_search_*`, `web_search_results`, `cited_web_search_results`, `query`, `steps` |
| `reasoning_trace` | `thinking`, `thinking_trace`, `agent_thinking_traces`, `item:Reasoning`, `response_item:reasoning` |
| `injected_context` | `injected:*` (except image markers), `injected_prompt_block` |
| `attachment_content_excluded` | `attachment_content_excluded`, `document`, `image`, `input_image`, `local_image`, `file_attachment`, `generated_image_urls`, `card_attachments_json`, `injected:image`, `injected:/image`, `item:ImageView` |
| `attachment_reference` | `attachment_no_text`, `file`, `files`, `attachments`, `line:attachment` |
| `handoff_unavailable` | `handoff:unavailable` |
| `empty` | `empty:*` |
| `unknown` | everything else, including `response_item:agent_message` (a handoff is kept since the capture revision; one still omitted means an older reading) |

A count of file objects is not a count of lost dialogue; the report never converts one into the other.

### 3.3 `quality` (per source)
* `classification {findings [{code, records|turns|items}], records_on_older_parser, current_parser_version}`. **Findings** are known
  unresolved items, each a Watch: `records_on_older_parser`, `unknown_origin`, `unknown_kind`, `possible_gap`, `unclassified_omitted_kind`.
  They are computed from what the shelf holds now; no historical audit count is hard-coded.
* `duplicates` (Codex only; others `{state: not_applicable}`): `state counted|unavailable`, `records_counted` (records read by parser 3 or later, whose
  matching the parser recorded), `records_unverifiable` (older records: duplicate counts were not kept), `copies_matched`, `copies_kept_as_turns`,
  `omitted_copy_items_all_records`, `whitespace_rule: collapse_whitespace_runs_item_bytes_kept`. **Equivalence rule:** two messages match when
  role and text are equal after all whitespace runs collapse to one space; each item is consumed once, so a same-words message with no free
  counterpart stays a turn; the item's own bytes are what is stored. Code indentation differences count as whitespace (the item's exact text is kept).
* `fidelity`: `{state: not_run|ok|failed|stale|unknown, at, method, seed, sample_size, checked, ok, failed, skipped, scope}`. `not_run` is **not passed**.
  `stale` is older than 14 days. `scope: storage_and_edition_same_parser`: it re-reads with the same parser, so it is **not** a semantic audit.
* `semantic_audit`: `{state: not_run | recorded, at, reviewer (agent|human), method, sample_size, scope, findings, skipped}`: only what an operator
  records into `_status/quality/semantic-audit.json`; never inferred.

### 3.4 `evidence`
`attachment_references`, `attachment_content_excluded_items`, `attachment_chars_excluded`, `attachments_seen`, `handoffs_retained`,
`handoffs_unavailable` (an `encrypted_content` part is never decrypted or guessed), `handoffs_duplicate`.

### 3.5 `status` (per source)
`{state: current|watch|off|unknown, reason, last_success_at, last_check_kind: capture|check|none, last_new_capture_at}`. Rules in §6.

## 4. What the capture revision changed (the classification rules)
These are structural: only a source's own fields decide a role, never message wording.
1. **Claude Code `origin.kind`:** `human` is the owner's words (it was stored as system); `peer` is another agent session (`coordination`);
   `task-notification` is `system`; **any other origin is not guessed** (stays `system`, typed `unknown`, counted, and raises `unknown_kind` = Watch). No origin: unchanged (`human`, or `system` for meta/compaction).
2. **Codex session class** from `session_meta.thread_source`: `guardian_review` (automatic approval review) and `subagent`/`agent_created_thread`
   (a thread an agent started). Their prompts and decisions are `coordination` with `class` provenance, never the owner's dialogue or authority. The same words in an
   ordinary session stay dialogue. The record carries tag `class:<name>` and `normalized.session_class`.
3. **Handoffs** (`response_item/agent_message`): kept as `coordination` turns with sender, recipient and time from the source; the `input_text` is kept, an `encrypted_content`
   part is marked `unavailable` (never decrypted); a handoff whose text is already a kept turn is a counted duplicate.
4. **Attachments:** a reference stays (name, type, size as the source gave them; a missing name is `unnamed`, never invented); extracted attachment text does **not** enter
   the dialogue (Claude.ai; Claude Code, Codex and Grok already kept images and documents as pointers). Text the owner typed or pasted into a message is dialogue.
   Explicitly designated final specs and artifacts use the existing deliberate designation path, not blanket import.
5. **Dialogue for retrieval:** `render.dialogue_turns(turns)` returns the `human` and `assistant` turns not in a non-dialogue class (`approval_review`, `subagent_task`, `subagent`, `handoff`).
   Ordinary conversational retrieval reads that; coordination and system turns remain in the record as attributed operational evidence.

**Versions:** parsers `claude-code` 2, `codex` 3, `claude-ai` 2 (Grok, pastes, Rooms unchanged). `moi migrate-preview [source]` is the **counts-only, read-only** preview
before any reprocessing: per provider, records compared, records that would get a new edition, role transitions (`system->human`, `human->coordination`, `added:coordination`,
`text_changed` ...), role totals before and after, classes after, and sources changed or gone (counted apart, never compared).
**Reprocessing:** the next `moi watch` re-reads an unchanged scheduled source with the new parser and adds a **new edition** where the turns differ; for the export source
`moi reprocess inbox` (typed yes) does the same from the kept export files. Output is deterministic for a given source and version. **Editions are immutable:** nothing is deleted
or rewritten. The current edition is what retrieval reads, so misattributed or extracted text leaves ordinary retrieval as soon as the corrected edition exists; the old editions
remain under the existing retention policy. A stricter purge is a separate owner decision.

## 5. Whitespace and duplicates
See §3.3. The rule is deliberately simple and documented; indentation is not preserved by the *matching*, but the kept turn is always the item's own text. A copy that differs from every
free item only in whitespace is treated as that item's duplicate; if no item is free, it stays a turn.

## 6. Status of a source (the order of judgement)
Applied top to bottom; the first match wins. `job` freshness is **not** an input.
Scheduled sources (`claude-code`, `codex`, `rooms`):
1. not configured → `off / not_configured`
2. not approved (or no dry run yet) → `off / not_approved` (intentional: the owner has not switched it on)
3. approval stale (its definition changed) → `watch / approval_stale` (it was on; needs the owner)
4. no readable status file, or no success time → `unknown / no_state`
5. last pass not `ok`, or any failed/unstable/disk-low item → `watch / capture_failed`
6. `now - last_success_at > 180000 s` → `watch / overdue`
Then, for every source:
7. an omitted kind no rule recognises → `watch / unclassified_omitted_kind`
8. any classification finding (§3.3) → `watch / <first finding code>`
9. fidelity `not_run`, `stale` or `unknown` (scheduled sources) → `unknown / quality_unknown`; fidelity `failed` → `watch / fidelity_failed`
10. otherwise `current / ok`
Manual sources (exports, pastes): no schedule, so never `overdue`; no captures yet → `off / no_captures_yet`; ledger *missing* → `watch / capture_failed`; then 7, 8; else `current / ok`.
**Current** means recent successful processing with no reported issue, not complete semantic coverage.
`last_success_at` moves only when a pass ends `ok` with no failed, unstable or disk-low item; `last_new_capture_at` only when that pass also captured or added an edition. A clean pass that finds nothing
new advances the first only (`last_check_kind: check`).

## 7. The Operate matrix (`memory-capture-matrix.json`), for the Guild page
```
{schema: "minimoi.memory-capture-matrix.v1", run_id, generated_at, stale_after_s: 172800,
 job: {state, reason}, overall: current|watch|off|unknown,
 rows: [{source, label, mode, last_success_at, last_check_kind, last_new_capture_at, captured_records, status, reason}]}
```
Display rules (UI owner: Codex): one compact table, columns **Source · Last successful check/capture · Captured records · Status**. Use "Last successful capture" when
`last_check_kind == capture`, "Last successful check" when `check`; the time is source-specific and its age is computed by the UI from `generated_at`/now. `captured_records` stays visible
when stale, labelled by status. Show `reason` beside any non-`current` status; `off` is intentional, `watch` is not.
`overall` = worst row by `watch > unknown > current > off`. **The UI must treat the matrix itself as `unknown` if it is missing, unreadable, has an unexpected `schema`, or
`now - generated_at > stale_after_s`.** `job.state` is shown separately (the existing jobs tile logic); never merge it into a source's status.

## 8. History (bounded)
`history.jsonl` keeps at most **90** snapshots. A snapshot is appended only when some source's totals, parser version or state changed since the last one, or once per UTC day (a heartbeat),
so unchanged totals do not accumulate. `report.history` gives `retained_snapshots`, `bound`, `previous_run_id`, `delta_since_previous {source: {records, turns, omitted}}` (same units per source)
and `unchanged_since_previous`. Comparison is always like with like; no combined turns/(turns+items) number exists. Alert thresholds, when added, are configuration
(`config/`), not code, and are not part of this contract's guarantees.

## 9. Limitations (stated, not hidden)
* Roles are right to the extent the **source's structure** is: an automatic prompt that arrives with no structural marker is still a human turn. Unknown structure is flagged, not fixed.
* Records written before this revision lack `turns_by_role`; the report counts their bodies each run (slower, same result) until they are reprocessed.
* `duplicates` is known only for records read by parser 3 or later; older ones are `records_unverifiable`.
* A changed source (it grew since capture) is counted in `changed_or_unverifiable_sources` and is not compared; the next capture is a new edition.
* Claude.ai may export a large paste as an attachment; those are reference-only under the owner boundary (the count of attachments with no file type is in `evidence` via
  `attachments_seen` and the excluded characters). If that turns out to cut real dialogue, it is a one-line policy change, not a data loss: the export file still holds it.
* Subagent-thread prompts are treated as coordination (an agent wrote them). That is an addition to the audit's list, easy to switch back (`_codex_session_role`).
* The fidelity sample re-reads with the same parser; it proves storage and edition fidelity, not that the parser's judgement is right. A semantic audit is a human or agent review recorded by an operator.
* No cross-source deduplication exists, so `records` is not a count of unique conversations.
