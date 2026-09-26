// Save: one deliberate save with honest fidelity and coverage. No Room, project or classification needed.
import { COVERAGE, FIDELITY, chip, dateTime, h, item, labelled } from '../dom.js';
import { pageHeader, section } from './common.js';

export const title = 'Save';

const FIDELITY_HELP = {
  original: 'The whole source exactly as received.',
  excerpt: 'Part of the source, copied exactly. An excerpt is never complete.',
  extraction: 'Text pulled out of another format (for example a PDF).',
  reconstruction: 'Rebuilt from memory or notes; shown as derived.',
};
const SOURCES = ['Claude Chat export', 'Codex', 'Claude Code', 'Grok', 'Meeting notes', 'Other'];

// The draft survives re-renders within this page load (never stored anywhere else).
let draft = { title: '', text: '', source_application: 'Claude Chat export', fidelity: 'excerpt', coverage: 'partial', missing: '', speakers: '' };
let lastResult = null;

export async function render(ctx) {
  const form = h('form', { class: 'save-form card', novalidate: true, onsubmit: (event) => submit(event, ctx) },
    field('save-title', 'Title (optional)', h('input', { id: 'save-title', name: 'title', type: 'text', value: draft.title, autocomplete: 'off' })),
    field('save-text', 'Text to keep', h('textarea', { id: 'save-text', name: 'text', rows: '8', required: true, 'aria-describedby': 'save-text-help' }, draft.text),
      h('p', { id: 'save-text-help', class: 'small' }, 'Paste the conversation or document. Up to 2,000,000 bytes; nothing is truncated. Blank lines separate segments; "Name: text" marks a declared speaker.')),
    field('save-source', 'Source application',
      h('select', { id: 'save-source', name: 'source_application' }, SOURCES.map((s) => h('option', { value: s, selected: s === draft.source_application }, s)))),
    field('save-fidelity', 'Fidelity',
      h('select', { id: 'save-fidelity', name: 'fidelity', 'aria-describedby': 'save-fidelity-help', onchange: (e) => syncCoverage(e.target.form) },
        Object.keys(FIDELITY_HELP).map((f) => h('option', { value: f, selected: f === draft.fidelity }, FIDELITY[f][0]))),
      h('p', { id: 'save-fidelity-help', class: 'small' }, FIDELITY_HELP[draft.fidelity])),
    h('fieldset', { class: 'field' },
      h('legend', null, 'Coverage'),
      h('div', { class: 'radio-row' },
        radio('coverage', 'complete', 'Complete', draft.coverage === 'complete'),
        radio('coverage', 'partial', 'Partial', draft.coverage === 'partial')),
      h('p', { id: 'coverage-note', class: 'small' }, draft.fidelity === 'excerpt' ? 'An excerpt is partial by definition, so Complete is not available.' : 'Say whether anything from the source is missing.'),
      field('save-missing', 'Missing items (one per line)',
        h('textarea', { id: 'save-missing', name: 'missing', rows: '2', placeholder: 'attachment: diagram.png' }, draft.missing))),
    field('save-speakers', 'Declared speakers (optional, comma-separated)',
      h('input', { id: 'save-speakers', name: 'speakers', type: 'text', value: draft.speakers, autocomplete: 'off', 'aria-describedby': 'save-speakers-help' }),
      h('p', { id: 'save-speakers-help', class: 'small' }, 'Recorded as declared, unverified. You are recorded separately as the authenticated submitter.')),
    h('div', { class: 'button-row' },
      h('button', { type: 'submit', class: 'btn btn--primary sim-action', 'data-focus-key': 'save' }, 'Save · simulated')),
    h('p', { class: 'small', id: 'save-error', role: 'alert' }));
  syncCoverage(form);

  const node = h('div', { class: 'view view-save' },
    pageHeader({
      crumb: 'Records / Save', eyebrow: 'Capture without a Room', title: 'Save something worth keeping',
      lead: 'No Room, project or classification is required. Saving creates no task and starts nothing.',
    }),
    h('div', { class: 'two-col' },
      section('capture', 'Capture', form),
      h('div', null,
        section('what-happens', 'What saving does',
          h('ul', { class: 'tight' },
            h('li', null, 'Creates one record and a receipt, owned by you.'),
            h('li', null, 'Keeps the original first; extraction and indexing never hide or roll it back.'),
            h('li', null, 'Does not create a Room, session, task, classification or agent run.'),
            h('li', null, 'Words inside the text stay claims by their declared speakers, even when you upload them.'))),
        receiptSection())));
  return { node };
}

function field(id, labelText, control, ...help) {
  return h('div', { class: 'field' }, h('label', { for: id }, labelText), control, ...help);
}

function radio(name, value, text, checked) {
  const id = `${name}-${value}`;
  return h('span', { class: 'radio' }, h('input', { type: 'radio', id, name, value, checked }), h('label', { for: id }, text));
}

