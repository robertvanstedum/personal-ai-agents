// Work detail: scope, four state dimensions, assurance stages with evidence, observation/self-report
// timeline, evidence and diagnostics. Recovery is a state of this view, not a separate page.
import { isAbsent } from '../contract.js';
import {
  DELIVERY, FRESHNESS, chip, dateTime, executionChip, h, item, labelled, principalLabels, shortHash, time,
  timeEl, timeSeconds,
} from '../dom.js';
import { absentPanel, dl, pageHeader, section, unknownPanel } from './common.js';

export const title = 'Work detail';

const STAGES = [
  ['produced', 'Produced'], ['builder_verified', 'Builder-verified'], ['independently_reviewed', 'Independently reviewed'],
  ['accepted', 'Accepted'], ['released', 'Released'],
];
const STAGE_AUTHORITY = { produced: 'builder', builder_verified: 'builder', independently_reviewed: 'reviewer', accepted: 'owner', released: 'owner' };

export function defaultAttempt(overview) {
  const roster = overview.roster;
  const attention = overview.attention;
  if (attention.status === 'known') {
    for (const entry of [...attention.exceptions, ...attention.decisions]) {
      if (entry.target?.view === 'work-detail') return entry.target.ref;
    }
  }
  if (roster.status === 'unavailable') return null;
  return roster.attempts[0]?.attempt_id || roster.completed_recent[0]?.attempt_id || null;
}

export async function render(ctx, params) {
  const overview = await ctx.adapter.getOverview();
  const roster = overview.roster;
  const rosterKnown = roster.status !== 'unavailable';
  const attemptId = params[0] || defaultAttempt(overview);
  const header = (extra = {}) => pageHeader({ crumb: 'Guild / Work detail', title: 'Work detail', snapshot: overview.observed_at, ...extra });

  if (!attemptId) {
    const body = rosterKnown
      ? item('p', { class: 'empty' }, `No work to show. Nothing has been assigned · roster read ${time(roster.observed_at)}.`)
      : unknownPanel('work detail', roster);
    return { node: h('div', { class: 'view view-detail' }, header(), body) };
  }

  const detail = await ctx.adapter.getAttempt(attemptId);
  if (isAbsent(detail)) {
    return { node: h('div', { class: 'view view-detail' }, header(), absentPanel(`work detail for ${attemptId}`, detail)) };
  }

  const label = principalLabels(detail.principals, rosterKnown ? roster.principals : []);
  const agents = new Map(rosterKnown ? roster.agents.map((a) => [a.principal, a]) : []);
  const idle = (p) => (agents.get(p)?.status === 'configured_not_running' ? ' (configured, not running)' : '');
  const a = detail.attempt;
  const asg = detail.assignment;
  const evidenceById = new Map(detail.evidence.map((e) => [e.id, e]));
  const recoveryOpen = detail.recovery && detail.recovery.state !== 'resolved';

  const node = h('div', { class: 'view view-detail' },
    pageHeader({
      crumb: 'Guild / Work detail',
      eyebrow: `Assignment · ${asg.work_ref.ref} · attempt ${a.number}`,
      title: asg.title,
      lead: asg.summary,
      snapshot: detail.observed_at,
      chips: [executionChip(a.execution, a.wait_reason), labelled(DELIVERY, a.delivery), labelled(FRESHNESS, a.freshness)],
    }),
    switcher(roster, a.attempt_id),
    item('p', { class: 'next-owner' },
      h('strong', null, 'Next owner: '),
      detail.next_owner.principal ? `${label(detail.next_owner.principal)}${idle(detail.next_owner.principal)} · ${detail.next_owner.action}` : 'Nobody · no action waiting'),
    recoveryOpen ? recoveryPanel(ctx, detail, label, idle) : null,
    stateSection(a),
    scopeSection(asg, label),
    assuranceSection(detail.assurance, evidenceById, label),
    timelineSection(detail.timeline, label),
    evidenceSection(detail.evidence),
    diagnosticsSection(detail.diagnostics),
    h('p', null, h('a', { class: 'link-button', href: '#work' }, 'Back to Work overview')));
  return { node };
}

