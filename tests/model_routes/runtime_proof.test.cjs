const assert = require('node:assert/strict');
// MINIMOI_RUNTIME_PROOF_MODULE lets the same checks run against another build of the tracker (e.g. the pre-#606 one).
const {tracker} = require(process.env.MINIMOI_RUNTIME_PROOF_MODULE || '../../core/model_routes/runtime_proof.cjs');
const run = 'chatcmpl_11111111-1111-4111-8111-111111111111';
const ctx = {runId: run, agentId: 'mc-chat', sessionKey: 'agent:mc-chat:openai-user:fixture'};
const instance = 'a'.repeat(32);
(async () => {
  const states = new Map(), receipts = [];
  const options = {instance, states, publish: async r => receipts.push(r)};
  const a = tracker(options), b = tracker(options);
  a.modelStarted({callId: 'call-1'}, ctx);
  b.modelStarted({callId: 'call-1'}, ctx); // duplicate registration does not invent a second model call
  await a.ended({success: true, messages: ['must never leave runtime']}, ctx);
  await b.ended({success: true}, ctx);
  assert.equal(receipts.length, 1);
  assert.equal(receipts[0].model_calls, 1);
  assert.equal(receipts[0].tool_calls, 0);
  assert.equal(JSON.stringify(receipts).includes('must never'), false);
  assert.equal(b.beforeTool({toolName: 'exec'}, {...ctx, toolCallId: 'late'}).block, true);
  assert.equal(a.beforeTool({}, {}).block, true);
  assert.equal(a.beforeTool({}, {...ctx, runId: 'broken'}).block, true);
  const native = {...ctx, runId: '33333333-3333-4333-8333-333333333333'};
  let sealed = false;
  const existingSeal = () => {sealed = true;};
  assert.equal(a.beforeTool({}, native) || existingSeal(), undefined);
  assert.equal(sealed, true);
  a.modelStarted({callId:'native'},native);
  await a.ended({success:true},native);
  assert.equal(receipts.length,1);
  assert.equal(a.beforeTool({runId:run},native).block,true);
  const other = {...ctx, runId: run.replace('11111111', '22222222')};
  assert.equal(a.beforeTool({}, other).block, true); // no start evidence
  a.modelStarted({callId: 'call-2'}, other);
  assert.equal(a.beforeTool({runId:native.runId},other).block,true);
  assert.equal(a.beforeTool({toolName: 'unlisted_native'}, {...other, toolCallId: 'tool-1'}), undefined);
  await a.ended({success: true}, other);
  assert.equal(receipts[1].tool_calls, 1);
  const limited = tracker({instance, states: new Map(), limit: 0, publish: async () => assert.fail('untracked receipt')});
  limited.modelStarted({callId: 'x'}, ctx);
  assert.equal(limited.beforeTool({}, ctx).block, true);
  await limited.ended({success: true}, ctx);
  await retryScenarios();
  console.log('runtime proof: passed');
})().catch(e => {console.error(e); process.exitCode = 1;});

