const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('domains/cos/static/cos-history-ui.js','utf8');
const fn=source.slice(source.indexOf('  function stopButton('),source.indexOf('  async function reload('));
(async()=>{
 let result={stop_requested:true},fail=false,calls=[],reloads=0;
 const ctx={document:{createElement:()=>({disabled:false})},state:{},signature:'old',encodeURIComponent,
 api:async(path,body)=>{calls.push([path,body]);if(fail)throw Error('not yet begun');return result;},reload:async()=>{reloads++}};
 vm.createContext(ctx);vm.runInContext(fn,ctx);
 let b=ctx.stopButton('cosc-test','t-test');let pending=b.onclick();assert.equal(b.disabled,true);await pending;
 assert.equal(calls[0][0],'history/conversations/cosc-test/turns/t-test/stop');assert.equal(b.textContent,'Stop requested');assert.equal(reloads,1);
 fail=true;b=ctx.stopButton('cosc-test','t-test');await b.onclick();assert.equal(b.disabled,false);assert.match(ctx.state.textContent,/not confirmed/);
 fail=false;result={stop_requested:false};await b.onclick();assert.equal(b.textContent,'Answer already saved');
 console.log('Stop exact identity, acknowledgement, and retryable failure pass');
})().catch(e=>{console.error(e);process.exitCode=1});
