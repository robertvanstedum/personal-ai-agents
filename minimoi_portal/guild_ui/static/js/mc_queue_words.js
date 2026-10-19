// What the waiting-messages line and chips say (issue #346 P3). Pure: no page, no network; tested in node.
// Each phrase claims only what the server's ledger view says. Waiting means not sent yet.
const ORD = ['next', '2nd', '3rd', '4th', '5th'];
export const place = (position) => (position === 1 ? 'next to send' : `${ORD[position - 1] || `${position}th`} in line`);

export function chipWords(position, running) {
  return position === 1 && !running ? 'Waiting · next to send' : `Waiting · ${place(position)} · not sent yet`;
}

export function pauseWords(pause, waiting) {
  const held = waiting ? ` ${waiting === 1 ? '1 message waits' : `${waiting} messages wait`} until you continue.` : ' Nothing more is sent until you continue.';
  return pause.reason === 'unknown'
    ? `Paused: an earlier message's outcome is unknown. It may have run, or may still be running.${held}`
    : `Paused: an earlier message failed, or could not be sent as it was.${held}`;
}

export function notSentWords(entry) {
  const reason = entry.outcome && entry.outcome.reason;
  if (entry.state === 'expired') return 'Not sent · it waited more than 24 hours';
  if (entry.state === 'cancelled' && reason === 'earlier_expired') return 'Not sent · cancelled because an earlier message expired';
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
  return view.running ? `${what}${where} · sent one at a time, after the current answer` : `${what}${where}`;
}

// Retry after a refusal that may pass (MC or a store not ready): 5 s, 15 s, 30 s, then every 60 s. Never a spin.
export const nextDelay = (prev) => (prev ? Math.min(60000, prev < 15000 ? 15000 : prev * 2) : 5000);