function switcher(roster, current) {
  if (roster.status === 'unavailable') return null;
  const entries = [...roster.attempts, ...roster.completed_recent].filter((x) => x.attempt_id !== current);
  if (!entries.length) return null;
  return h('nav', { class: 'switcher', 'aria-label': 'Other work in this snapshot' },
    h('span', { class: 'small' }, 'Other work: '),
    ...entries.map((x) => h('a', { href: `#work-detail/${x.attempt_id}` }, x.title)));
}

function recoveryPanel(ctx, detail, label, idle) {
  const r = detail.recovery;
  const attemptId = detail.attempt.attempt_id;
  const check = r.receipt_check;
  const statusChip = check.status === 'receipt_found' ? chip('Receipt found · simulated', 'ok', { 'data-status': 'receipt_found' })
    : check.status === 'no_receipt' ? chip('No receipt found', 'bad', { 'data-status': 'no_receipt' })
      : chip('Outcome uncertain', 'warn', { 'data-status': 'outcome_uncertain' });
  const run = (type) => ctx.act({ type, target: attemptId }, [type, 'recovery-result']);
  return h('section', { class: 'recovery', 'aria-labelledby': 'recovery-h' },
    h('div', { class: 'card-head' },
      h('h2', { id: 'recovery-h' }, 'Recovery: did the result arrive?'),
      h('div', { class: 'chip-row' }, statusChip, chip(`Exception ${r.state}`, r.state === 'open' ? 'warn' : 'neutral', { 'data-status': r.state }))),
    h('p', { class: 'lead' }, 'The local work is safe. Central publication needs to be checked before anything is repeated.'),
    h('div', { class: 'two-col' },
      item('div', { class: 'card' },
        h('h3', null, 'Last confirmed state'),
        h('ol', { class: 'timeline' }, r.last_confirmed.map((e) => timelineEntry(e, label, false)))),
      item('div', { class: 'card' },
        h('h3', null, 'Recovery responsibility'),
        dl([
          ['Owner', `${label(r.recovery_owner)}${idle(r.recovery_owner)}`],
          ['Impact', r.impact],
          ['Detected', `${r.detected_by} · first ${time(r.first_observed_at)} · last ${time(r.last_observed_at)}`],
          ['Known', h('ul', { class: 'tight' }, r.known_effects.map((k) => h('li', null, k)))],
          ['Unknown', h('ul', { class: 'tight' }, r.unknown_effects.length ? r.unknown_effects.map((k) => h('li', null, k)) : h('li', null, 'Nothing listed'))],
          ['Next safe action', r.next_safe_action.summary, h('span', { class: 'small block' }, r.next_safe_action.authority_note)],
        ]))),
    item('div', { class: 'card action-box' },
      h('h3', null, 'Check before repeating anything'),
      h('p', null, 'Recovery starts with the existing operation. No new build or model run is needed to check it.'),
      h('p', { class: 'receipt-status', tabindex: '-1', 'data-focus-key': 'recovery-result' },
        h('strong', null, 'Receipt check: '),
        check.status === 'not_checked' ? `not checked yet (operation ${check.operation_id}).`
          : [check.status === 'receipt_found' ? 'Receipt found · simulated. ' : 'No receipt found. ', check.detail || '', ' Checked ', timeEl(check.checked_at, timeSeconds), '.']),
      h('div', { class: 'button-row' }, r.actions.map((act) => h('button', {
        type: 'button', class: `btn sim-action${act.type === 'check_saved_result' ? ' btn--primary' : ''}`,
        disabled: !act.enabled, 'data-focus-key': act.type, onclick: () => run(act.type),
      }, `${act.label} · simulated`))),
      r.paused ? h('p', { class: 'small' }, `Recovery paused by ${label(r.paused.by)} at ${timeSeconds(r.paused.at)} (simulated). Pausing does not resolve the exception.`) : null,
      r.actions_taken.length ? h('ul', { class: 'tight small' }, r.actions_taken.map((t) => h('li', null, `${label(t.actor)} · ${t.type.replaceAll('_', ' ')} · ${timeSeconds(t.at)} · ${t.result}`))) : null),
    h('p', { class: 'rule' }, h('strong', null, 'Rule: '), r.rule));
}

