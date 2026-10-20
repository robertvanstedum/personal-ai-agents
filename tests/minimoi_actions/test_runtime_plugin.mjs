import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import plugin, {toolFactory} from '../../docker/cos-agent-a/plugins/mc-actions/index.mjs';

const token = 'synthetic-executor-'+'e'.repeat(32);
const endpoint = 'https://mc-actions.invalid/internal/mc/v1/actions';
const args = {operation:'fixture.observe',operation_version:1,arguments:{query:'synthetic'}};
function result(extra={}) { return {version:1,operation_id:randomUUID(),receipt_id:randomUUID(),status:'done',
  code:'fixture_verified',work_id:null,verified_state:{observed:true},links:[],confirmation:null,...extra}; }
function response(value=result()) {return new Response(JSON.stringify(value), {headers:{'content-type':'application/json'}});}
function context(session='A') {
  return {sessionKey:session,toolBindings:{minimoi:{context_reference:session.repeat(43),session_key:session,
    run_id:'run-'+session,dispatch_id:'dispatch-'+session,generation:1}},assertInvocationCurrent:()=>{}};
}
function factory(fetchImpl) {return toolFactory({endpoint,token,operations:['fixture.observe'],fetchImpl});}

test('default disabled and missing/forged binding never exposes a tool',()=>{
  let registered=false;
  plugin.register({registerTool:()=>{registered=true;}});
  assert.equal(registered,false);
  const make=factory(()=>{throw Error('must not send');});
  assert.equal(make.contextVersion,2);
  assert.equal(make.create({sessionKey:'A'}),null);
  const ctx=context();ctx.toolBindings.minimoi.principal_id='forged';
  assert.equal(make.create(ctx),null);
  const wrong=context();wrong.sessionKey='other';
  assert.equal(make.create(wrong),null);
});

test('trusted metadata is immutable, stays outside arguments, and sessions stay separate',async()=>{
  const sent=[];
  const make=factory(async(url,options)=>{sent.push({url,options});return response();});
  const a=context(),b=context('B');
  const ta=make.create(a),tb=make.create(b);
  a.toolBindings.minimoi.context_reference='Z'.repeat(43);
  const results=await Promise.all([ta.execute('a',args),tb.execute('b',args)]);
  assert.ok(results.every(r=>r.details.status==='done'));
  assert.deepEqual(sent.map(x=>x.options.headers['X-MC-Context']),['A'.repeat(43),'B'.repeat(43)]);
  for(const {url,options} of sent) {
    assert.equal(url,endpoint); assert.equal(options.redirect,'error');
    assert.equal(options.headers.Authorization,'Bearer '+token);
    assert.deepEqual(JSON.parse(options.body),args);
    assert.ok(!options.body.includes(token));
    assert.ok(!options.body.includes('context_reference'));
  }
});

test('model cannot add authority fields, arbitrary operation, oversized or non-JSON args',async()=>{
  let calls=0;const tool=factory(async()=>{calls++;return response();}).create(context());
  for(const bad of [ {...args,context_reference:'forged'}, {...args,approved:true},
      {...args,operation:'shell.execute'}, {...args,operation_version:NaN},
      {...args,arguments:{x:NaN}}, {...args,arguments:{x:'x'.repeat(65536)}},
      {...args,arguments:{x:undefined}}, {...args,arguments:['bad']} ]) {
    await assert.rejects(tool.execute('bad',bad),/arguments refused/);
  }
  assert.equal(calls,0);
});

test('revoked invocation and pre-aborted signal prevent sending',async()=>{
  let calls=0;const make=factory(async()=>{calls++;return response();});
  const ctx=context();ctx.assertInvocationCurrent=()=>{throw Error('revoked');};
  await assert.rejects(make.create(ctx).execute('a',args),/unconfirmed/);
  const controller=new AbortController();controller.abort();
  await assert.rejects(make.create(context()).execute('b',args,controller.signal),/unconfirmed/);
  assert.equal(calls,0);
});

test('late response is not injected into a superseding turn',async()=>{
  let active=true;const ctx=context();ctx.assertInvocationCurrent=()=>{if(!active)throw Error('old');};
  const tool=factory(async()=>{active=false;return response();}).create(ctx);
  await assert.rejects(tool.execute('a',args),/unconfirmed/);
});

test('failed send, invalid success and leaked credentials never become success or a retry',async()=>{
  const failures=[async()=>{throw Error('network error '+token);},
    async()=>new Response('private body',{status:503}),
    async()=>new Response('not json',{headers:{'content-type':'application/json'}}),
    async()=>response({...result(),status:'invented'}),
    async()=>response({...result(),verified_state:{token}}),
    async()=>response({...result(),verified_state:{context:'A'.repeat(43)}})];
  for(const send of failures) {
    let calls=0;
    const tool=factory(async()=>{calls++;return send();}).create(context());
    await assert.rejects(tool.execute('a',args),e=>/unconfirmed/.test(e.message)&&!e.message.includes(token));
    assert.equal(calls,1);
  }
});

test('streaming oversized response is cancelled at the byte limit',async()=>{
  let cancelled=false;
  const body=new ReadableStream({pull(c){c.enqueue(new Uint8Array(32769));},cancel(){cancelled=true;}});
  const tool=factory(async()=>new Response(body,{headers:{'content-type':'application/json'}})).create(context());
  await assert.rejects(tool.execute('a',args),/unconfirmed/);
  assert.equal(cancelled,true);
});

test('endpoint is operator fixed and credential or redirect URL tricks are refused',()=>{
  for(const url of ['file:///internal/mc/v1/actions','https://user:pass@example.test/internal/mc/v1/actions',
      endpoint+'?url=elsewhere',endpoint+'#fragment','https://example.test/other']) {
    assert.throws(()=>toolFactory({endpoint:url,token,operations:['fixture.observe']}));
  }
});
