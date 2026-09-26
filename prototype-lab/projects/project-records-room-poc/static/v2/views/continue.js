// Continue: recent records, a record reader (declared speakers stay unverified) and "Where we are",
// whose owner decisions come only from direct owner decision records.
import { isAbsent } from '../contract.js';
import {
  COVERAGE, FIDELITY, chip, citeList, citeLink, dateTime, h, item, labelled, shortHash, time,
} from '../dom.js';
import { absentPanel, pageHeader, section } from './common.js';

export const title = 'Continue';

const PEOPLE = { robert: 'Robert', 'claude-code': 'Claude Code', codex: 'Codex', workshop: 'Workshop (mac-workshop-1)' };
const person = (id) => PEOPLE[id] || id;

export function segmentId(sourceId, revision, ordinal) {
  return `seg-${sourceId}-${revision}-${ordinal}`;
}

export async function render(ctx, params) {
  const [recordParam, revParam, segParam] = params;
  const list = await ctx.adapter.listRecords();
  const header = pageHeader({
    crumb: 'Records / Continue', eyebrow: 'Return to the actual sources', title: 'Continue where you left off',
    lead: 'Read what was saved, see where things stand, and follow every claim back to its exact segment.',
    snapshot: isAbsent(list) ? list.observed_at : list.observed_at,
  });
  if (isAbsent(list)) {
    return { node: h('div', { class: 'view view-continue' }, header, section('recent', 'Recent records', absentPanel('recent records', list))) };
  }
  if (!list.records.length) {
    return {
      node: h('div', { class: 'view view-continue' }, header,
        section('recent', 'Recent records',
          item('p', { class: 'empty' }, 'No records saved yet. ', h('a', { href: '#save' }, 'Save something'), ' — saving creates no task and starts nothing.'))),
    };
  }
  const selectedId = recordParam || list.records[0].source_id;
  const revision = revParam && /^r\d+$/.test(revParam) ? Number(revParam.slice(1)) : undefined;
  const ordinal = segParam && /^s\d+$/.test(segParam) ? Number(segParam.slice(1)) : undefined;
  const [record, interpretation] = await Promise.all([
    ctx.adapter.getRecord(selectedId, revision),
    ctx.adapter.getInterpretation(selectedId),
  ]);

  const node = h('div', { class: 'view view-continue' }, header,
    h('div', { class: 'continue-grid' },
      recentList(list, selectedId),
      reader(record, selectedId),
      whereWeAre(interpretation)));
  const focus = !isAbsent(record) && ordinal ? `#${CSS.escape(segmentId(record.source_id, record.revision, ordinal))}` : null;
  return { node, focus };
}

function recentList(list, selectedId) {
  return h('nav', { class: 'record-list', 'aria-labelledby': 'recent-h' },
    h('h2', { id: 'recent-h' }, 'Recent records'),
    h('ul', { class: 'plain-list' }, list.records.map((r) => item('li', { class: `record-link${r.source_id === selectedId ? ' is-selected' : ''}` },
      h('a', { href: `#continue/${encodeURIComponent(r.source_id)}`, 'aria-current': r.source_id === selectedId ? 'true' : null },
        r.title || `Untitled ${r.kind}`),
      h('span', { class: 'small block' }, `${r.source_id} · revision ${r.current_revision} · ${time(r.captured_at)}`),
      h('span', { class: 'chip-row' }, labelled(FIDELITY, r.fidelity), labelled(COVERAGE, r.coverage_state),
        r.authorship === 'direct' ? chip('Owner decision · direct', 'ok', { 'data-status': 'direct' }) : null)))),
    list.truncated ? h('p', { class: 'small' }, `Showing the newest ${list.limit}; older records are not listed here.`)
      : h('p', { class: 'small' }, `All ${list.records.length} records in this scenario are listed.`));
}

