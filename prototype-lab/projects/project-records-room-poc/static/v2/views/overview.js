// Work overview: attention first, then current assignments, the agent roster, completed work and health.
import {
  AGENT_STATUS, FRESHNESS, HEALTH, WAIT_REASON, chip, dateTime, duration, executionChip, h, item, labelled,
  principalLabels, time, timeEl,
} from '../dom.js';
import { pageHeader, section, unknownPanel } from './common.js';

export const title = 'Work overview';

export async function render(ctx) {
  const overview = await ctx.adapter.getOverview();
  const roster = overview.roster;
  const known = roster.status !== 'unavailable';
  const label = principalLabels(known ? roster.principals : []);
  const agentStatus = new Map(known ? roster.agents.map((a) => [a.principal, a]) : []);

  const view = h('div', { class: 'view view-overview' },
    pageHeader({
      crumb: 'Guild / Work overview', eyebrow: 'Your work, across agents and sessions', title: 'Work in progress',
      lead: 'What happened, what is waiting, and who takes the next step.', snapshot: overview.observed_at,
    }),
    observationLine(roster),
    known && roster.monitor.freshness !== 'current' ? staleBanner(roster) : null,
    attention(overview.attention, label, agentStatus),
    assignments(roster, label, agentStatus),
    agents(roster, label),
    completed(roster, label),
    healthFooter(overview.health));
  return { node: view };
}

function observationLine(roster) {
  if (roster.status === 'unavailable') {
    return h('p', { class: 'observation-line' }, 'Roster: ', h('strong', null, 'Unknown'), ` · the work store could not be read at ${time(roster.observed_at)}.`);
  }
  return h('p', { class: 'observation-line' },
    `Roster read ${time(roster.observed_at)} · monitor last ran `, timeEl(roster.monitor.last_run_at), ' ',
    labelled(FRESHNESS, roster.monitor.freshness),
    ` · refreshes every ${roster.refresh_s} s when live; this preview does not refresh.`);
}

function staleBanner(roster) {
  return h('div', { class: 'banner banner--warn', role: 'note' },
    h('p', null, h('strong', null, 'Stale: '),
      `the monitor last ran at ${time(roster.monitor.last_run_at)}. States below are last observed, not current. Lost contact is not proof that work stopped.`));
}

function attention(att, label, agentStatus) {
  if (att.status === 'unknown') {
    return section('attention', 'Needs attention',
      unknownPanel('open exceptions and decisions', { detail: att.detail, observed_at: att.observed_at }));
  }
  const exceptionCards = att.exceptions.map((x) => item('article', { class: 'card card--warn' },
    h('div', { class: 'card-head' },
      h('h4', null, x.title),
      chip(x.category === 'outcome_uncertain' ? 'Outcome uncertain' : x.category.replaceAll('_', ' '), 'warn', { 'data-status': x.category })),
    h('p', null, x.summary),
    h('p', { class: 'small' },
      `Impact: ${x.impact} · recovery owner: ${label(x.recovery_owner)}${notRunning(agentStatus, x.recovery_owner)} · open since ${time(x.first_observed_at)} · detected by ${x.detected_by}`),
    x.target ? h('p', null, h('a', { class: 'btn btn--primary', href: `#${x.target.view}/${x.target.ref}` }, x.next_action || 'Open')) : null));
  const decisionCards = att.decisions.map((d) => item('article', { class: 'card' },
    h('div', { class: 'card-head' }, h('h4', null, d.title), chip('Decision waiting', 'warn', { 'data-status': d.kind })),
    h('p', null, d.summary),
    h('p', { class: 'small' }, `Next owner: ${label(d.next_owner)}${notRunning(agentStatus, d.next_owner)} · since ${time(d.since)}`),
    d.target ? h('p', null, h('a', { class: 'link-button', href: `#${d.target.view}/${d.target.ref}` }, d.next_action)) : null));
  // Exceptions and decisions are separate, labelled groups so "none open" for one never reads as "nothing waiting".
  const exceptionsGroup = exceptionCards.length
    ? h('div', { class: 'attention-group' }, h('h3', null, `Exceptions (${exceptionCards.length})`), h('div', { class: 'card-list' }, exceptionCards))
    : item('p', { class: 'true-zero' }, h('strong', null, 'Exceptions: '), chip('None open', 'ok', { 'data-status': 'none' }),
      ` No open exceptions · checked by ${label(att.observer)} at ${time(att.observed_at)}.`);
  const decisionsGroup = decisionCards.length
    ? h('div', { class: 'attention-group' }, h('h3', null, `Decisions waiting (${decisionCards.length})`), h('div', { class: 'card-list' }, decisionCards))
    : item('p', { class: 'true-zero' }, h('strong', null, 'Decisions waiting: '), `none · checked by ${label(att.observer)} at ${time(att.observed_at)}.`);
  // Open exceptions come first; when there are none, the waiting decisions lead and the all-clear sits below them.
  return section('attention', 'Needs attention',
    ...(exceptionCards.length ? [exceptionsGroup, decisionsGroup] : [decisionsGroup, exceptionsGroup]));
}

