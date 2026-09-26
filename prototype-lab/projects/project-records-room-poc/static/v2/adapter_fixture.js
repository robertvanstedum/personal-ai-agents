// Fixture adapter (package U): implements contract.js from synthetic JSON in /static/v2/fixtures/.
// It never calls the Records API. Every object it returns carries "simulated": true, and act() changes
// only this page's in-memory copy of the fixtures.
import { ACTION_TYPES, SCHEMA_IDS, assertAdapter } from './contract.js';

export const FIXTURE_BASE = '/static/v2/fixtures/';
export const SNAPSHOT_AT = '2026-09-25T15:42:00Z';
export const DEFAULT_SESSION_ID = 'ses-handoff-review';
export const EFFECTS = 'None. Preview only: nothing outside this page changed.';

export const SCENARIO_LABELS = Object.freeze({
  normal: 'Normal',
  recovery: 'Recovery (outcome uncertain)',
  stale: 'Stale observations',
  store_unavailable: 'Store unavailable',
  empty: 'Empty (no work, no records)',
  error: 'Adapter error',
  loading: 'Loading (never finishes)',
});
export const SCENARIOS = Object.freeze(Object.keys(SCENARIO_LABELS));

const RECORD_FILES = {
  'rec-7f1c': { 2: 'record.rec-7f1c.r2.json' },
  'rec-5d31': { 1: 'record.rec-5d31.r1.json' },
  'rec-2b90': { 1: 'record.rec-2b90.r1.json' },
  'rec-9a04': { 1: 'record.rec-9a04.r1.json' },
  'rec-3e77': { 1: 'record.rec-3e77.r1.json' },
};
const ATTEMPT_FILES = {
  'att-room-021-2': 'attempt.room-021-2.json',
  'att-c2c-003-1': 'attempt.c2c-003-1.json',
  'att-capture-014-1': 'attempt.capture-014-1.json',
  'att-export-009-1': 'attempt.export-009-1.json',
};
// Records cited by the one synthetic interpretation.
const INTERPRETED = new Set(['rec-7f1c', 'rec-5d31', 'rec-2b90']);

const PLANS = {
  normal: {
    overview: 'overview.normal.json', records: 'records.normal.json', recordFiles: RECORD_FILES,
    interpretation: 'interpretation.recovery-placement.json', attempts: ATTEMPT_FILES, session: 'session.normal.json',
  },
  recovery: {
    overview: 'overview.recovery.json', records: 'records.normal.json', recordFiles: RECORD_FILES,
    interpretation: 'interpretation.recovery-placement.json',
    attempts: { ...ATTEMPT_FILES, 'att-room-021-2': 'attempt.room-021-2.recovery.json' }, session: 'session.recovery.json',
  },
  stale: {
    overview: 'overview.stale.json', records: 'records.stale.json',
    recordFiles: { ...RECORD_FILES, 'rec-7f1c': { 2: 'record.rec-7f1c.r2.stale.json', 3: 'record.rec-7f1c.r3.json' } },
    interpretation: 'interpretation.recovery-placement.stale.json',
    attempts: { ...ATTEMPT_FILES, 'att-room-021-2': 'attempt.room-021-2.stale.json', 'att-c2c-003-1': 'attempt.c2c-003-1.stale.json' },
    session: 'session.stale.json',
  },
  empty: {
    overview: 'overview.empty.json', records: 'records.empty.json', recordFiles: {}, interpretation: null, attempts: {},
    session: 'session.empty.json',
  },
  store_unavailable: {
    overview: 'overview.store_unavailable.json',
    unavailable: {
      records: 'records.unavailable.json', record: 'record.unavailable.json', attempt: 'attempt.unavailable.json',
      interpretation: 'interpretation.unavailable.json', session: 'session.unavailable.json',
    },
  },
  error: { fail: true },
  loading: { hang: true },
};

const TEMPLATES = { check: 'action_result.check_saved_result.json', save: 'action_result.save_record.json' };

export class FixtureAdapterError extends Error {
  constructor(message) { super(message); this.name = 'FixtureAdapterError'; }
}

/** Own-property lookup: ids come from the URL, so inherited names like "constructor" must not resolve. */
function own(map, key) { return map && Object.hasOwn(map, key) ? map[key] : undefined; }

