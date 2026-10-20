/** Zero-spend OpenClaw 2026.9.6 connector proof.
 * Run inside an ephemeral candidate agent image with --network none, a writable
 * /tmp and private /home/node/.cache tmpfs, no credentials or state volumes.
 * The operator must enforce local resource gates and preserve normal dev state.
 * Uses only a fake loopback provider; output contains synthetic requests.
 */
import http from 'node:http';
import fs from 'node:fs';
import {spawn} from 'node:child_process';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
assert.equal(fs.statSync('/opt/minimoi/openclaw-plugins/mc-cutoff-delivery/index.mjs').uid,0);
assert.ok(!fs.existsSync('/opt/minimoi/openclaw-plugins/mc-proving-tools'));
const root='/tmp/d25-proof';fs.mkdirSync(root,{recursive:true});
const config={gateway:{mode:'local',port:19192,bind:'loopback',auth:{mode:'token',token:'fake-gateway-token'},http:{endpoints:{chatCompletions:{enabled:true}}}},update:{checkOnStart:false},models:{catalogRefresh:{enabled:false},providers:{'minimoi-gateway':{baseUrl:'http://127.0.0.1:19191/v1',apiKey:'fake-no-network',api:'openai-completions',models:[{id:'minimoi-cos-agent',name:'Mock MC',contextWindow:131072,maxTokens:4096}]}}},plugins:{load:{paths:['/opt/minimoi/openclaw-plugins/mc-cutoff-delivery']},entries:{'mc-cutoff-delivery':{enabled:true},'memory-core':{enabled:false}}},cron:{enabled:false},agents:{defaults:{model:{primary:'minimoi-gateway/minimoi-cos-agent'}},entries:{'cos-agent-a':{name:'Mock MC',workspace:root+'/workspace',model:{primary:'minimoi-gateway/minimoi-cos-agent',fallbacks:[]},skills:[],heartbeat:{every:'0m'},sandbox:{mode:'off'},tools:{profile:'full',allow:['read']}}}}};
fs.mkdirSync(root+'/workspace',{recursive:true});fs.writeFileSync(root+'/workspace/fixture.txt','read-only zero-spend fixture');fs.writeFileSync(root+'/config.json',JSON.stringify(config));
let mode='empty',calls=[],round=0;
const server=http.createServer(async(req,res)=>{
 let body='';for await(const x of req)body+=x;
 const request=JSON.parse(body);calls.push({mode,messages:request.messages});
 const chunks=[];let finish='stop';
 if(mode==='empty')finish='length';
 if(mode==='partial'){chunks.push({content:'Known partial analysis.'});finish='length';}
 if(mode==='incomplete'){chunks.push({tool_calls:[{index:0,id:'call_incomplete',type:'function',function:{name:'read',arguments:'{"path":'}}]});finish='length';}
 if(mode==='tool_then_empty'){
  if(round++===0){chunks.push({tool_calls:[{index:0,id:'call_read',type:'function',function:{name:'read',arguments:JSON.stringify({path:root+'/workspace/fixture.txt'})}}]});finish='tool_calls';}else finish='length';
 }
 if(mode==='stop')chunks.push({content:'Follow-up works.'});
 res.writeHead(200,{'Content-Type':'text/event-stream'});
 for(const delta of [{role:'assistant'},...chunks])res.write('data: '+JSON.stringify({id:'chatcmpl-proof',object:'chat.completion.chunk',created:1,model:'minimoi-cos-agent',choices:[{index:0,delta,finish_reason:null}]})+'\n\n');
 res.write('data: '+JSON.stringify({id:'chatcmpl-proof',object:'chat.completion.chunk',created:1,model:'minimoi-cos-agent',choices:[{index:0,delta:{},finish_reason:finish}],usage:{prompt_tokens:10,completion_tokens:20,total_tokens:30}})+'\n\n');res.end('data: [DONE]\n\n');
});await new Promise(r=>server.listen(19191,'127.0.0.1',r));
const gateway=spawn('node',['/app/openclaw.mjs','gateway','run'],{env:{...process.env,OPENCLAW_STATE_DIR:root+'/state',OPENCLAW_CONFIG_PATH:root+'/config.json'},stdio:['ignore','pipe','pipe']});
let gatewayLog='';gateway.stdout.on('data',x=>gatewayLog+=x);gateway.stderr.on('data',x=>gatewayLog+=x);
let ready=false;for(let i=0;i<60;i++){try{const r=await fetch('http://127.0.0.1:19192/healthz');if(r.ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,500));}
async function turn(session){
 const response=await fetch('http://127.0.0.1:19192/v1/chat/completions',{method:'POST',headers:{Authorization:'Bearer fake-gateway-token','Content-Type':'application/json'},body:JSON.stringify({model:'openclaw/cos-agent-a',user:session,messages:[{role:'user',content:'Test the supplied response.'}]}),signal:AbortSignal.timeout(30000)});
 const out=await response.text();return {code:response.ok?0:response.status,out,err:''};
}
const results=[];
try{
 assert.ok(ready,'gateway_not_ready:'+gatewayLog);
 for(const scenario of ['empty','partial','incomplete','tool_then_empty']){
  mode=scenario;round=0;const before=calls.length;const session=crypto.randomUUID();const response=await turn(session);const count=calls.length-before;
  results.push({scenario,count,...response});
  assert.equal(response.code,0,JSON.stringify(results.at(-1)));assert.equal(count,scenario==='tool_then_empty'?2:1,JSON.stringify(results.at(-1)));
  assert.equal(JSON.parse(response.out).choices[0].finish_reason,'length');
  assert.match(response.out,scenario==='partial'?/Known partial analysis/:/MiniMoi platform notice/);
  mode='stop';const start=calls.length;const follow=await turn(session);results.push({scenario:scenario+'_followup',count:calls.length-start,...follow});assert.equal(follow.code,0,follow.err);assert.equal(calls.length-start,1);assert.match(follow.out,/Follow-up works/);
 }
 console.log(JSON.stringify({passed:true,results,calls:calls.length,requests:calls,gatewayLog}));
}catch(error){console.log(JSON.stringify({passed:false,error:String(error),results,calls:calls.length,requests:calls,gatewayLog}));process.exitCode=1;}finally{gateway.kill('SIGTERM');server.close();setTimeout(()=>process.exit(process.exitCode||0),2000);}