function notRunning(agentStatus, principal) {
  const agent = agentStatus.get(principal);
  return agent?.status === 'configured_not_running' ? ' (configured, not running)' : '';
}

function assignments(roster, label, agentStatus) {
  if (roster.status === 'unavailable') {
    return section('assignments', 'Current assignments', unknownPanel('current assignments', roster));
  }
  if (!roster.attempts.length) {
    return section('assignments', 'Current assignments',
      item('p', { class: 'empty' }, `No work assigned · roster read ${time(roster.observed_at)}.`));
  }
  const rows = roster.attempts.map((a) => item('tr', null,
    h('td', { 'data-label': 'Work / latest evidence' },
      h('a', { class: 'row-link', href: `#work-detail/${a.attempt_id}` }, a.title),
      h('span', { class: 'small block' }, `${a.work_ref} · ${label(a.agent)} · ${a.adapter_kind === 'real' ? 'real adapter' : 'test adapter'}${a.manual ? ' · manual run' : ''}`),
      h('span', { class: 'small block' }, a.latest_evidence)),
    h('td', { 'data-label': 'Execution' },
      executionChip(a.execution, a.wait_reason),
      a.step ? h('span', { class: 'small block' }, `Step: ${a.step}`) : null,
      a.freshness === 'stale' ? h('span', { class: 'small block' }, 'Last observed state, not current') : null,
      a.elapsed_s !== null ? h('span', { class: 'small block' }, `Elapsed ${duration(a.elapsed_s)}`) : null),
    h('td', { 'data-label': 'Observation' },
      labelled(FRESHNESS, a.freshness),
      h('span', { class: 'small block' }, 'Last contact: ', a.last_contact_source ? `${a.last_contact_source} · ` : '', timeEl(a.last_contact_at)),
      h('span', { class: 'small block' }, 'Last progress: ',
        a.last_progress_basis === 'self_report' ? 'self-report · ' : a.last_progress_basis ? 'observed · ' : '', timeEl(a.last_progress_at))),
    h('td', { 'data-label': 'Next owner' },
      h('strong', null, label(a.next_owner)),
      h('span', { class: 'small block' }, (a.next_action || 'No action waiting') + notRunning(agentStatus, a.next_owner)))));
  return section('assignments', 'Current assignments',
    h('table', { class: 'table', role: 'table' },
      h('caption', { class: 'visually-hidden' }, 'Current assignments'),
      h('thead', null, h('tr', null, ...['Work / latest evidence', 'Execution', 'Observation', 'Next owner'].map((t) => h('th', { scope: 'col' }, t)))),
      h('tbody', null, rows)));
}

