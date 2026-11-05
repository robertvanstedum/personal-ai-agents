import { apiGet, apiPost } from './api.js';

const node = (tag, text, cls) => {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = String(text ?? '');
  if (cls) el.className = cls;
  return el;
};

// Send the digest actually displayed. Never calculate an approval from edited
// model text, and never turn loading/recovery into an owner action.
export const proposalBody = (proposal, key) => ({
  interpretation_id: proposal.interpretation_id,
  proposal_digest: proposal.proposal_digest,
  idempotency_key: key,
});

export function initTasks(page) {
  const root = document.querySelector('[data-task-view]');
  if (!root) return;
  const status = root.querySelector('[data-task-status]');
  const proposals = root.querySelector('[data-task-proposals]');
  const work = root.querySelector('[data-task-open]');
  const detail = root.querySelector('[data-task-detail]');
  const record = root.querySelector('[data-task-record]');
  let loading = false, writing = false, detailRead = 0;
  const say = (text) => { status.textContent = text; };
  const key = () => crypto.randomUUID();
  const button = (text, action) => {
    const el = node('button', text); el.type = 'button';
    el.addEventListener('click', action); return el;
  };
  async function retryKey(task, action, text) {
    // Keep only an opaque fingerprint and random operation key in this tab.
    // Reload + retyping identical words reuses the uncertain operation.
    if (!page.task_owner) throw new Error('Missing owner');
    const bytes = new TextEncoder().encode(JSON.stringify([page.urls.api, page.task_owner, task.task_id, action, text]));
    const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(b => b.toString(16).padStart(2, '0')).join('');
    const name = `task-operation:${hash}`;
    let value = sessionStorage.getItem(name);
    if (!value) { value = key(); sessionStorage.setItem(name, value); }
    return value;
  }
  function linkState(values) {
    const url = new URL(window.location.href);
    for (const name of ['task', 'proposal', 'digest']) url.searchParams.delete(name);
    for (const [name, value] of Object.entries(values)) url.searchParams.set(name, value);
    history.replaceState(null, '', url);
  }
  function facts(card, pairs) {
    const dl = node('dl');
    for (const [label, value] of pairs) {
      dl.append(node('dt', label), node('dd', value || 'Not recorded'));
    }
    card.append(dl);
  }
  function evidence(card, text) {
    const more = node('details'); more.append(node('summary', 'Record reference'), node('p', text)); card.append(more);
  }
  function failure(response) {
    if (!response.status || response.status >= 500 || response.body?.error === 'unconfirmed') {
      return 'The result is not confirmed. Keep this page or reopen its link, check the current state, and retry only if needed.';
    }
    return response.body?.message || 'The action was refused. Refresh to see the current state.';
  }
  async function send(path, body, card) {
    if (writing || loading) return null;
    writing = true;
    const controls = [...root.querySelectorAll('button, textarea')].map(el => [el, el.disabled]);
    controls.forEach(([el]) => { el.disabled = true; });
    say('Waiting for the recorded result…');
    try {
      const response = await apiPost(path, body);
      if (!response.ok) { say(failure(response)); return null; }
      return response.body;
    } catch (_) {
      say('The result is not confirmed. Refresh before trying again.'); return null;
    } finally {
      writing = false;
      controls.forEach(([el, disabled]) => { el.disabled = disabled; });
    }
  }
  function proposalCard(p) {
    const card = node('article', undefined, 'task-card');
    const terms = p.proposed?.task;
    card.append(node('h3', terms?.outcome || p.proposed?.outcome || 'Proposed work'));
    if (!terms || !p.interpretation_id || !p.proposal_digest) {
      card.append(node('p', 'This proposal is incomplete. It cannot be confirmed here.')); return card;
    }
    facts(card, [['Request', p.proposed.outcome], ['Task outcome', terms.outcome], ['Lead', terms.lead], ['Next step', terms.holder], ['Done when', terms.done_when], ['Return window', terms.return_window == null ? 'Not set' : JSON.stringify(terms.return_window)], ['Scope', p.proposed.scope], ['Recipients', (p.proposed.recipients || []).join(', ')], ['Continuing work', p.proposed.continuing ? 'Yes' : 'No']]);
    card.append(node('h4', 'Source words'));
    for (const source of p.sources || []) {
      card.append(node('blockquote', source.content || 'Source text is not included in this recovery view.'));
      evidence(card, source.source_event_id);
    }
    if (p.finishing) card.append(node('p', 'Your confirmation was recorded, but the remaining steps may need finishing. Nothing resumes until you click.'));
    evidence(card, `${p.interpretation_id}\n${p.proposal_digest}`);
    const actions = node('div', undefined, 'task-actions');
    const decisionKey = key();
    for (const [action, label] of [['confirm', p.finishing ? 'Finish confirmation' : 'Confirm this work'], ['decline', 'Decline']]) {
      if (p.finishing && action === 'decline') continue;
      actions.append(button(label, async () => {
        if (writing || loading) return;
        linkState({ proposal: p.interpretation_id, digest: p.proposal_digest });
        const result = await send(`/tasks/${action}`, proposalBody(p, `${decisionKey}-${action}`), card);
        if (!result) return;
        if (result.state === 'confirmed' && result.task_id) {
          linkState({ task: result.task_id });
          if (await refresh()) say('Confirmed. The recorded task is shown below.');
        } else if (result.state === 'declined') {
          linkState({}); if (await refresh()) say('Declined. The decision was recorded.');
        } else { say('The service returned an unexpected result. Refresh to reconcile it.'); }
      }));
    }
    card.append(actions); return card;
  }
  async function showTask(id) {
    const read = ++detailRead;
    const response = await apiGet(`/tasks/item/${encodeURIComponent(id)}`);
    if (read !== detailRead) return;
    detail.hidden = false; record.replaceChildren();
    if (!response.ok) { const message = response.body?.message || 'This task could not be read.'; record.append(node('p', message)); say(message); return false; }
    const task = response.body.task;
    const card = node('article', undefined, 'task-card');
    card.append(node('h3', task.outcome));
    facts(card, [['Status', task.state], ['Lead', task.lead], ['Next step', task.holder], ['Done when', task.done_when]]);
    evidence(card, `${task.task_id}\nRevision ${task.revision}`);
    if (['submitted', 'working', 'waiting'].includes(task.state)) {
      const label = node('label', 'What should change?');
      const input = node('textarea'); input.maxLength = 4000; input.placeholder = 'Write the correction or reason for cancellation.';
      label.append(input); card.append(label);
      const actions = node('div', undefined, 'task-actions');
      for (const [action, caption] of [['correct', 'Record correction'], ['cancel', 'Cancel task']]) {
        actions.append(button(caption, async () => {
          const text = input.value.trim();
          if (!text) { say('Write the correction or cancellation reason first.'); input.focus(); return; }
          let operationKey;
          try { operationKey = await retryKey(task, action, text); }
          catch (_) { say('This browser could not preserve a safe retry. No action was sent.'); return; }
          const result = await send(`/tasks/${action}`, { task_id: task.task_id, text, idempotency_key: operationKey }, card);
          if (!result) return;
          if (result.state === 'correction_recorded') {
            say('Correction recorded. The task has not been replaced yet; CoS must propose the new reading for your confirmation.');
          } else if (result.state === 'cancel_recorded') {
            if (!await showTask(task.task_id)) return;
            say('Cancellation recorded. Existing external effects may still need reconciliation.');
            if (result.open_effects?.length) record.append(node('p', `Unresolved effects: ${JSON.stringify(result.open_effects)}`));
          } else say('The service returned an unexpected result. Refresh to reconcile it.');
        }));
      }
      card.append(actions);
    }
    record.append(card);
    return true;
  }
  async function refresh() {
    if (loading || writing) return;
    loading = true; detailRead++; say('Reading the current record…');
    proposals.replaceChildren(); work.replaceChildren(); detail.hidden = true;
    try {
      const response = await apiGet('/tasks/view');
      if (!response.ok) { say(response.body?.message || 'Task tracking is unavailable.'); return; }
      const data = response.body;
      for (const p of data.proposals || []) proposals.append(proposalCard(p));
      if (!data.proposals?.length) proposals.append(node('p', 'No proposals waiting for your decision.'));
      for (const row of data.work || []) {
        const card = node('article', undefined, 'task-card'); card.append(node('h3', row.outcome || 'Awaiting an answer'));
        facts(card, [['Status', row.state], ['Lead', row.lead], ['Next step', row.holder]]);
        if (row.type === 'task') card.append(button('Open task', () => { if (loading || writing) return; linkState({ task: row.id }); showTask(row.id); }));
        work.append(card);
      }
      if (!data.work?.length) work.append(node('p', 'No open work recorded. Completed work stays in its task link.'));
      if (data.more) work.append(node('p', `Showing the first 20 of ${data.total} open items.`));
      const query = new URLSearchParams(location.search);
      if (query.has('proposal') && query.has('digest')) {
        const iid = query.get('proposal');
        if (!(data.proposals || []).some((p) => p.interpretation_id === iid)) {
          const recovered = await apiGet(`/tasks/recover?interpretation_id=${encodeURIComponent(iid)}&proposal_digest=${encodeURIComponent(query.get('digest'))}`);
          if (recovered.ok) proposals.append(proposalCard(recovered.body.proposal));
          else { say(recovered.body?.message || 'The previous decision needs reconciliation.'); return; }
        }
      }
      if (query.has('task') && !await showTask(query.get('task'))) return false;
      say('Current record loaded. No action is taken until you choose it.');
      return true;
    } catch (_) { say('The current record could not be read. Try Refresh.');
    } finally { loading = false; }
  }
  root.querySelector('[data-task-refresh]').addEventListener('click', refresh);
  refresh();
}
