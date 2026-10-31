// Collaborate: one session with purpose, participants (membership / authenticated contact / working kept
// separate), selected context with its disclosure, requests with next owner, and the owner's Inbox.
import { isAbsent } from '../contract.js';
import {
  FIDELITY, REQUEST_STATE, WAIT_REASON, chip, dateTime, h, item, labelled, principalLabels, time, timeEl,
} from '../dom.js';
import { absentPanel, pageHeader, section } from './common.js';

export const title = 'Collaborate';

export async function render(ctx, params) {
  const sessionId = params[0] || ctx.defaultSessionId;
  const [view, records] = await Promise.all([ctx.adapter.getSession(sessionId), ctx.adapter.listRecords()]);
  if (isAbsent(view)) {
    return {
      node: h('div', { class: 'view view-collaborate' },
        pageHeader({ crumb: 'Rooms / Collaborate', title: 'Collaborate', snapshot: view.observed_at }),
        absentPanel('session: participants, context and requests', view)),
    };
  }
  const label = principalLabels(view.principals);
  const s = view.session;
  const node = h('div', { class: 'view view-collaborate' },
    pageHeader({
      crumb: `Rooms / ${s.room.title} / ${s.title}`, eyebrow: 'Session', title: s.title,
      lead: s.purpose ? `Purpose: ${s.purpose}` : 'No purpose recorded yet.', snapshot: view.observed_at,
      chips: [chip(s.state === 'recording' ? 'Recording' : s.state === 'paused' ? 'Paused' : 'Closed', s.state === 'recording' ? 'ok' : 'neutral', { 'data-status': s.state })],
    }),
    inbox(ctx, view, label),
    participants(view, label),
    standing(view, label),
    context(ctx, view, records, label),
    requests(view, label));
  return { node };
}

function inbox(ctx, view, label) {
  const mine = view.requests.filter((r) => view.inbox.for_you.includes(r.id));
  const answered = view.requests.filter((r) => r.state === 'answered' && r.answer?.by === view.viewer);
  return section('inbox', `Inbox · for ${label(view.viewer)}`,
    mine.length ? mine.map((r) => {
      const fieldId = `answer-${r.id}`;
      return item('article', { class: 'card card--warn' },
        h('div', { class: 'card-head' }, h('h3', null, r.title), labelled(REQUEST_STATE, r.state)),
        r.body ? h('p', null, r.body) : null,
        h('p', { class: 'small' }, `From ${label(r.requester)} · ${time(r.created)} · next owner: ${label(r.next_owner)}`),
        h('form', {
          class: 'answer-form',
          onsubmit: (event) => {
            event.preventDefault();
            ctx.act({ type: 'answer_request', target: r.id, session: view.session.id, answer: event.target.elements.answer.value },
              [`answer-${r.id}`, 'inbox-h']);
          },
        },
          h('label', { for: fieldId }, 'Your answer'),
          h('textarea', { id: fieldId, name: 'answer', rows: '3', required: true }),
          h('button', { type: 'submit', class: 'btn btn--primary sim-action', 'data-focus-key': `answer-${r.id}` }, 'Answer · simulated'),
          h('p', { class: 'small' }, 'An answer is not an approval. The requester acknowledges it.')));
    }) : item('p', { class: 'empty' }, 'Nothing waiting for you in this session.'),
    answered.map((r) => item('p', { class: 'small' }, chip('Answered', 'ok', { 'data-status': 'answered' }),
      ` “${r.title}” · ${time(r.answer.at)} · next owner ${label(r.next_owner)} (${r.next_action}).`)));
}

function workingCell(w, label) {
  if ((w.state === 'working' || w.state === 'waiting') && w.freshness === 'current' && w.attempt_id && w.observed_at) {
    const text = w.state === 'waiting' ? `Waiting · ${WAIT_REASON[w.wait_reason] || w.wait_reason}` : 'Working';
    return [chip(text, 'info', { 'data-status': w.state }),
      h('span', { class: 'small block' }, h('a', { href: `#work-detail/${w.attempt_id}` }, w.attempt_id), ` · ${w.source} `, timeEl(w.observed_at))];
  }
  return [chip('Not working now', 'neutral', { 'data-status': 'not_working' }),
    w.observed_at ? h('span', { class: 'small block' }, `Last observation ${time(w.observed_at)} via ${w.source}${w.freshness === 'stale' ? ' (stale)' : ''}`) : null,
    w.note ? h('span', { class: 'small block' }, w.note) : null];
}