function reader(record, selectedId) {
  if (isAbsent(record)) return h('article', { class: 'reader', 'aria-labelledby': 'reader-h' }, h('h2', { id: 'reader-h' }, selectedId), absentPanel('record', record));
  const r = record;
  const direct = r.authorship === 'direct' && r.kind === 'owner_decision' && r.submitter.route === 'owner_decision';
  const stages = r.stages;
  return h('article', { class: 'reader', 'aria-labelledby': 'reader-h' },
    h('div', { class: 'card-head' },
      h('h2', { id: 'reader-h' }, r.title || `Untitled ${r.kind}`),
      h('div', { class: 'chip-row' }, labelled(FIDELITY, r.fidelity), labelled(COVERAGE, r.coverage.state),
        chip(`Revision ${r.revision} of ${r.current_revision}`, r.revision === r.current_revision ? 'neutral' : 'warn', { 'data-status': r.revision === r.current_revision ? 'current_revision' : 'older_revision' }))),
    r.revision !== r.current_revision ? h('div', { class: 'banner banner--warn', role: 'note' },
      h('p', null, `You are reading revision ${r.revision}. Revision ${r.current_revision} is current. `,
        h('a', { href: `#continue/${encodeURIComponent(r.source_id)}/r${r.current_revision}` }, `Read revision ${r.current_revision}`))) : null,
    r.fidelity === 'reconstruction' ? h('div', { class: 'banner banner--warn', role: 'note' },
      h('p', null, h('strong', null, 'Derived: '), 'this is a reconstruction, not the original words.')) : null,
    item('div', { class: 'card meta' },
      h('dl', { class: 'facts' },
        h('dt', null, 'Submitted by'), h('dd', null, `${person(r.submitter.principal)} (authenticated)`),
        h('dt', null, 'Declared speakers'), h('dd', null, r.declared_speakers.length ? `${r.declared_speakers.join(', ')} — declared, unverified` : 'None declared'),
        h('dt', null, 'Authorship'), h('dd', null, direct ? 'Direct owner decision (recorded through the owner decision route)' : 'Imported: statements inside are claims by their declared speakers'),
        h('dt', null, 'Captured'), h('dd', null, `${dateTime(r.capture.captured_at)} from ${r.capture.source_application}${r.capture.source_time ? ` · source time ${time(r.capture.source_time)}` : ''}`),
        h('dt', null, 'Coverage'), h('dd', null, r.coverage.state === 'complete' ? 'Complete' : r.coverage.state === 'partial' ? 'Partial' : 'Unknown',
          r.coverage.missing.length ? h('ul', { class: 'tight' }, r.coverage.missing.map((m) => h('li', null, `Missing: ${m}`))) : null,
          r.coverage.note ? h('span', { class: 'small block' }, r.coverage.note) : null),
        h('dt', null, 'Content'), h('dd', null, `${r.media_type} · ${r.size.toLocaleString('en-US')} bytes · SHA-256 `, h('code', { class: 'hash' }, shortHash(r.sha256))),
        h('dt', null, 'Receipt'), h('dd', null, `${r.receipt.id} · ${dateTime(r.receipt.committed_at)}`,
          h('ul', { class: 'tight small' },
            h('li', null, `Original: ${stages.original}`),
            h('li', null, `Extraction: ${stages.extraction.replaceAll('_', ' ')}`),
            h('li', null, `Indexing: ${stages.indexing}`),
            h('li', null, `Central availability: ${stages.central === 'not_connected' ? 'not connected — local proof' : stages.central}`))))),
    h('h3', null, 'Segments'),
    h('ol', { class: 'segments' }, r.segments.map((s) => item('li', {
      class: 'segment', id: segmentId(r.source_id, r.revision, s.ordinal), tabindex: '-1',
      'aria-label': `Segment ${s.ordinal}`,
    },
      h('p', { class: 'segment-head' },
        h('span', { class: 'segment-cite' }, `${r.source_id}@${r.revision}#${s.ordinal}`),
        s.declared_speaker ? [' · ', h('strong', null, s.declared_speaker), ' ', chip('declared, unverified', 'unverified', { 'data-status': 'declared_unverified' })]
          : direct ? [' · ', h('strong', null, `${person(r.submitter.principal)}`), ' ', chip('authenticated owner decision', 'ok', { 'data-status': 'direct' })]
            : ' · no speaker declared',
        s.source_time ? ` · ${time(s.source_time)}` : ''),
      h('p', { class: 'segment-text' }, s.text)))),
    r.relations.length ? h('p', { class: 'small' }, r.relations.map((rel) => `${rel.relation} ${rel.target_ref} (${person(rel.actor)}, ${dateTime(rel.created)})`).join(' · ')) : null);
}

