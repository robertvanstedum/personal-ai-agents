// MiniMoi Rooms and operations UI: the one data contract the views use (package U, first version).
//
// Views never fetch data themselves. They call an adapter that implements the methods below.
//   adapter_fixture.js  U   simulated fixtures; every object carries "simulated": true
//   adapter_http.js     A4  the live API; refuses any payload that carries "simulated" (not wired in U)
// Both must return objects that validate against the JSON Schemas in contracts/ (registry: contracts/index.json).
// Absent data is explicit: a store that cannot be read yields {status: "unavailable", code: "store_unavailable"},
// which views render as Unknown, never as zero or empty.

/**
 * @typedef {object} Absent
 * Shape: contracts/common.schema.json#/$defs/absent (embedded in each resource schema).
 * @property {string} schema  The contract id of the resource that could not be read.
 * @property {true} [simulated]
 * @property {'unavailable'|'not_found'} status
 * @property {'store_unavailable'|'not_found'} code
 * @property {string} observed_at
 * @property {string} detail
 */

/**
 * @typedef {object} Overview
 * Shape: contracts/overview.schema.json ("schema": "minimoi.work.overview/1").
 * @property {'minimoi.work.overview/1'} schema
 * @property {true} [simulated]  Fixture data only.
 * @property {string} observed_at
 * @property {object} roster      "minimoi.work.roster/1" (plan §5: observed_at, refresh_s, monitor, principals,
 *                                agents[], attempts[], completed_recent[]) or {status:"unavailable", code:"store_unavailable"}.
 * @property {object} attention   {status:"known", exceptions[], decisions[]} or {status:"unknown", reason}.
 * @property {{central_records: object, local_workshop: object, diagnostics: object}} health
 *                                Each {status, detail, observed_at|null, source|null}; null observed_at means Unknown.
 */

/**
 * @typedef {object} AttemptDetail
 * Shape: contracts/attempt_detail.schema.json ("schema": "minimoi.work.attempt/1").
 * attempt (four state dimensions), assignment (typed work reference, spec revision, acceptance boundary,
 * requester/builder/reviewer, mandate), assurance (five stages with evidence), timeline (observation vs
 * self_report, source and received times), evidence, next_owner, diagnostics, recovery (null unless an
 * exception is open; always present when execution is outcome_uncertain).
 */

/**
 * @typedef {object} RecordList
 * Shape: contracts/record.schema.json#/$defs/record_list ("schema": "minimoi.records.list/1").
 * @property {number} limit
 * @property {boolean} truncated
 * @property {object[]} records  Summaries: source_id, title, kind, current_revision, fidelity, coverage_state, …
 */

/**
 * @typedef {object} RecordDetail
 * Shape: contracts/record.schema.json#/$defs/record ("schema": "minimoi.records.record/1").
 * Envelope per Spec 158 §21.4: fidelity, coverage {state, missing[], note}, capture, authenticated submitter,
 * declared speakers (unverified), authorship direct|imported, receipt stages, segments, relations.
 */

/**
 * @typedef {object} Interpretation
 * Shape: contracts/interpretation.schema.json ("schema": "minimoi.records.interpretation/1").
 * state current|stale (+stale_reason), adapter + adapter_kind test|live, usage, cited[], body with owner_decisions
 * (authorship "direct", decided_by, decided_at) kept apart from reported_decisions (reported_unverified).
 */

/**
 * @typedef {object} SessionView
 * Shape: contracts/session_view.schema.json ("schema": "minimoi.rooms.session_view/1").
 * participants with separate membership / contact (join receipt) / working (current observed attempt only),
 * standing_visibility (never membership), selected_context with disclosure, requests with next_owner, inbox.
 */

/**
 * @typedef {object} RecordDraft
 * @property {string|null} title
 * @property {string} text
 * @property {string} source_application
 * @property {'original'|'excerpt'|'extraction'|'reconstruction'} fidelity
 * @property {{state: 'complete'|'partial', missing: string[], note: string|null}} coverage
 * @property {string[]} declared_speakers
 */

/**
 * @typedef {object} Action
 * @property {'check_saved_result'|'pause_recovery'|'save_record'|'share_selected_context'|'answer_request'} type
 * @property {string} target  Attempt id, workspace scope, session id or request id, depending on type.
 * Additional fields: draft (save_record), source {source_id, revision} and session (share_selected_context),
 * answer and session (answer_request).
 */

/**
 * @typedef {object} ActionResult
 * Shape: contracts/action_result.schema.json ("schema": "minimoi.ui.action_result/1").
 * @property {string} outcome
 * @property {string} message  Announced to the user; simulated results say so.
 */

/**
 * @typedef {object} RoomsDataContract
 * @property {() => Promise<Overview>} getOverview
 * @property {(attemptId: string) => Promise<AttemptDetail|Absent>} getAttempt
 * @property {() => Promise<RecordList|Absent>} listRecords
 * @property {(recordId: string, revision?: number) => Promise<RecordDetail|Absent>} getRecord
 * @property {(draft: RecordDraft) => Promise<ActionResult>} saveRecord
 * @property {(recordId: string) => Promise<Interpretation|Absent>} getInterpretation
 * @property {(sessionId: string) => Promise<SessionView|Absent>} getSession
 * @property {(action: Action) => Promise<ActionResult>} act
 */

export const CONTRACT_VERSION = 1;

export const METHODS = Object.freeze([
  'getOverview', 'getAttempt', 'listRecords', 'getRecord', 'saveRecord', 'getInterpretation', 'getSession', 'act',
]);

export const ACTION_TYPES = Object.freeze([
  'check_saved_result', 'pause_recovery', 'save_record', 'share_selected_context', 'answer_request',
]);

export const SCHEMA_IDS = Object.freeze({
  overview: 'minimoi.work.overview/1',
  attempt: 'minimoi.work.attempt/1',
  record: 'minimoi.records.record/1',
  recordList: 'minimoi.records.list/1',
  interpretation: 'minimoi.records.interpretation/1',
  session: 'minimoi.rooms.session_view/1',
  actionResult: 'minimoi.ui.action_result/1',
});

/** Throws unless every contract method is a function. Returns the adapter for chaining. */
export function assertAdapter(adapter) {
  const missing = METHODS.filter((name) => typeof adapter?.[name] !== 'function');
  if (missing.length) throw new TypeError(`Adapter does not implement: ${missing.join(', ')}`);
  return adapter;
}

/** True when a resource could not be read or does not exist (render Unknown / Not found, never zero). */
export function isAbsent(resource) {
  return Boolean(resource) && (resource.status === 'unavailable' || resource.status === 'not_found');
}
