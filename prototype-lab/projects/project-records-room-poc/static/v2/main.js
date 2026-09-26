// Preview shell: scenario selection, hash routing, focus management and the polite live region.
// Package U wires only the fixture adapter. adapter_http.js is deliberately not imported here.
import { assertAdapter } from './contract.js';
import { DEFAULT_SESSION_ID, SCENARIOS, SCENARIO_LABELS, createFixtureAdapter } from './adapter_fixture.js';
import { h, reducedMotion } from './dom.js';
import { errorPanel, loadingPanel } from './views/common.js';
import * as collaborate from './views/collaborate.js';
import * as continueView from './views/continue.js';
import * as detail from './views/detail.js';
import * as overview from './views/overview.js';
import * as save from './views/save.js';

const VIEWS = { save, continue: continueView, collaborate, work: overview, 'work-detail': detail };
const DEFAULT_VIEW = 'work';
const FOCUSABLE = 'a[href], button, input, select, textarea, [tabindex]';

const state = { scenario: 'normal', adapter: null, generation: 0, failures: new Map() };

function readScenario() {
  const requested = new URLSearchParams(location.search).get('scenario');
  return SCENARIOS.includes(requested) ? requested : 'normal';
}

function parseHash() {
  const [view, ...params] = location.hash.replace(/^#/, '').split('/').map((part) => decodeURIComponent(part));
  return VIEWS[view] ? { view, params: params.filter(Boolean) } : { view: DEFAULT_VIEW, params: [] };
}

function announce(message) {
  const region = document.getElementById('announcer');
  region.textContent = '';
  // A fresh text node after clearing makes repeated identical messages announce again.
  requestAnimationFrame(() => { region.textContent = message; });
}

function focusElement(el) {
  if (!el) return false;
  if (el.disabled) return false;
  if (!el.matches(FOCUSABLE)) el.setAttribute('tabindex', '-1');
  el.focus({ preventScroll: true });
  el.scrollIntoView({ block: 'center', behavior: reducedMotion() ? 'auto' : 'smooth' });
  return document.activeElement === el;
}

function applyFocus(selector, mode, keys) {
  if (selector) {
    const target = document.querySelector(selector);
    if (target) {
      target.classList.add('is-target');
      if (focusElement(target)) return;
    }
  }
  if (mode === 'keys') {
    for (const key of keys) {
      const el = document.querySelector(`[data-focus-key="${CSS.escape(key)}"]`) || document.getElementById(key);
      if (focusElement(el)) return;
    }
  }
  if (mode !== 'none') focusElement(document.getElementById('view-title'));
}

function updateNav(view) {
  for (const link of document.querySelectorAll('.side-nav a[data-view]')) {
    if (link.dataset.view === view) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  }
}

async function render({ focus = 'title', keys = [] } = {}) {
  const generation = ++state.generation;
  const { view, params } = parseHash();
  const module = VIEWS[view];
  const container = document.getElementById('view');
  updateNav(view);
  document.title = `${module.title} · Preview (simulated) · mini moi`;
  container.setAttribute('aria-busy', 'true');
  container.replaceChildren(loadingPanel(module.title.toLowerCase()));
  try {
    const result = await module.render(ctx, params);
    if (generation !== state.generation) return;
    state.failures.delete(view);
    container.replaceChildren(result.node);
    applyFocus(result.focus, focus, keys);
  } catch (error) {
    if (generation !== state.generation) return;
    const attempts = (state.failures.get(view) || 0) + 1;
    state.failures.set(view, attempts);
    container.replaceChildren(errorPanel({
      what: module.title, error, attempts, onRetry: () => render({ focus: 'keys', keys: ['retry'] }),
    }));
    applyFocus(null, focus === 'none' ? 'none' : 'keys', ['retry']);
  } finally {
    if (generation === state.generation) container.setAttribute('aria-busy', 'false');
  }
}

function showActionError(action, keys, error, options) {
  const container = document.getElementById('view');
  container.querySelector('.action-error')?.remove();
  const panel = h('div', { class: 'panel panel--error action-error', role: 'alert' },
    h('p', null, h('strong', null, "Couldn't complete the simulated action. "), error.message, ' Nothing was changed.'),
    h('button', {
      type: 'button', class: 'btn btn--primary', 'data-focus-key': 'action-retry',
      onclick: async () => { panel.remove(); await ctx.act(action, keys, options); },
    }, 'Retry'));
  container.prepend(panel);
  focusElement(panel.querySelector('button'));
}

const ctx = {
  get adapter() { return state.adapter; },
  get scenario() { return state.scenario; },
  defaultSessionId: DEFAULT_SESSION_ID,
  announce,
  rerender: (keys = []) => render({ focus: 'keys', keys }),
  /** Run a simulated action, announce its result politely, then re-render keeping focus near the control. */
  async act(action, keys = [], { rerender = true } = {}) {
    const container = document.getElementById('view');
    container.setAttribute('aria-busy', 'true');
    try {
      const result = await state.adapter.act(action);
      announce(result.message);
      if (rerender) await render({ focus: 'keys', keys });
      return result;
    } catch (error) {
      announce(`Couldn't complete the simulated action: ${error.message} Nothing was changed.`);
      showActionError(action, keys, error, { rerender });
      return null;
    } finally {
      container.setAttribute('aria-busy', 'false');
    }
  },
};

function useScenario(scenario, { announceIt = true } = {}) {
  state.scenario = scenario;
  state.adapter = assertAdapter(createFixtureAdapter({ scenario }));
  state.failures.clear();
  save.resetSaveState();
  document.documentElement.dataset.scenario = scenario;
  if (announceIt) announce(`Scenario: ${SCENARIO_LABELS[scenario]}. Simulated data reloaded; earlier simulated changes were discarded.`);
}

function setupBanner() {
  const select = document.getElementById('scenario-select');
  select.replaceChildren(...SCENARIOS.map((s) => h('option', { value: s, selected: s === state.scenario }, SCENARIO_LABELS[s])));
  select.addEventListener('change', () => {
    const url = new URL(location.href);
    url.searchParams.set('scenario', select.value);
    history.replaceState(null, '', url);
    useScenario(select.value);
    render({ focus: 'none' });
  });
  document.getElementById('reset-sim').addEventListener('click', () => {
    useScenario(state.scenario, { announceIt: false });
    announce('Simulated changes discarded. Fixture data reloaded.');
    render({ focus: 'none' });
  });
  document.querySelector('.skip-link').addEventListener('click', (event) => {
    event.preventDefault();
    focusElement(document.getElementById('view-title') || document.getElementById('main'));
  });
}

useScenario(readScenario(), { announceIt: false });
setupBanner();
window.addEventListener('hashchange', () => render({ focus: 'title' }));
render({ focus: 'none' });
