// The shared ⋯ menu's Voice, Model, Last-answer line (#661), for Master Craftsman and Chief of Staff alike: one code path,
// so the twins cannot drift. The server owns the choice (partner-settings): the owner's Voice per partner, the same on
// every device and in every new conversation. The Model is that Voice's one strong model, shown, never chosen apart, so
// a cross-provider pair cannot be picked. The page sends only the voice and the revision it saw. A change applies from
// the next message and the next call; an active call never changes provider (setBusy holds the choice meanwhile).
//
// request(method, url, body?) -> {ok, status, body}: the page's own fetch with its own write headers (MC's api.js, CoS's
// cosWriteHeaders). onVoice(provider): called with the selected voice after every read and every change.

const UNREADABLE = 'Voice and model settings could not be read.';
const NOT_CHANGED = 'The voice could not be changed. Nothing changed.';
const APPLIES = 'Applies from your next message and your next call.';

export function newKey() {
  const bytes = new Uint8Array(12);
  (globalThis.crypto || { getRandomValues: (a) => a.map(() => Math.floor(Math.random() * 256)) }).getRandomValues(bytes);
  return 'pm' + Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

export function initPartnerMenu({ root, settingsUrl, request, conversationId = () => null, onVoice = () => {}, key = newKey }) {
  if (!root) return null;
  const row = root.querySelector('[data-partner-voice-row]');
  const select = root.querySelector('[data-partner-voice]');
  const modelRow = root.querySelector('[data-partner-model-row]');
  const model = root.querySelector('[data-partner-model]');
  const last = root.querySelector('[data-partner-last]');
  const note = root.querySelector('[data-partner-note]');
  if (!row || !select || !modelRow || !model || !last || !note) return null;
  let revision = 0, selected = null, generation = 0, busy = false;
  const say = (el, text) => { el.textContent = text || ''; el.hidden = !text; };
  const names = new Map(Array.from(select.options, (o) => [o.value, o.textContent]));

  function show(view) {
    revision = view.revision;
    selected = view.voice && view.voice.selected;
    const offered = (view.voice && view.voice.options) || [];
    for (const option of Array.from(select.options)) {
      const o = offered.find((x) => x.provider === option.value);
      const name = (o && o.label) || names.get(option.value);
      option.disabled = !o || !o.available;
      option.textContent = option.disabled ? `${name} · not available here` : name;
      option.title = (o && o.reason) || '';
    }
    if (selected) select.value = selected;
    row.hidden = false;
    select.disabled = busy || !selected || select.dataset.callLive === 'true';   // a live call's provider never changes
    say(model, view.model ? view.model.text : '');
    modelRow.hidden = !view.model;
    say(last, view.last_answer);
    onVoice(selected);
  }

  function url() {
    const cid = conversationId();
    return settingsUrl + (cid ? `?conversation=${encodeURIComponent(cid)}` : '');
  }

  async function load() {
    const mine = ++generation;
    let r = null;
    try { r = await request('GET', url()); } catch (e) { r = null; }
    if (mine !== generation) return;                    // a later read or change answers for this moment
    if (r && r.ok && r.body) { show(r.body); } else say(note, UNREADABLE);
  }

  select.addEventListener('change', async () => {
    const wanted = select.value, mine = ++generation;
    select.disabled = true;
    say(note, '');
    let r = null;
    try {
      r = await request('POST', settingsUrl, { voice: wanted, revision, conversation: conversationId() || null, idempotency_key: key() });
    } catch (e) { r = null; }
    if (mine !== generation) return;
    if (r && r.ok && r.body) { show(r.body); say(note, APPLIES); return; }
    if (selected) select.value = selected;
    select.disabled = busy || !selected || select.dataset.callLive === 'true';
    say(note, (r && r.body && r.body.message) || NOT_CHANGED);
    if (r && r.body && r.body.error === 'settings_changed') await load();
  });

  return {
    load,
    voice: () => selected,
    setBusy(on) { busy = Boolean(on); select.disabled = busy || !selected || select.dataset.callLive === 'true'; },
  };
}
