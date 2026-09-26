// Shared view pieces: page header, section, loading / error / unknown / not-found states.
import { chip, dateTime, h, item, simTag, time } from '../dom.js';

export function pageHeader({ crumb, eyebrow, title, lead, chips = [], snapshot }) {
  return h('header', { class: 'page-header' },
    h('div', { class: 'crumb-row' },
      h('p', { class: 'crumb' }, crumb),
      snapshot ? h('p', { class: 'snapshot' }, `Simulated snapshot · ${dateTime(snapshot)}`) : null),
    eyebrow ? h('p', { class: 'eyebrow' }, eyebrow) : null,
    h('div', { class: 'title-row' },
      h('h1', { id: 'view-title', tabindex: '-1' }, title),
      h('div', { class: 'chip-row' }, ...chips, simTag())),
    lead ? h('p', { class: 'lead' }, lead) : null);
}

export function section(id, title, ...children) {
  return h('section', { class: 'section', 'aria-labelledby': `${id}-h` },
    h('h2', { id: `${id}-h` }, title), ...children);
}

export function loadingPanel(what) {
  return h('div', { class: 'panel panel--loading', role: 'status' },
    h('p', { class: 'loading-line' }, h('span', { class: 'pulse', 'aria-hidden': 'true' }), `Loading ${what}…`),
    h('p', { class: 'small' }, 'If this does not finish, the preview adapter is not answering. Nothing is shown until it does.'));
}

export function errorPanel({ what, error, attempts, onRetry }) {
  return h('section', { class: 'panel panel--error', role: 'alert', 'aria-labelledby': 'error-h' },
    h('h1', { id: 'view-title', tabindex: '-1', class: 'error-title' }, `Couldn't load ${what}`),
    h('p', null, error?.message || 'The adapter reported an error.'),
    h('p', { class: 'small' }, `Nothing was changed. Tried ${attempts} ${attempts === 1 ? 'time' : 'times'}.`),
    h('button', { type: 'button', class: 'btn btn--primary', 'data-focus-key': 'retry', onclick: onRetry }, 'Retry'));
}

/** The store could not be read: say Unknown, never zero or empty. */
export function unknownPanel(what, absent) {
  return item('div', { class: 'panel panel--unknown' },
    h('p', { class: 'unknown-head' }, chip('Unknown', 'unknown', { 'data-status': 'unknown' }), ` ${what}`),
    h('p', null, absent?.detail || 'This could not be read.'),
    h('p', { class: 'small' }, `Observed ${time(absent?.observed_at)}. Unknown is not zero and not empty: it is not known.`));
}

export function notFoundPanel(what, absent) {
  return item('div', { class: 'panel' },
    h('p', { class: 'unknown-head' }, chip('Not found', 'neutral', { 'data-status': 'not_found' }), ` ${what}`),
    h('p', null, absent?.detail || 'Nothing with this id exists here.'));
}

export function absentPanel(what, absent) {
  return absent?.status === 'not_found' ? notFoundPanel(what, absent) : unknownPanel(what, absent);
}

export function dl(rows) {
  return h('dl', { class: 'facts' }, ...rows.filter(Boolean).flatMap(([term, ...value]) => [h('dt', null, term), h('dd', null, ...value)]));
}