function stateSection(a) {
  return section('state', 'State',
    h('p', { class: 'small' }, 'Four independent dimensions. None of them alone means “done”.'),
    dl([
      ['Execution', executionChip(a.execution, a.wait_reason), a.step ? ` step ${a.step}` : ''],
      ['Delivery of evidence', labelled(DELIVERY, a.delivery)],
      ['Observation', labelled(FRESHNESS, a.freshness),
        h('span', { class: 'small block' }, 'Last contact: ', a.last_contact_source ? `${a.last_contact_source} · ` : '', timeEl(a.last_contact_at, timeSeconds)),
        h('span', { class: 'small block' }, 'Last progress: ', timeEl(a.last_progress_at, timeSeconds))],
      ['Runtime', `${a.runtime.client} · ${a.runtime.runtime} ${a.runtime.version} · ${a.runtime.adapter} (${a.runtime.adapter_kind === 'real' ? 'real adapter' : 'test adapter'})${a.runtime.manual ? ' · manual run' : ''}`],
      ['Host', `${a.host} · ${a.environment} · fence ${a.fence}${a.previous_attempt ? ` · previous attempt ${a.previous_attempt}` : ''}`],
      a.repo ? ['Repository', `${a.repo.repository} · ${a.repo.worktree} · base ${a.repo.base_rev}`,
        a.repo.dirty_patch_sha256 ? h('span', { class: 'small block' }, 'Dirty patch SHA-256 ', h('code', null, a.repo.dirty_patch_sha256)) : null] : null,
      ['Started / ended', `${dateTime(a.started_at)} · ${a.ended_at ? dateTime(a.ended_at) : 'not ended'}`],
    ]));
}

function scopeSection(asg, label) {
  const m = asg.mandate;
  return section('scope', 'Assignment scope',
    item('div', { class: 'card' }, dl([
      ['Work reference', h('code', null, `${asg.work_ref.kind}:${asg.work_ref.ref}`)],
      ['Spec revision', asg.spec_revision],
      ['Acceptance boundary', asg.acceptance_boundary],
      ['Requester', label(asg.requester)],
      ['Builder', label(asg.builder)],
      ['Reviewer', asg.reviewer ? label(asg.reviewer) : 'None named'],
      ['Mandate', `${m.operations.join(', ')} · host ${m.host} · expires ${dateTime(m.expires_at)} · retry limit ${m.retry_limit} · stale after ${m.stale_after_s} s · ${m.offline_allowed ? 'offline allowed' : 'offline not allowed'}`],
    ])));
}

function stageValid(key, stage, evidenceById) {
  if (stage.state !== 'done' || !stage.by || !stage.at || !stage.evidence_ref) return false;
  if (stage.authority !== STAGE_AUTHORITY[key]) return false;
  if (key === 'accepted' || key === 'released') return /^disp-/.test(stage.evidence_ref);
  const evidence = evidenceById.get(stage.evidence_ref);
  if (!evidence) return false;
  if (key === 'builder_verified') return evidence.kind === 'test' && Boolean(evidence.command && evidence.result);
  if (key === 'independently_reviewed') return evidence.kind === 'review';
  return true;
}

