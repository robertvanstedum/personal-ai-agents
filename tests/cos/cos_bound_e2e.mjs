// Driven by test_cos_bound_e2e.py: argv = endpoint, run id. Seals and sends one open and one forged call.
import {toolFactory, hook, endpointUrl} from '../../docker/cos-agent-a/plugins/cos-bound/index.mjs';
import {runSealer} from '../../docker/cos-agent-a/plugins/cos-bound/shared.mjs';
const [endpoint, run] = process.argv.slice(2);
const token = 't'.repeat(40), instance = 'a'.repeat(32);
const sessionKey = 'agent:cos-agent-a:openai-user:cos-confer:' + '0'.repeat(32);
const sealer = runSealer();
const create = toolFactory({endpoint, token, tokenFile: '/run/secrets/x', agents: ['cos-agent-a'],
                            acceptCredentialReadableBy: ['cos-agent-a'], sealer, instance});
const h = hook({sealer, agents: ['cos-agent-a'], url: endpointUrl(endpoint), token, instance});
const [tool] = create({sessionKey, agentId: 'cos-agent-a'});
const ctx = {sessionKey, agentId: 'cos-agent-a', runId: run};
const sealed = h({toolName: 'cos_actions', params: {operation: 'open', title: 'Deck for Robert'}}, {...ctx, toolCallId: 'call-1'});
const good = await tool.execute('call-1', sealed.params);
const forged = await tool.execute('call-2', {operation: 'open', title: 'Forged'});
h({toolName: 'exec', params: {command: 'soffice --convert-to pdf deck.pptx'}}, {...ctx, toolCallId: 'n1'});
await new Promise(r => setTimeout(r, 300));
console.log(JSON.stringify({good: good.details, forged: forged.details}));
