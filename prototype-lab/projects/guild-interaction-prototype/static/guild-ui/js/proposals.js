// Proposal → confirm/edit/cancel → receipt, plus the queue overlay.
// Browser implementation of the proposal store; at lift it is replaced by
// server calls with the same functions (INTEGRATION_MAP.md).
import { world, addMessage, nextReceiptId, nowLabel, nowMinutes, persist, changed, newId } from './world.js';
import { label as timeLabel, parseDayTime, parseAmount, stripPayment, writeEvidenceCookie } from './capture.js';
import { announce } from './dom.js';

export const OFF_REASON = () => world.scenario.off_record_refusal ||
  'Off the record — go back on the record to attach, invite or act.';

export function workItem() {
  return world.page.item_id || world.scenario.work_item;
}

export function fmtSize(n) {
  if (typeof n !== 'number') return '';
  return n < 1024 ? `${n} B` : `${(n / 1024).toFixed(1)} KB`;
}

export function pending() {
  const open = world.conv.proposals.filter((p) => p.status === 'proposed');
  return open.length ? open[open.length - 1] : null;
}

function field(p, key) { return p.fields.find((f) => f.key === key); }

export function propose(templateKey, overrides = {}) {
  if (world.off) return { refused: OFF_REASON() };
  const tpl = world.scenario.proposals[templateKey];
  if (!tpl) return { refused: `No proposal template "${templateKey}".` };
  if (tpl.once) {
    const existing = world.conv.proposals.find((p) => p.template === templateKey && p.status !== 'cancelled');
    if (existing) return { existing };
  }
  const p = {
    id: newId('p'), template: templateKey, effect: tpl.effect, title: tpl.title, note: tpl.note || '',
    fields: (overrides.fields || tpl.fields || []).map((f) => ({ ...f })), status: 'proposed', revised: false,
    receipt: null, work_item: workItem(), created: nowLabel(),
  };
  world.conv.proposals.push(p);
  addMessage({ kind: 'proposal', who: 'mc', ref: p.id });
  persist(); changed();
  return { proposal: p };
}

export function proposeFile(att) {
  if (world.off) return { refused: OFF_REASON() };
  const tpl = world.scenario.proposals.file;
  const p = {
    id: newId('p'), template: 'file', effect: 'file', title: tpl.title, note: tpl.note, attachment: att.id,
    fields: [
      { key: 'name', label: 'File', value: att.name, editable: true },
      { label: 'Size', value: fmtSize(att.size) },
      { key: 'session', label: 'Destination session', value: world.scenario.session.id, editable: true,
        options: world.scenario.sessions_for_filing },
      { label: 'Work item', value: `#${workItem()}` },
      { label: 'Id', value: att.sha256 ? `sha256:${att.sha256.slice(0, 12)}…` : 'digest unavailable' },
      { label: 'Upload', value: 'stays in this browser; not uploaded' },
    ],
    status: 'proposed', revised: false, receipt: null, work_item: workItem(), created: nowLabel(),
  };
  world.conv.proposals.push(p);
  addMessage({ kind: 'proposal', who: 'mc', ref: p.id });
  persist(); changed();
  return { proposal: p };
}

function pushProposal(p) {
  world.conv.proposals.push(p);
  addMessage({ kind: 'proposal', who: 'mc', ref: p.id });
  persist(); changed();
  return { proposal: p };
}

// Refill receipt → proposal (vendor, amount, time only; payment details already removed).
export function proposeRefill(r, messageId) {
  if (world.off) return { refused: OFF_REASON() };
  const cap = world.page.capture;
  const bal = cap.balances.find((b) => b.id === r.source) || cap.balances[0];
  const paid = r.paid_at != null ? timeLabel(r.paid_at) : nowLabel();
  const amount = `$${r.amount.toFixed(2)}`;
  return pushProposal({
    id: newId('p'), template: 'refill', effect: 'refill', message: messageId, status: 'proposed', revised: false,
    receipt: null, work_item: workItem(), created: nowLabel(),
    title: `Record refill: ${bal.label} +${amount} · ${paid}`,
    note: r.source ? 'Simulated: stored in this browser only.' : 'Vendor not recognised in the receipt — check the balance before confirming.',
    fields: [
      { key: 'balance', label: 'Balance', value: bal.label, editable: true, options: cap.balances.map((b) => b.label) },
      { key: 'amount', label: 'Amount', value: amount, editable: true },
      { key: 'paid', label: 'Paid at', value: paid + (r.paid_at == null ? ' (no time in the receipt — using filing time)' : ''), editable: true },
      { label: 'Vendor', value: r.vendor || 'not recognised' },
      { label: 'Evidence', value: 'reported (receipt) · pasted by Robert' },
      { label: 'Kept', value: 'vendor, amount and time only · payment details removed' },
      { label: 'Effect', value: 'simulated · kept in this browser' },
    ],
  });
}

