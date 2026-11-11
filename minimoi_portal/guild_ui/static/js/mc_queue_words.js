// What the waiting-messages line and chips say (issue #346 P3). Pure: no page, no network; tested in node.
// Each phrase claims only what the server's ledger view says. Waiting means not sent yet.
const ORD = ['next', '2nd', '3rd', '4th', '5th'];
export const place = (position) => (position === 1 ? 'next to send' : `${ORD[position - 1] || `${position}th`} in line`);

export function chipWords(position, running) {
  return `Not sent · ${place(position)}`;
}

// When it paused, what is uncertain, and what Continue does (Dev, 11 Nov 2026: "a strange message ... not clear to me").
const timeOf = (iso) => { const d = new Date(iso || ''); return Number.isNaN(d.getTime()) ? '' : d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }); };
export function pauseWords(pause, waiting) {
  const at = timeOf(pause.created_at);
  const head = `Paused${at ? ` at ${at}` : ''}: `;
  const what = pause.reason === 'unknown'
    ? 'an earlier message lost its connection. It may have run, or may still be running.'
    : "an earlier message didn't get an answer (it failed, or could not be sent as it was).";
  const next = waiting
    ? ` ${waiting === 1 ? '1 waiting message is' : `${waiting} waiting messages are`} held; Continue sends ${waiting === 1 ? 'it' : 'them'}.`
    : ' Continue lets new messages go through again.';
  return head + what + next;
}

export function notSentWords(entry) {
  const reason = entry.outcome && entry.outcome.reason;
  if (entry.state === 'expired') return 'Not sent · it waited more than 24 hours';
  if (entry.state === 'cancelled' && reason === 'earlier_expired') return 'Not sent · cancelled because an earlier message expired';
  if (entry.state === 'cancelled' && reason === 'owner_stop_acknowledged') return 'Stopped · cancellation confirmed; no answer saved';
  if (entry.state === 'cancelled') return 'Not sent · cancelled';
  return null;
}

export function barWords(view, here) {
  const waiting = view.queued.length;
  if (view.pause) return pauseWords(view.pause, waiting);
  if (!waiting) return '';
  const elsewhere = view.queued.filter((e) => e.conversation_id !== here).length;
  const where = elsewhere ? ` (${elsewhere} in another conversation)` : '';
  const what = waiting === 1 ? '1 message waiting' : `${waiting} messages waiting`;
  return `${what}${where}`;
}

// Retry after a refusal that may pass (MC or a store not ready): 5 s, 15 s, 30 s, then every 60 s. Never a spin.
export const nextDelay = (prev) => (prev ? Math.min(60000, prev < 15000 ? 15000 : prev * 2) : 5000);
