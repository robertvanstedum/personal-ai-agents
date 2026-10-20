/** Preserve provider terminal semantics. Never retry or change request options. */
export const EMPTY_NOTICE = '[MiniMoi platform notice: The model reached its output limit before producing an answer. Earlier tool actions may still have occurred; no automatic retry was made.]';

export function cutoffMessage(message) {
  if (message?.stopReason !== 'length') return message;
  const original = Array.isArray(message.content) ? message.content : [];
  // A tool call cut off at the output boundary is not a complete instruction.
  // Preserve stream content indexes while making incomplete calls non-executable.
  const content = original.map(part => part.type === 'toolCall' ? {type: 'text', text: ''} : part);
  const visible = content.some(part => part.type === 'text' && typeof part.text === 'string' && part.text.trim());
  if (!visible) content.push({type: 'text', text: EMPTY_NOTICE});
  // Non-empty partial replies already receive OpenClaw's native truncation notice.
  return {...message, content, stopReason: 'length'};
}

export function wrapCutoffStream(inner, createStream) {
  return (model, context, options) => {
    if (model?.provider !== 'minimoi-gateway' || !['minimoi-cos-agent', 'minimoi-mc-agent'].includes(model.id)) {
      return inner(model, context, options);
    }
    const base = inner(model, context, options);
    const output = createStream();
    void (async () => {
      let last;
      try {
        for await (const event of await base) {
          last = event.partial ?? event.message ?? last;
          if (event.type === 'done') {
            const message = event.reason === 'length' ? cutoffMessage(event.message) : event.message;
            if (message !== event.message) {
              const added = message.content.findIndex(part => part.type === 'text' && part.text === EMPTY_NOTICE);
              if (added >= 0) {
                output.push({type: 'text_start', contentIndex: added, partial: message});
                output.push({type: 'text_delta', contentIndex: added, delta: EMPTY_NOTICE, partial: message});
                output.push({type: 'text_end', contentIndex: added, content: EMPTY_NOTICE, partial: message});
              }
            }
            output.push({...event, message});
            output.end(message);
            return;
          }
          output.push(event);
          if (event.type === 'error') { output.end(); return; }
        }
        throw new Error('MiniMoi provider stream ended without a terminal event');
      } catch (error) {
        const reason = options?.signal?.aborted ? 'aborted' : 'error';
        const message = {...(last ?? {role: 'assistant', content: [], api: model.api, provider: model.provider,
          model: model.id, usage: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
            cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0}}, timestamp: Date.now()}),
          stopReason: reason, errorMessage: error instanceof Error ? error.message : String(error)};
        output.push({type: 'error', reason, error: message});
        output.end();
      }
    })();
    return output;
  };
}