// Vendor warning → proposal (REPORTED evidence for that tool's plan).
export function proposeVendor(w, text, messageId) {
  if (world.off) return { refused: OFF_REASON() };
  const cap = world.page.capture;
  const tool = w.tool || cap.tools[0];
  return pushProposal({
    id: newId('p'), template: 'vendor', effect: 'vendor_evidence', message: messageId, status: 'proposed', revised: false,
    receipt: null, work_item: workItem(), created: nowLabel(),
    title: `Record vendor warning: ${tool}`,
    note: w.tool ? 'Simulated: stored in this browser only.' : 'Tool not named in the text — check it before confirming.',
    fields: [
      { key: 'tool', label: 'Tool', value: tool, editable: true, options: cap.tools },
      { key: 'kind', label: 'Kind', value: w.kind === 'limit_reached' ? 'limit reached' : 'warning', editable: true, options: ['warning', 'limit reached'] },
      { key: 'text', label: 'Warning', value: stripPayment(text).slice(0, 160), editable: true },
      { key: 'reset', label: 'Says resets', value: w.stated_reset_at != null ? timeLabel(w.stated_reset_at) : '—', editable: true },
      { key: 'left', label: 'Says left', value: w.stated_remaining_pct != null ? `${w.stated_remaining_pct} %` : '—', editable: true },
      { label: 'Seen', value: nowLabel() },
      { label: 'Evidence', value: 'reported · pasted by Robert' },
      { label: 'Effect', value: 'simulated · kept in this browser' },
    ],
  });
}

export function edit(id, values) {
  if (world.off) return { refused: OFF_REASON() };
  const p = world.conv.proposals.find((x) => x.id === id);
  if (!p || p.status !== 'proposed') return { refused: 'Only a pending proposal can be edited.' };
  for (const f of p.fields) {
    if (f.editable && f.key in values) {
      const v = String(values[f.key]).trim();
      if (!v) continue;
      if (f.options && !f.options.includes(v)) continue;
      f.value = v;
    }
  }
  p.revised = true;
  persist(); changed();
  return { proposal: p };
}

export function cancel(id) {
  if (world.off) return { refused: OFF_REASON() };
  const p = world.conv.proposals.find((x) => x.id === id);
  if (!p || p.status !== 'proposed') return { refused: 'Nothing pending to cancel.' };
  p.status = 'cancelled';
  addMessage({ kind: 'system', text: `Proposal cancelled · ${p.title} · no receipt` });
  persist(); changed();
  return { proposal: p };
}

export function receiptLabel(r) {
  switch (r.kind) {
    case 'file': return `simulated filing · local receipt ${r.id} · file not uploaded — ${r.name} → ${r.session}`;
    case 'decision': return `owner decision · local receipt ${r.id} · simulated — ${r.text}`;
    case 'queue': return `pending real write · local receipt ${r.id} — #${r.work_item} status ${r.from} → ${r.to}`;
    case 'needs_posted': return `posted to Needs you · local receipt ${r.id} · simulated`;
    case 'refill': return `refill recorded · local receipt ${r.id} · simulated — ${r.label} +$${Number(r.amount).toFixed(2)} · paid ${r.paid} (reported, receipt)`;
    case 'vendor': return `vendor warning recorded · local receipt ${r.id} · simulated — ${r.tool}: “${r.text}” (reported)`;
    case 'usage_shift': return `${r.title} · local receipt ${r.id} · simulated · no routing changed — ${r.action}`;
    default: return `${r.title} · local receipt ${r.id} · simulated; nothing executed`;
  }
}

function issue(r) {
  const receipt = { when: nowLabel(), ...r };
  world.overlay.receipts.push(receipt);
  addMessage({ kind: 'receipt', who: 'mc', ref: receipt.id });
  announce(`Receipt ${receipt.id}: ${receiptLabel(receipt)}`);
  return receipt;
}