function assuranceSection(assurance, evidenceById, label) {
  return section('assurance', 'Assurance',
    h('p', { class: 'small' }, 'Each stage needs its own evidence and authority. A process exit or a self-report never fills one.'),
    h('ol', { class: 'stages' }, STAGES.map(([key, name], index) => {
      const stage = assurance[key];
      const valid = stageValid(key, stage, evidenceById);
      let state;
      let detail;
      if (valid) {
        state = chip('Done', 'ok', { 'data-status': 'done' });
        const evidence = evidenceById.get(stage.evidence_ref);
        detail = [
          `${key === 'accepted' ? 'Accepted by' : 'By'} ${label(stage.by)} · ${dateTime(stage.at)}`,
          h('span', { class: 'small block' },
            evidence ? `${evidence.kind} ${evidence.id}${evidence.sha256 ? ` · SHA-256 ${shortHash(evidence.sha256)}` : ''}${evidence.result ? ` · ${evidence.result}` : ''}`
              : `Disposition ${stage.evidence_ref}`),
        ];
      } else if (stage.state === 'done') {
        state = chip('Not verified', 'bad', { 'data-status': 'claimed_without_evidence' });
        detail = 'Claimed without matching evidence or authority; not counted.';
      } else {
        state = chip(stage.state === 'not_requested' ? 'Not requested' : 'Not yet', 'neutral', { 'data-status': stage.state });
        detail = stage.note || 'Not yet';
      }
      return item('li', { class: `stage${valid ? ' stage--done' : ''}` },
        h('span', { class: 'stage-name' }, `${index + 1} · ${name}`), state, h('span', { class: 'stage-detail' }, detail));
    })));
}

function timelineEntry(e, label, withItem = true) {
  const make = withItem ? item : (tag, props, ...children) => h(tag, props, ...children);
  return make('li', { class: 'timeline-entry' },
    h('span', { class: 'timeline-time' }, timeEl(e.received_at)),
    h('div', null,
      h('p', { class: 'timeline-title' }, e.title, ' ',
        e.basis === 'self_report' ? chip('Self-report', 'warn', { 'data-status': 'self_report' }) : chip('Observation', 'info', { 'data-status': 'observation' }),
        e.kind === 'heartbeat' ? chip('Heartbeat · contact, not progress', 'neutral', { 'data-status': 'heartbeat' }) : null),
      e.detail ? h('p', { class: 'small' }, e.detail) : null,
      h('p', { class: 'small' },
        `${label(e.actor)} · source time `, timeEl(e.source_observed_at, timeSeconds), ' · received ', timeEl(e.received_at, timeSeconds),
        e.step ? ` · step ${e.step}` : '', e.operation_id ? ` · operation ${e.operation_id}` : '',
        e.evidence_refs.length ? ` · evidence ${e.evidence_refs.join(', ')}` : '')));
}

function timelineSection(timeline, label) {
  return section('timeline', 'What happened',
    h('p', { class: 'small' }, 'Observation: seen by the Workshop, the monitor or the service. Self-report: what an agent said about itself.'),
    timeline.length ? h('ol', { class: 'timeline' }, timeline.map((e) => timelineEntry(e, label))) : item('p', { class: 'empty' }, 'Nothing recorded yet.'));
}

function evidenceSection(evidence) {
  return section('evidence', 'Evidence for the next person',
    evidence.length ? h('ul', { class: 'plain-list' }, evidence.map((e) => item('li', { class: 'card' },
      h('h3', null, `${e.kind} · ${e.id}`),
      dl([
        e.sha256 ? ['SHA-256', h('code', { class: 'hash' }, e.sha256)] : null,
        e.command ? ['Command', h('code', null, e.command)] : null,
        e.result ? ['Result', e.result] : null,
        e.reviewed_revision ? ['Reviewed revision', h('code', null, e.reviewed_revision)] : null,
        e.location ? ['Location', e.location] : null,
        ['Source', `${e.source} · ${dateTime(e.created)}`],
      ]))))
      : item('p', { class: 'empty' }, 'No evidence recorded. Nothing here is verified.'));
}

function diagnosticsSection(diagnostics) {
  return section('diagnostics', 'Diagnostics',
    item('div', { class: 'card' },
      h('p', null, h('a', { class: 'link-disabled sim-action', role: 'link', 'aria-disabled': 'true' }, 'Open diagnostics console (separate window) · simulated')),
      h('p', { class: 'small' }, diagnostics.note),
      diagnostics.trace_id ? h('p', { class: 'small' }, 'Trace reference ', h('code', null, diagnostics.trace_id), ' (correlation only, never authorization).') : null));
}