function participants(view, label) {
  if (!view.participants.length) return section('participants', 'Participants', item('p', { class: 'empty' }, 'No participants.'));
  const rows = view.participants.map((p) => item('tr', null,
    h('td', { 'data-label': 'Participant' }, h('strong', null, label(p.principal))),
    h('td', { 'data-label': 'Membership' }, `${p.membership.role} · since ${time(p.membership.since)}`),
    h('td', { 'data-label': 'Authenticated contact' },
      p.contact.state === 'joined'
        ? [chip('Joined', 'ok', { 'data-status': 'joined' }), h('span', { class: 'small block' }, `Join receipt ${p.contact.receipt_id} · `, timeEl(p.contact.at))]
        : [chip('No join receipt', 'neutral', { 'data-status': 'not_joined' }), h('span', { class: 'small block' }, p.contact.note || 'Not joined')]),
    h('td', { 'data-label': 'Working now' }, workingCell(p.working, label))));
  return section('participants', 'Participants',
    h('p', { class: 'small' }, 'Three separate kinds of evidence. Membership is not contact; a join receipt is contact, not presence; working needs a current observed attempt.'),
    h('table', { class: 'table', role: 'table' },
      h('caption', { class: 'visually-hidden' }, 'Participants and their evidence'),
      h('thead', null, h('tr', null, ...['Participant', 'Membership', 'Authenticated contact', 'Working now'].map((t) => h('th', { scope: 'col' }, t)))),
      h('tbody', null, rows)));
}

function standing(view, label) {
  if (!view.standing_visibility.length) return null;
  return h('div', { class: 'callout', role: 'note' }, view.standing_visibility.map((v) => item('p', null,
    h('strong', null, `${label(v.principal)} has standing read access. `), v.note)));
}

function context(ctx, view, records, label) {
  const shared = view.selected_context;
  const choices = isAbsent(records) ? [] : records.records.filter((r) => !shared.some((c) => c.source.source_id === r.source_id));
  return section('context', 'Selected context',
    h('p', { class: 'small' }, 'Only records shared into this session under a disclosure grant. Participants see these copies, not the rest of the source.'),
    shared.length ? h('ul', { class: 'plain-list' }, shared.map((c) => item('li', { class: 'card' },
      h('div', { class: 'card-head' },
        h('h3', null, h('a', { href: `#continue/${encodeURIComponent(c.source.source_id)}/r${c.source.revision}` }, c.title)),
        h('span', { class: 'chip-row' }, labelled(FIDELITY, c.fidelity), chip(c.coverage_state === 'complete' ? 'Complete coverage' : 'Partial coverage', c.coverage_state === 'complete' ? 'ok' : 'warn', { 'data-status': c.coverage_state }))),
      h('p', { class: 'small' }, h('code', null, `${c.source.source_id}@${c.source.revision}`)),
      h('p', { class: 'small' }, `Shared by ${label(c.shared_by)} · under disclosure grant ${c.grant.id} (${c.grant.scope}) · ${dateTime(c.shared_at)}`),
      h('p', { class: 'small' }, c.disclosure_note))))
      : item('p', { class: 'empty' }, 'No context shared into this session yet.'),
    isAbsent(records) ? item('p', { class: 'small' }, 'Records: Unknown (could not be read), so nothing can be shared right now.')
      : choices.length ? h('form', {
        class: 'share-form',
        onsubmit: (event) => {
          event.preventDefault();
          const [sourceId, revision] = event.target.elements.record.value.split('@');
          ctx.act({ type: 'share_selected_context', target: view.session.id, source: { source_id: sourceId, revision: Number(revision) } }, ['share', 'context-h']);
        },
      },
        h('label', { for: 'share-record' }, 'Record to share'),
        h('select', { id: 'share-record', name: 'record' }, choices.map((r) => h('option', { value: `${r.source_id}@${r.current_revision}` }, `${r.title || r.source_id} (${r.source_id}@${r.current_revision})`))),
        h('button', { type: 'submit', class: 'btn sim-action', 'data-focus-key': 'share' }, 'Share into session · simulated'),
        h('p', { class: 'small' }, "Sharing needs source-disclosure authority for this exact record and this audience. CoS's standing read does not grant it."))
        : null);
}

function requests(view, label) {
  if (!view.requests.length) return section('requests', 'Requests', item('p', { class: 'empty' }, 'No requests in this session.'));
  const rows = view.requests.map((r) => item('tr', null,
    h('td', { 'data-label': 'Request' }, h('strong', null, r.title), h('span', { class: 'small block' }, `${r.id} · ${r.kind.replaceAll('_', ' ')}`)),
    h('td', { 'data-label': 'From → to' }, `${label(r.requester)} → ${label(r.assignee)}`),
    h('td', { 'data-label': 'State' }, labelled(REQUEST_STATE, r.state), h('span', { class: 'small block' }, `Updated ${time(r.updated)}`)),
    h('td', { 'data-label': 'Next owner' }, r.next_owner ? [h('strong', null, label(r.next_owner)), h('span', { class: 'small block' }, r.next_action)] : 'Nobody · closed')));
  return section('requests', 'Requests',
    h('table', { class: 'table', role: 'table' },
      h('caption', { class: 'visually-hidden' }, 'Requests in this session'),
      h('thead', null, h('tr', null, ...['Request', 'From → to', 'State', 'Next owner'].map((t) => h('th', { scope: 'col' }, t)))),
      h('tbody', null, rows)));
}