export function confirm(id) {
  if (world.off) return { refused: OFF_REASON() };
  const p = world.conv.proposals.find((x) => x.id === id);
  if (!p || p.status !== 'proposed') return { refused: 'Nothing pending to confirm.' };
  let receipt;
  if (p.effect === 'file') {
    const att = world.conv.attachments.find((a) => a.id === p.attachment);
    const name = field(p, 'name').value;
    const session = field(p, 'session').value;
    receipt = issue({ id: nextReceiptId(), kind: 'file', name, session, work_item: p.work_item, proposal: p.id });
    if (att) { att.state = 'simulated-filing'; att.receipt = receipt.id; att.filed_as = name; att.session = session; }
  } else if (p.effect === 'decision') {
    const tpl = world.scenario.proposals.decision;
    const text = field(p, 'text').value;
    const owners = p.fields.find((f) => f.label === 'Owners')?.value || '';
    receipt = issue({ id: tpl.receipt, kind: 'decision', text, work_item: p.work_item, proposal: p.id });
    world.overlay.decision = { text, owners, receipt: receipt.id, session: world.scenario.session.id, when: receipt.when };
  } else if (p.effect === 'refill') {
    const cap = world.page.capture;
    const bal = cap.balances.find((b) => b.label === field(p, 'balance').value) || cap.balances[0];
    const amount = parseAmount(field(p, 'amount').value);
    if (amount == null) return { refused: 'The amount is not readable — edit it before confirming.' };
    const paidAt = parseDayTime(field(p, 'paid').value);
    const entry = { source: bal.id, vendor: p.fields.find((f) => f.label === 'Vendor').value, amount,
      paid_at: paidAt != null ? paidAt : nowMinutes() };
    world.overlay.refills.push(entry);
    receipt = issue({ id: nextReceiptId(), kind: 'refill', label: bal.label, amount, paid: timeLabel(entry.paid_at),
      work_item: p.work_item, proposal: p.id });
    evidenceChanged();
  } else if (p.effect === 'vendor_evidence') {
    const reset = parseDayTime(field(p, 'reset').value);
    const leftM = /(\d{1,3})/.exec(field(p, 'left').value);
    const entry = { tool: field(p, 'tool').value, kind: field(p, 'kind').value === 'limit reached' ? 'limit_reached' : 'warning',
      text: stripPayment(field(p, 'text').value).slice(0, 160), seen_at: nowMinutes(),
      stated_reset_at: reset, stated_remaining_pct: leftM ? Math.min(100, Number(leftM[1])) : null };
    world.overlay.vendor.push(entry);
    receipt = issue({ id: nextReceiptId(), kind: 'vendor', tool: entry.tool, text: entry.text,
      work_item: p.work_item, proposal: p.id });
    evidenceChanged();
  } else if (p.effect === 'usage_shift') {
    const action = field(p, 'action');
    receipt = issue({ id: nextReceiptId(), kind: 'usage_shift', title: p.title, action: action ? action.value : '',
      work_item: p.work_item, proposal: p.id });
  } else if (p.effect === 'needs_posted') {
    receipt = issue({ id: nextReceiptId(), kind: 'needs_posted', work_item: p.work_item, proposal: p.id });
    world.overlay.needs_posted = true;
  } else {
    receipt = issue({ id: nextReceiptId(), kind: 'next_step', title: p.title, work_item: p.work_item, proposal: p.id });
  }
  p.status = 'confirmed';
  p.receipt = receipt.id;
  if (p.message) { const m = world.conv.messages.find((x) => x.id === p.message); if (m) m.filed = true; }
  persist(); changed();
  if (p.effect === 'refill' || p.effect === 'vendor_evidence') reloadSoon();
  return { proposal: p, receipt };
}

// Owner Save on a queue card or item = the confirmation (decision Q4).
export function queueSave(itemId, from, to, note) {
  if (world.off) return { refused: OFF_REASON() };
  if (from === to) return { refused: 'Status unchanged.' };
  const receipt = issue({ id: nextReceiptId(), kind: 'queue', work_item: itemId, from, to });
  world.overlay.queue[String(itemId)] = { from, to, note: note || '', receipt: receipt.id, when: receipt.when, state: 'pending real write' };
  world.overlay.queue_history.push({ item: itemId, from, to, by: 'Robert', at: receipt.when, receipt: receipt.id });
  persist(); changed();
  return { receipt };
}

// Filed evidence is mirrored into one cookie so the server renders the light,
// table and Needs you with it; the page then reloads to show the update.
function evidenceChanged() {
  world.overlay.vendor = world.overlay.vendor.slice(-6);
  world.overlay.refills = world.overlay.refills.slice(-6);
  if (world.page.capture) writeEvidenceCookie(world.page.capture, world.overlay);
}

function reloadSoon() {
  window.setTimeout(() => window.location.reload(), 700);
}

export function receiptsFor(itemId) {
  return world.overlay.receipts.filter((r) => String(r.work_item) === String(itemId));
}
