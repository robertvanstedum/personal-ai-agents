import { definePluginEntry } from 'openclaw/plugin-sdk/plugin-entry';
import { createAssistantMessageEventStream } from 'openclaw/plugin-sdk/llm';
import { wrapCutoffStream } from './stream.mjs';
export default definePluginEntry({
  id: 'mc-cutoff-delivery', name: 'MiniMoi cutoff delivery',
  description: 'Deliver an honest notice when the model exhausts its output limit before answering.',
  register(api) {
    api.registerProvider({
      id: 'minimoi-gateway', label: 'MiniMoi gateway',
      wrapStreamFn(ctx) {
        return ctx.streamFn ? wrapCutoffStream(ctx.streamFn, createAssistantMessageEventStream) : undefined;
      },
    });
  },
});