function agents(roster, label) {
  if (roster.status === 'unavailable') return section('roster', 'Agent roster', unknownPanel('agent roster', roster));
  if (!roster.agents.length) return section('roster', 'Agent roster', item('p', { class: 'empty' }, 'No agents are configured.'));
  const rows = roster.agents.map((a) => {
    const [text, tone] = AGENT_STATUS[a.status] || ['Unknown', 'unknown'];
    const statusText = a.status === 'waiting' && a.wait_reason ? `${text} · ${WAIT_REASON[a.wait_reason] || a.wait_reason}` : text;
    return item('tr', null,
      h('td', { 'data-label': 'Agent' }, h('strong', null, label(a.principal)), a.runtime ? h('span', { class: 'small block' }, a.runtime) : null),
      h('td', { 'data-label': 'Role' }, a.role),
      h('td', { 'data-label': 'Status' }, chip(statusText, tone, { 'data-status': a.status }),
        a.status === 'stale' ? h('span', { class: 'small block' }, 'No recent contact; may have stopped (not confirmed)') : null),
      h('td', { 'data-label': 'Current step' },
        a.current_attempt ? h('a', { href: `#work-detail/${a.current_attempt}` }, a.step || a.current_attempt) : h('span', null, 'No current attempt')),
      h('td', { 'data-label': 'Last contact' },
        a.last_contact_at ? [a.last_contact_source, ' · ', timeEl(a.last_contact_at)] : 'None observed'));
  });
  return section('roster', 'Agent roster',
    h('p', { class: 'small' }, 'Running and waiting need a current observed attempt. A configured agent is not evidence of work.'),
    h('table', { class: 'table', role: 'table' },
      h('caption', { class: 'visually-hidden' }, 'Agent roster'),
      h('thead', null, h('tr', null, ...['Agent', 'Role', 'Status', 'Current step', 'Last contact'].map((t) => h('th', { scope: 'col' }, t)))),
      h('tbody', null, rows)));
}

function completed(roster, label) {
  if (roster.status === 'unavailable') return section('completed', 'Completed', unknownPanel('completed work', roster));
  if (!roster.completed_recent.length) return section('completed', 'Completed', item('p', { class: 'empty' }, 'No completed work yet.'));
  return section('completed', 'Completed',
    h('ul', { class: 'plain-list' }, roster.completed_recent.map((c) => {
      const accepted = c.accepted && c.accepted.authority === 'owner' && c.accepted.by && c.accepted.at;
      return item('li', { class: 'completed-row' },
        h('a', { class: 'row-link', href: `#work-detail/${c.attempt_id}` }, c.title),
        h('span', { class: 'small block' }, `${c.work_ref} · ${label(c.agent)} · finished ${time(c.finished_at)}`),
        h('span', { class: 'chip-row' },
          accepted ? chip(`Accepted · ${label(c.accepted.by)} · ${dateTime(c.accepted.at)}`, 'ok', { 'data-status': 'accepted' })
            : chip('Not accepted', 'neutral', { 'data-status': 'not_accepted' }),
          c.released ? chip(`Released · ${label(c.released.by)} · ${dateTime(c.released.at)}`, 'ok', { 'data-status': 'released' })
            : chip(c.note || 'Not released', 'neutral', { 'data-status': 'not_released' })));
    })));
}

function healthFooter(health) {
  const entry = (name, e) => item('div', { class: 'health-entry' },
    h('h3', null, name),
    labelled(HEALTH, e.status),
    h('p', { class: 'small' }, e.detail),
    h('p', { class: 'small' }, e.observed_at ? ['Observed ', timeEl(e.observed_at), ` · ${e.source}`] : 'Observation: Unknown'));
  return h('footer', { class: 'health-footer', 'aria-label': 'Service health' },
    entry('Central Records', health.central_records),
    entry('Local Workshop', health.local_workshop),
    entry('Diagnostics', health.diagnostics));
}
