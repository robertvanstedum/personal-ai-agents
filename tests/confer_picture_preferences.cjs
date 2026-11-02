const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const code=fs.readFileSync(require('node:path').join(__dirname,'../domains/cos/static/calm-confer.js'),'utf8');
function rig(replies){
 const label={textContent:''},attrs={},body={dataset:{picture:'shown'}},status={textContent:''},handlers={},calls=[];
 const button={disabled:true,querySelector:()=>label,setAttribute:(k,v)=>attrs[k]=v,addEventListener:(k,f)=>handlers[k]=f};
 const dialog={showModal(){this.open=true},close(){this.open=false}};
 const nodes={'picture-mode':button,'save-state':status,'confer-picture-dialog':dialog,'confer-picture-close':{addEventListener(){}}};
 const ctx={document:{body,getElementById:id=>nodes[id],querySelectorAll:()=>[]},location:{pathname:'/app/cos/ui/confer'},window:{cosHistory:{device:'synthetic-device'}},cosWriteHeaders:()=>({'X-CSRF-Token':'synthetic-csrf'}),fetch:async(url,opts)=>{calls.push({url,opts});const reply=replies.shift();if(reply instanceof Error)throw reply;return reply},encodeURIComponent};
 vm.runInNewContext(code,ctx);return {button,body,status,attrs,label,calls,click:()=>handlers.click()};
}
const ok=picture=>({ok:true,json:async()=>({picture})});const tick=()=>new Promise(r=>setImmediate(r));
(async()=>{
 let r=rig([ok('hidden'),ok('shown')]);await tick();assert.equal(r.body.dataset.picture,'hidden');await r.click();assert.equal(r.body.dataset.picture,'shown');assert.equal(r.calls[1].url,'/app/cos/ui/history/preferences');assert.equal(r.calls[1].opts.headers['X-CSRF-Token'],'synthetic-csrf');assert.deepEqual(JSON.parse(r.calls[1].opts.body),{device:'synthetic-device',picture:'shown'});
 r=rig([ok('faded'),{ok:false}]);await tick();assert.equal(r.body.dataset.picture,'shown');await r.click();assert.equal(r.body.dataset.picture,'shown');assert.match(r.status.textContent,/not saved/);assert.equal(r.button.disabled,false);
 r=rig([new Error('offline'),ok('hidden'),ok('shown')]);await tick();assert.match(r.attrs['aria-label'],/Retry/);await r.click();assert.equal(r.body.dataset.picture,'hidden');assert.equal(r.calls[1].opts.method,'GET');assert.equal(r.status.textContent,'');await r.click();assert.equal(r.calls[2].opts.method,'POST');
 let resolve;const held=new Promise(r=>resolve=r);r=rig([ok('shown'),{ok:true,json:()=>held}]);await tick();const first=r.click();await tick();await r.click();assert.equal(r.calls.length,2);assert.equal(r.button.disabled,true);resolve({picture:'hidden'});await first;assert.equal(r.body.dataset.picture,'hidden');assert.equal(r.button.disabled,false);
 r=rig([ok('shown'),ok('bogus')]);await tick();await r.click();assert.equal(r.body.dataset.picture,'shown');assert.match(r.status.textContent,/not saved/);
 console.log('5 picture preference scenarios passed: persistence, legacy mapping, failed read/write, duplicate-click exclusion, invalid response. No network.');
})().catch(e=>{console.error(e);process.exit(1)});