function whereWeAre(interp) {
  if (isAbsent(interp)) {
    return h('aside', { class: 'where', 'aria-labelledby': 'where-h' }, h('h2', { id: 'where-h' }, 'Where we are'),
      interp.status === 'not_found' ? item('p', { class: 'empty' }, interp.detail) : absentPanel('interpretation', interp));
  }
  const body = interp.body;
  // Only direct, attributed owner decision records count as owner decisions. Anything else is reported.
  const ownerDecisions = body.owner_decisions.filter((d) => d.authorship === 'direct' && d.decided_by && d.decided_at && d.decision_record);
  const demoted = body.owner_decisions.filter((d) => !ownerDecisions.includes(d));
  const test = interp.adapter_kind === 'test';
  return h('aside', { class: 'where', 'aria-labelledby': 'where-h' },
    h('div', { class: 'card-head' },
      h('h2', { id: 'where-h' }, 'Where we are'),
      h('div', { class: 'chip-row' },
        interp.state === 'stale' ? chip('Stale', 'warn', { 'data-status': 'stale' }) : chip('Current', 'ok', { 'data-status': 'current' }),
        chip(test ? 'Test adapter' : 'Live adapter', test ? 'neutral' : 'info', { 'data-status': interp.adapter_kind }))),
    interp.state === 'stale' && interp.stale_reason ? h('div', { class: 'banner banner--warn', role: 'note' },
      h('p', null, h('strong', null, `Interpretation is stale: source ${interp.stale_reason.source_id} has revision ${interp.stale_reason.current_revision}; this view cites revision ${interp.stale_reason.cited_revision}.`),
        ` Marked stale ${time(interp.stale_since)}. It has not been rewritten.`)) : null,
    item('div', { class: 'card' },
      h('p', { class: 'small' }, `Interpreter: ${interp.adapter} (${test ? 'test adapter · deterministic · no model call' : 'live adapter'}) · requested by ${person(interp.requested_by)} · ${dateTime(interp.created)}`),
      h('p', { class: 'small' }, `Usage: ${interp.usage.status === 'unavailable' ? 'Unknown' : interp.usage.status}${interp.usage.cost_usd === null ? ' · cost: not reported' : ` · cost $${interp.usage.cost_usd}`}`),
      h('h3', null, 'Question'), h('p', null, interp.question),
      h('h3', null, 'Current understanding'), h('p', null, body.understanding)),
    points('Alternatives', body.alternatives),
    body.disagreements.length ? h('div', { class: 'where-block' }, h('h3', null, 'Disagreements'),
      body.disagreements.map((d) => item('div', { class: 'card' },
        h('p', null, h('strong', null, d.topic)),
        h('div', { class: 'positions' }, d.positions.map((p) => h('div', { class: 'position' },
          h('p', null, h('strong', null, p.holder), ' ', chip(p.holder_basis === 'declared_speaker' ? 'declared, unverified' : 'authenticated', p.holder_basis === 'declared_speaker' ? 'unverified' : 'ok', { 'data-status': p.holder_basis })),
          h('p', null, p.position), citeList(p.citations))))))) : null,
    h('div', { class: 'where-block' }, h('h3', null, 'Owner decisions'),
      h('p', { class: 'small' }, 'Only from a direct, authenticated owner decision record.'),
      ownerDecisions.length ? ownerDecisions.map((d) => item('div', { class: 'card card--ok' },
        h('p', null, d.summary),
        h('p', { class: 'small' }, `Decided by ${person(d.decided_by)} · ${dateTime(d.decided_at)} · direct owner decision record `, citeLink(d.decision_record))))
        : item('p', { class: 'empty' }, 'No owner decision recorded.')),
    h('div', { class: 'where-block' }, h('h3', null, 'Reported, unverified'),
      h('p', { class: 'small' }, 'Claims found inside sources. Uploading a source does not make its claims decisions, whoever uploaded it.'),
      [...body.reported_decisions, ...demoted.map((d) => ({ summary: d.summary, declared_by: 'not a direct record', uploaded_by: null, citations: [d.decision_record], note: 'Listed as an owner decision without a direct record, so it is shown here instead.' }))]
        .map((d) => item('div', { class: 'card card--unverified' },
          h('p', null, `“${d.summary}”`, ' ', chip('Reported, unverified', 'unverified', { 'data-status': 'reported_unverified' })),
          h('p', { class: 'small' }, `Declared by ${d.declared_by}${d.uploaded_by ? ` · uploaded by ${person(d.uploaded_by)}` : ''}`),
          h('p', { class: 'small' }, d.note), citeList(d.citations))),
      !body.reported_decisions.length && !demoted.length ? item('p', { class: 'empty' }, 'None found.') : null),
    points('Open points', body.open_points),
    body.no_decision_reached.length ? points('No decision reached', body.no_decision_reached) : null);
}

function points(heading, list) {
  return h('div', { class: 'where-block' }, h('h3', null, heading),
    list.length ? h('ul', { class: 'plain-list' }, list.map((p) => item('li', { class: 'card' }, h('p', null, p.summary), citeList(p.citations))))
      : item('p', { class: 'empty' }, 'None.'));
}
