// Connection loss is not an execution verdict. Match authoritative recovery by request/turn,
// never by whichever answer happens to arrive next. No dispatch or replay lives here.
export function interruptions(show) {
  const pending = new Map();
  const observed = new Set();
  return {
    track(requestId, conversationId) {
      if (!pending.has(requestId)) pending.set(requestId, {requestId, conversationId, turnId: null});
    },
    lost(requestId, conversationId, turnId = null) {
      const old = pending.get(requestId);
      pending.set(requestId, old || { requestId, conversationId, turnId });
      show(requestId, 'unknown', 'Connection interrupted. Completion is not yet confirmed. Reload to check for the saved answer.');
    },
    reconcile(view) {
      for (const p of pending.values()) {
        const e = [view.running, ...(view.recent || [])].find(e => e
          && e.note_request_id === p.requestId && e.conversation_id === p.conversationId);
        if (!e) continue;
        if (e.outcome?.reply_note_id) p.replyId = e.outcome.reply_note_id;
        if (p.replyId && observed.has(p.replyId)) {
          pending.delete(p.requestId);
          show(p.requestId, 'recovered', '');
          continue;
        }
        if (e.state === 'claimed') show(p.requestId, 'running', 'Still working. The saved answer will appear here.');
        else if (e.state === 'answered') show(p.requestId, 'saved', 'Answer saved. Retrieving it.');
        else if (e.state === 'failed') show(p.requestId, 'failed', 'This answer failed. Your message is kept.');
        else if (e.state === 'cancelled') show(p.requestId, 'cancelled', 'Stopped. Your message is kept.');
        else show(p.requestId, 'unknown', 'Completion is not confirmed. Nothing was retried.');
      }
    },
    recovered(note) {
      if (Number.isSafeInteger(note.id)) observed.add(note.id);
      for (const [key, p] of pending) {
        if ((p.replyId && note.id === p.replyId) || (p.turnId && note.turn?.turn_id === p.turnId)) {
          pending.delete(key);
          show(key, 'recovered', '');
          return key;
        }
      }
      return null;
    },
  };
}