function isoSeconds(ms) { return new Date(ms).toISOString().replace(/\.\d{3}Z$/, 'Z'); }

async function sha256Hex(text) {
  if (!globalThis.crypto?.subtle) throw new FixtureAdapterError('SHA-256 is unavailable in this browser context.');
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

/**
 * @param {{scenario?: string, fetchImpl?: typeof fetch}} [options]
 * @returns {import('./contract.js').RoomsDataContract & {scenario: string, simulated: true}}
 */
export function createFixtureAdapter({ scenario = 'normal', fetchImpl = globalThis.fetch.bind(globalThis) } = {}) {
  const plan = PLANS[scenario];
  if (!plan) throw new FixtureAdapterError(`Unknown preview scenario "${scenario}".`);
  const cache = new Map();
  const saved = new Map();
  const startedAt = Date.now();
  let counter = 0;

  // Simulated clock: the fixture snapshot time plus real time elapsed since this adapter was created.
  const now = () => isoSeconds(Date.parse(SNAPSHOT_AT) + (Date.now() - startedAt));
  const copy = (value) => structuredClone(value);

  function load(name) {
    if (!/^[a-z0-9_.-]+\.json$/.test(name)) throw new FixtureAdapterError(`Invalid fixture name "${name}".`);
    if (!cache.has(name)) {
      const url = FIXTURE_BASE + name;
      if (!url.startsWith(FIXTURE_BASE) || url.includes('/api/')) throw new FixtureAdapterError('Fixture adapter may not call the API.');
      cache.set(name, fetchImpl(url, { cache: 'no-store', credentials: 'omit' }).then(async (response) => {
        if (!response.ok) throw new FixtureAdapterError(`Fixture ${name} could not be loaded (HTTP ${response.status}).`);
        const data = await response.json();
        if (data?.simulated !== true) throw new FixtureAdapterError(`Fixture ${name} is not marked simulated; refusing to show it.`);
        return data;
      }).catch((error) => { cache.delete(name); throw error; }));
    }
    return cache.get(name);
  }

  async function gate() {
    if (plan.fail) throw new FixtureAdapterError('Simulated adapter failure (scenario "error"): the preview could not load this data.');
    if (plan.hang) await new Promise(() => {});
  }

  function notFound(schema, subject, detail) {
    return { schema, simulated: true, status: 'not_found', code: 'not_found', observed_at: SNAPSHOT_AT, detail, subject };
  }

  function result(type, target, outcome, message, extra = {}) {
    return {
      schema: SCHEMA_IDS.actionResult, simulated: true, action: { type, target }, outcome, message, at: now(),
      effects: EFFECTS, state_changes: extra.state_changes || [], record: extra.record ?? null,
    };
  }

  async function readUnavailable(kind) {
    return plan.unavailable ? copy(await load(plan.unavailable[kind])) : null;
  }

  const adapter = {
    scenario,
    simulated: true,

    async getOverview() {
      await gate();
      return copy(await load(plan.overview));
    },

    async getAttempt(attemptId) {
      await gate();
      const unavailable = await readUnavailable('attempt');
      if (unavailable) return { ...unavailable, subject: attemptId };
      const file = own(plan.attempts, attemptId);
      if (!file) return notFound(SCHEMA_IDS.attempt, attemptId, 'No attempt with this id exists in the current scenario.');
      return copy(await load(file));
    },

    async listRecords() {
      await gate();
      const unavailable = await readUnavailable('records');
      if (unavailable) return unavailable;
      const list = copy(await load(plan.records));
      const newest = [...saved.values()].reverse().map((r) => ({
        source_id: r.source_id, title: r.title, kind: r.kind, current_revision: r.current_revision, fidelity: r.fidelity,
        coverage_state: r.coverage.state, missing_count: r.coverage.missing.length, captured_at: r.capture.captured_at,
        submitter: r.submitter.principal, authorship: r.authorship,
      }));
      list.records = [...newest, ...list.records];
      return list;
    },

    async getRecord(recordId, revision) {
      await gate();
      const unavailable = await readUnavailable('record');
      if (unavailable) return { ...unavailable, subject: recordId };
      if (saved.has(recordId)) {
        const record = saved.get(recordId);
        if (revision && Number(revision) !== record.revision) return notFound(SCHEMA_IDS.record, `${recordId}@${revision}`, 'That revision does not exist.');
        return copy(record);
      }
      const files = own(plan.recordFiles, recordId);
      if (!files) return notFound(SCHEMA_IDS.record, recordId, 'No record with this id exists in the current scenario.');
      const wanted = revision ? Number(revision) : Math.max(...Object.keys(files).map(Number));
      if (!own(files, wanted)) return notFound(SCHEMA_IDS.record, `${recordId}@${wanted}`, 'That revision is not part of this preview.');
      return copy(await load(own(files, wanted)));
    },

    async getInterpretation(recordId) {
      await gate();
      const unavailable = await readUnavailable('interpretation');
      if (unavailable) return { ...unavailable, subject: recordId };
      if (!plan.interpretation || !INTERPRETED.has(recordId)) {
        return notFound(SCHEMA_IDS.interpretation, recordId, 'No interpretation cites this record yet.');
      }
      return copy(await load(plan.interpretation));
    },

    async getSession(sessionId) {
      await gate();
      const unavailable = await readUnavailable('session');
      if (unavailable) return { ...unavailable, subject: sessionId };
      if (sessionId !== DEFAULT_SESSION_ID) return notFound(SCHEMA_IDS.session, sessionId, 'No session with this id exists in the current scenario.');
      return copy(await load(plan.session));
    },

    async saveRecord(draft) {
      return adapter.act({ type: 'save_record', target: 'workspace:robert', draft });
    },

    async act(action) {
      await gate();
      const type = action?.type;
      const target = String(action?.target || '');
      if (!ACTION_TYPES.includes(type) || !target) throw new FixtureAdapterError('Unknown or incomplete action.');
      if (plan.unavailable) {
        return type === 'save_record'
          ? result(type, target, 'not_saved', 'Not saved · simulated. The record store could not be reached, so nothing was stored. Your text is still in the form.')
          : result(type, target, 'refused', 'Not done · simulated. The store could not be read, so nothing changed.');
      }
      switch (type) {
        case 'check_saved_result': return checkSavedResult(target);
        case 'pause_recovery': return pauseRecovery(target);
        case 'answer_request': return answerRequest(target, action);
        case 'share_selected_context': return shareContext(target, action);
        case 'save_record': return saveRecord(target, action.draft || {});
        default: throw new FixtureAdapterError('Unhandled action.');
      }
    },
  };

  async function recoveryFor(attemptId) {
    const file = own(plan.attempts, attemptId);
    if (!file) return null;
    const detail = await load(file);
    return detail.recovery ? detail : null;
  }

  async function checkSavedResult(attemptId) {
    const detail = await recoveryFor(attemptId);
    if (!detail || detail.recovery.receipt_check.status !== 'not_checked') {
      return result('check_saved_result', attemptId, 'refused', 'Nothing to check · simulated. This attempt has no unchecked saved result.');
    }
    const template = copy(await load(TEMPLATES.check));
    const at = now();
    const recovery = detail.recovery;
    const operationId = recovery.receipt_check.operation_id;
    recovery.receipt_check = {
      status: 'receipt_found', operation_id: operationId, checked_at: at,
      detail: `Operation ${operationId} committed centrally at 10:28 CT (simulated). Its payload SHA-256 matches the local evidence.`,
    };
    recovery.known_effects.push(`Central save committed: receipt found for ${operationId} (simulated)`);
    recovery.unknown_effects = ['Whether the stored result passes verification (not yet checked)'];
    recovery.next_safe_action = {
      summary: 'Verify the stored result against the local evidence, then close the exception with both references.',
      authority_note: recovery.next_safe_action.authority_note,
    };
    recovery.actions = recovery.actions.map((a) => (a.type === 'check_saved_result' ? { ...a, enabled: false, note: 'Receipt already found' } : a));
    recovery.actions_taken.push({ type: 'check_saved_result', actor: 'robert', at, result: 'Receipt found (simulated)' });
    detail.attempt.execution = 'finished';
    detail.attempt.delivery = 'centrally_recorded';
    detail.next_owner = { principal: recovery.recovery_owner, action: 'Verify the stored result against the local evidence' };
    detail.timeline.push({
      seq: Math.max(0, ...detail.timeline.map((e) => e.seq)) + 1, kind: 'effect_observed', basis: 'observation',
      title: 'Saved result found (simulated)', detail: `Receipt lookup for ${operationId}; payload hash matches`,
      actor: 'robert', step: 'publish-result', wait_reason: null, source_observed_at: '2026-09-25T15:28:14Z',
      received_at: at, operation_id: operationId, evidence_refs: ['ev-op-021'],
    });
    const overview = await load(plan.overview);
    for (const attempt of overview.roster.attempts || []) {
      if (attempt.attempt_id !== attemptId) continue;
      Object.assign(attempt, {
        execution: 'finished', delivery: 'centrally_recorded',
        latest_evidence: 'Receipt found (simulated) · verification pending', next_action: 'Verify the stored result',
      });
    }
    for (const item of overview.attention.exceptions || []) {
      if (item.target?.ref !== attemptId) continue;
      Object.assign(item, {
        summary: 'Receipt found (simulated). The exception stays open until the stored result is verified.',
        next_action: 'Verify the stored result', last_observed_at: at,
      });
    }
    return { ...template, action: { type: 'check_saved_result', target: attemptId }, at };
  }

  async function pauseRecovery(attemptId) {
    const detail = await recoveryFor(attemptId);
    if (!detail || detail.recovery.paused) {
      return result('pause_recovery', attemptId, 'refused', 'Nothing to pause · simulated. Recovery is not running for this attempt.');
    }
    const at = now();
    detail.recovery.paused = { by: 'robert', at };
    detail.recovery.actions = detail.recovery.actions.map((a) => (a.type === 'pause_recovery' ? { ...a, enabled: false, note: 'Paused' } : a));
    detail.recovery.actions_taken.push({ type: 'pause_recovery', actor: 'robert', at, result: 'Paused (simulated)' });
    return result('pause_recovery', attemptId, 'paused',
      'Recovery paused · simulated. No recovery step runs until someone resumes it. Pausing does not resolve the exception.',
      { state_changes: [{ target: detail.recovery.exception_id, field: 'paused', from: null, to: 'robert' }] });
  }

  async function answerRequest(requestId, action) {
    const text = String(action.answer || '').trim();
    const session = await load(plan.session);
    const request = session.requests.find((r) => r.id === requestId);
    if (!request || request.state !== 'requested' || request.assignee !== session.viewer) {
      return result('answer_request', requestId, 'refused', 'Not answered · simulated. This request is not waiting for your answer.');
    }
    if (!text) return result('answer_request', requestId, 'refused', 'Not answered · simulated. Write an answer first.');
    const at = now();
    Object.assign(request, {
      state: 'answered', answer: { by: session.viewer, at, text }, next_owner: request.requester,
      next_action: 'Acknowledge the answer', updated: at,
    });
    session.inbox.for_you = session.inbox.for_you.filter((id) => id !== requestId);
    const overview = await load(plan.overview);
    if (overview.attention.decisions) {
      overview.attention.decisions = overview.attention.decisions.filter((d) => d.id !== `dec-${requestId.toLowerCase()}`);
    }
    const requester = session.principals.find((p) => p.id === request.requester)?.label || request.requester;
    return result('answer_request', requestId, 'answered',
      `Answer sent · simulated. ${requester} now owns the next step: acknowledge it. An answer is not an approval.`,
      { state_changes: [{ target: requestId, field: 'state', from: 'requested', to: 'answered' }] });
  }

  async function shareContext(sessionId, action) {
    const source = action.source || {};
    const session = await load(plan.session);
    if (sessionId !== session.session.id) return result('share_selected_context', sessionId, 'refused', 'Not shared · simulated. Unknown session.');
    const record = await adapter.getRecord(source.source_id, source.revision);
    if (!record || record.status) return result('share_selected_context', sessionId, 'refused', 'Not shared · simulated. That record could not be read.');
    if (session.selected_context.some((c) => c.source.source_id === record.source_id && c.source.revision === record.revision)) {
      return result('share_selected_context', sessionId, 'refused', `Not shared · simulated. ${record.source_id}@${record.revision} is already in this session.`);
    }
    counter += 1;
    const at = now();
    const grant = `dg-sim-${counter}`;
    session.selected_context.push({
      source: { source_id: record.source_id, revision: record.revision }, title: record.title || record.source_id,
      fidelity: record.fidelity, coverage_state: record.coverage.state, shared_by: session.viewer,
      grant: { id: grant, kind: 'source_disclosure', scope: `${record.source_id}@${record.revision} to this session's current audience` },
      shared_at: at, disclosure_note: 'Participants see this copy. The source record\'s other context is not disclosed.',
    });
    return result('share_selected_context', sessionId, 'shared',
      `Shared · simulated under disclosure grant ${grant}. Participants see this copy only; CoS's standing read did not grant it.`,
      { state_changes: [{ target: sessionId, field: 'selected_context', from: null, to: `${record.source_id}@${record.revision}` }] });
  }

  async function saveRecord(target, draft) {
    const text = String(draft.text || '');
    const fidelity = draft.fidelity;
    const coverageState = draft.coverage?.state;
    const missing = (draft.coverage?.missing || []).map((m) => String(m).trim()).filter(Boolean);
    const speakers = [...new Set((draft.declared_speakers || []).map((s) => String(s).trim()).filter(Boolean))];
    const refuse = (why) => result('save_record', target, 'refused', `Not saved · simulated. ${why}`);
    if (!text.trim()) return refuse('Paste the text to keep first.');
    if (new TextEncoder().encode(text).length > 2_000_000) return refuse('The text is over the 2,000,000-byte limit; nothing was truncated.');
    if (!['original', 'excerpt', 'extraction', 'reconstruction'].includes(fidelity)) return refuse('Choose a fidelity.');
    if (!['complete', 'partial'].includes(coverageState)) return refuse('Choose complete or partial coverage.');
    if (fidelity === 'excerpt' && coverageState === 'complete') return refuse('An excerpt cannot be marked complete.');
    const note = coverageState === 'partial' && !missing.length ? 'Partial; the missing parts were not listed.' : null;
    const coverage = { state: coverageState, missing: coverageState === 'complete' ? [] : missing, note };

    counter += 1;
    const at = now();
    const sourceId = `rec-sim${counter}`;
    const paragraphs = text.split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean);
    const segments = [];
    for (const [index, paragraph] of paragraphs.entries()) {
      const match = paragraph.match(/^([^:\n]{1,40}):\s+([\s\S]+)$/);
      const speaker = match && speakers.includes(match[1].trim()) ? match[1].trim() : null;
      const body = speaker ? match[2].trim() : paragraph;
      segments.push({ ordinal: index + 1, declared_speaker: speaker, source_time: null, text: body, sha256: await sha256Hex(body) });
    }
    const sha = await sha256Hex(text);
    const submitter = { principal: 'robert', label: 'Robert', authenticated: true, route: 'records_save' };
    const stages = { original: 'committed', extraction: 'not_applicable', indexing: 'done', central: 'not_connected' };
    const receipt = {
      id: `rcpt-sim${counter}-1`, actor: 'robert', committed_at: at, durability: 'local_sqlite',
      production: 'not_connected', scope: 'workspace:robert',
    };
    const title = String(draft.title || '').trim() || null;
    saved.set(sourceId, {
      schema: SCHEMA_IDS.record, simulated: true, source_id: sourceId, workspace: 'workspace:robert', owner: 'robert',
      kind: speakers.length ? 'conversation' : 'note', title, revision: 1, current_revision: 1,
      revisions: [{ revision: 1, created: at, note: 'Saved in the preview (simulated)' }], sha256: sha,
      media_type: 'text/plain', size: new TextEncoder().encode(text).length, fidelity, coverage,
      capture: { source_application: String(draft.source_application || 'Not stated').slice(0, 80), source_reference: null, captured_at: at, source_time: null },
      submitter, declared_author: null, declared_speakers: speakers, authorship: 'imported', stages, receipt, segments, relations: [],
    });
    const template = copy(await load(TEMPLATES.save));
    return {
      ...template,
      action: { type: 'save_record', target },
      message: `Saved · simulated. Receipt ${receipt.id}. No Room, task or run was created.`,
      at,
      record: { source_id: sourceId, revision: 1, sha256: sha, title, fidelity, coverage, stages, receipt, submitter, declared_speakers: speakers },
    };
  }

  return assertAdapter(adapter);
}