/** #606: OpenClaw ends an attempt with success=false on a 429, waits, and retries the SAME runId. */
function fakeTimers() {
  let next = 0;
  const pending = new Map();
  return {pending, set: (fn, ms) => { pending.set(++next, {fn, ms}); return next; }, clear: id => { pending.delete(id); },
    fire: () => { const due = [...pending.values()]; pending.clear(); due.forEach(t => t.fn()); }};
}
const flush = () => new Promise(r => setImmediate(r));
async function retryScenarios() {
  const id = n => ({...ctx, runId: run.replace('11111111', n)});
  // Every publish asserts the run is already fenced: no tool can run under a receipt, even while it is being written.
  function harness() {
    const timers = fakeTimers(), receipts = [], states = new Map();
    let t;
    const publish = async r => {
      assert.equal(t.beforeTool({}, {...id(r.run.slice(9, 17)), toolCallId: 'during-publish'}).block, true);
      receipts.push(r);
    };
    t = tracker({instance, states, publish, timers, settleMs: 1000});
    return {t, timers, receipts, states, other: tracker({instance, states, publish, timers, settleMs: 1000})};
  }

  { // 429 mid-turn -> error end -> retry -> tools work -> one success receipt covering every attempt
    const {t, timers, receipts} = harness(), c = id('44444444');
    t.modelStarted({callId: 'attempt-1'}, c);
    assert.equal(t.beforeTool({}, {...c, toolCallId: 'before-429'}), undefined);
    await t.ended({success: false, error: '429 rate limit'}, c);
    assert.equal(receipts.length, 0, 'an error end that may be retried publishes nothing');
    assert.equal(timers.pending.size, 1);
    assert.match(t.beforeTool({}, {...c, toolCallId: 'between'}).blockReason, /between attempts/);
    t.modelStarted({callId: 'attempt-2'}, c);                     // transient same-model retry
    assert.equal(timers.pending.size, 0, 'the retry cancels settlement');
    for (let i = 0; i < 9; i++)                                   // the nine minimoi_host_list calls seen on Dev
      assert.equal(t.beforeTool({toolName: 'minimoi_host_list'}, {...c, toolCallId: 'host-list-' + i}), undefined);
    await t.ended({success: true}, c);
    assert.equal(receipts.length, 1);
    assert.deepEqual([receipts[0].success, receipts[0].model_calls, receipts[0].tool_calls], [true, 2, 10]);
    assert.match(t.beforeTool({}, {...c, toolCallId: 'after'}).blockReason, /has finished/, 'success still tombstones');
    t.modelStarted({callId: 'attempt-3'}, c);
    assert.equal(t.beforeTool({}, {...c, toolCallId: 'zombie'}).block, true);
    await t.ended({success: true}, c);
    timers.fire(); await flush();
    assert.equal(receipts.length, 1, 'one receipt per run');
  }

  { // repeated 429s across several retries still end in exactly one receipt
    const {t, timers, receipts} = harness(), c = id('55555555');
    for (let i = 0; i < 3; i++) {
      t.modelStarted({callId: 'try-' + i}, c);
      assert.equal(t.beforeTool({}, {...c, toolCallId: 'tool-' + i}), undefined);
      await t.ended({success: false}, c);
    }
    t.modelStarted({callId: 'try-final'}, c);
    await t.ended({success: true}, c);
    assert.equal(timers.pending.size, 0);
    assert.deepEqual([receipts.length, receipts[0].model_calls, receipts[0].tool_calls], [1, 4, 3]);
  }

  { // an error end with no retry settles: tombstone first, then a success=false receipt; a late attempt stays fenced
    const {t, timers, receipts} = harness(), c = id('66666666');
    t.modelStarted({callId: 'only'}, c);
    await t.ended({success: false}, c);
    assert.equal(timers.pending.get([...timers.pending.keys()][0]).ms, 1000);
    timers.fire(); await flush();
    assert.equal(receipts.length, 1);
    assert.deepEqual([receipts[0].success, receipts[0].model_calls, receipts[0].tool_calls], [false, 1, 0]);
    t.modelStarted({callId: 'too-late'}, c);
    assert.equal(t.beforeTool({}, {...c, toolCallId: 'after-receipt'}).block, true, 'never a tool under a published receipt');
    await t.ended({success: true}, c);
    timers.fire(); await flush();
    assert.equal(receipts.length, 1);
  }

  { // a settle timer that outlives its error end does nothing once a retry has started
    const {t, timers, receipts} = harness(), c = id('77777777');
    t.modelStarted({callId: 'a'}, c);
    await t.ended({success: false}, c);
    const stale = [...timers.pending.values()][0].fn;
    t.modelStarted({callId: 'b'}, c);
    stale(); await flush();
    assert.equal(receipts.length, 0);
    assert.equal(t.beforeTool({}, {...c, toolCallId: 'still-open'}), undefined);
  }

  { // duplicate plugin registration shares one row and one settle timer
    const {t, other, timers, receipts} = harness(), c = id('88888888');
    t.modelStarted({callId: 'd'}, c); other.modelStarted({callId: 'd'}, c);
    await t.ended({success: false}, c); await other.ended({success: false}, c);
    assert.equal(timers.pending.size, 1);
    timers.fire(); await flush();
    assert.equal(receipts.length, 1);
  }

  { // a failing publish from the settle timer never becomes an unhandled rejection, and the run stays fenced
    const timers = fakeTimers(), c = id('99999999');
    const t = tracker({instance, states: new Map(), timers, settleMs: 1, publish: async () => { throw Error('relay down'); }});
    t.modelStarted({callId: 'x'}, c);
    await t.ended({success: false}, c);
    timers.fire(); await flush();
    assert.equal(t.beforeTool({}, {...c, toolCallId: 'y'}).block, true);
  }

  { // native CLI runs are unchanged: no tracking, no timers, no receipts, their own tool policy
    const {t, timers, receipts} = harness(), n = {...ctx, runId: '44444444-3333-4333-8333-333333333333'};
    t.modelStarted({callId: 'n1'}, n);
    await t.ended({success: false}, n);
    assert.equal(t.beforeTool({}, {...n, toolCallId: 'n-tool'}), undefined);
    t.modelStarted({callId: 'n2'}, n);
    await t.ended({success: true}, n);
    assert.equal(t.beforeTool({}, {...n, toolCallId: 'n-tool-2'}), undefined);
    assert.equal(timers.pending.size, 0);
    assert.equal(receipts.length, 0);
  }
}