function syncCoverage(form) {
  const fidelity = form.elements.fidelity.value;
  const complete = form.querySelector('#coverage-complete');
  const partial = form.querySelector('#coverage-partial');
  const help = form.querySelector('#save-fidelity-help');
  if (help) help.textContent = FIDELITY_HELP[fidelity];
  const note = form.querySelector('#coverage-note');
  if (fidelity === 'excerpt') {
    complete.disabled = true;
    partial.checked = true;
    if (note) note.textContent = 'An excerpt is partial by definition, so Complete is not available.';
  } else {
    complete.disabled = false;
    if (note) note.textContent = 'Say whether anything from the source is missing.';
  }
}

function readDraft(form) {
  const e = form.elements;
  draft = {
    title: e.title.value, text: e.text.value, source_application: e.source_application.value, fidelity: e.fidelity.value,
    coverage: form.querySelector('input[name="coverage"]:checked')?.value || 'partial', missing: e.missing.value, speakers: e.speakers.value,
  };
  return {
    title: draft.title.trim() || null,
    text: draft.text,
    source_application: draft.source_application,
    fidelity: draft.fidelity,
    coverage: { state: draft.coverage, missing: draft.missing.split('\n').map((m) => m.trim()).filter(Boolean), note: null },
    declared_speakers: draft.speakers.split(',').map((s) => s.trim()).filter(Boolean),
  };
}

async function submit(event, ctx) {
  event.preventDefault();
  const form = event.target;
  const payload = readDraft(form);
  const error = form.querySelector('#save-error');
  if (!payload.text.trim()) {
    error.textContent = 'Paste the text to keep first. Nothing was saved.';
    form.elements.text.focus();
    return;
  }
  error.textContent = '';
  const result = await ctx.act({ type: 'save_record', target: 'workspace:robert', draft: payload }, ['receipt-h', 'save'], { rerender: false });
  if (!result) return;
  lastResult = result;
  if (result.outcome === 'saved') draft = { ...draft, title: '', text: '', missing: '', speakers: '' };
  await ctx.rerender(['receipt-h', 'save']);
}

function receiptSection() {
  if (!lastResult) {
    return section('receipt', 'Receipt', item('p', { class: 'empty' }, 'Nothing saved yet in this preview.'));
  }
  const r = lastResult;
  if (r.outcome !== 'saved' || !r.record) {
    return section('receipt', 'Receipt',
      item('div', { class: 'card card--warn' }, h('p', null, chip('Not saved', 'bad', { 'data-status': r.outcome }), ' ', r.message)));
  }
  const rec = r.record;
  const stages = rec.stages;
  return section('receipt', 'Receipt',
    item('div', { class: 'card card--ok receipt' },
      h('p', null, chip('Saved · simulated', 'ok', { 'data-status': 'saved' }), ' ', r.message),
      h('ol', { class: 'stages-list' },
        h('li', null, h('strong', null, 'Original: '), stages.original),
        h('li', null, h('strong', null, 'Extraction: '), stages.extraction.replaceAll('_', ' ')),
        h('li', null, h('strong', null, 'Indexing: '), stages.indexing),
        h('li', null, h('strong', null, 'Central availability: '), stages.central === 'not_connected' ? 'not connected — local proof' : stages.central)),
      h('dl', { class: 'facts' },
        h('dt', null, 'Receipt'), h('dd', null, `${rec.receipt.id} · ${dateTime(rec.receipt.committed_at)} · ${rec.receipt.durability} · production ${rec.receipt.production.replaceAll('_', ' ')}`),
        h('dt', null, 'Record'), h('dd', null, h('a', { href: `#continue/${rec.source_id}` }, `${rec.source_id}@${rec.revision}`), rec.title ? ` · ${rec.title}` : ' · untitled'),
        h('dt', null, 'SHA-256'), h('dd', null, h('code', { class: 'hash' }, rec.sha256)),
        h('dt', null, 'Submitter'), h('dd', null, `${rec.submitter.label} (authenticated)`),
        h('dt', null, 'Declared speakers'), h('dd', null, rec.declared_speakers.length ? `${rec.declared_speakers.join(', ')} — declared, unverified` : 'None declared'),
        h('dt', null, 'Fidelity'), h('dd', null, labelled(FIDELITY, rec.fidelity)),
        h('dt', null, 'Coverage'), h('dd', null, labelled(COVERAGE, rec.coverage.state),
          rec.coverage.missing.length ? h('ul', { class: 'tight' }, rec.coverage.missing.map((m) => h('li', null, `Missing: ${m}`))) : null,
          rec.coverage.note ? h('span', { class: 'small block' }, rec.coverage.note) : null)),
      h('p', { class: 'small' }, r.effects)));
}

export function resetSaveState() {
  lastResult = null;
}
