const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const history = fs.readFileSync('domains/cos/static/cos-history-ui.js','utf8');
const template = fs.readFileSync('domains/cos/templates/cos_ui.html','utf8');
const selectSource = history.slice(history.indexOf('  async function select('), history.indexOf('  async function list('));
const createSource = history.slice(history.indexOf('  let creatingConversation'),history.indexOf("  document.getElementById('mc-page')"));
const sendSource = template.slice(template.indexOf('async function sendMessage()'),template.indexOf('// ── Files from CoS'));
function harness(ready=true) {
  const button = {disabled:false};
  const input = {disabled:false,readOnly:false,value:'Test message',focus(){}};
  let release;
  const selection = new Promise(resolve=>release=resolve);
  const calls=[];
  const ctx = {window:{cosHistory:{ready,conversationId:'old'},cosVoiceSelecting:false},
    input,sendInput:input, selecting:false, uploading:0, cid:'old', conversation:{}, generation:0,
    device:'test-device',signature:'',before:null,viewingEarlier:false,attached:[],voiceConversation:'voice',
    document:{getElementById:()=>button,querySelectorAll:()=>[button],
      dispatchEvent(){},addEventListener(){},body:{dataset:{}}}, CustomEvent:class {},
    persistDraft(){},releaseVoice:async()=>{},api:async(path,body)=>{calls.push([path,body]);await selection;return {};},
    storage:()=>null,draftKey:()=>'',pending:()=>null,renderTray(){},state:{},showTitle(){},markCurrent(){},
    reload:async()=>{},log:{scrollTop:0,scrollHeight:0,text:'old transcript',replaceChildren(){this.text='';}}};
  vm.createContext(ctx);vm.runInContext(selectSource+'\n'+createSource+'\n'+sendSource,ctx);
  return {ctx,button,input,calls,release};
}
(async()=>{
  const h=harness();
  const selecting=h.ctx.select('new',{});
  await Promise.resolve(); await Promise.resolve();
  assert.equal(h.ctx.window.cosHistory.ready,false);
  assert.equal(h.ctx.window.cosHistory.conversationId,'old');
  await h.ctx.sendMessage(); // Actual send handler must return before creating an owner request.
  assert.equal(h.calls.length,1);assert.equal(h.calls[0][0],'history/selection');
  h.release();await selecting;
  assert.equal(h.ctx.window.cosHistory.conversationId,'new');
  assert.equal(h.ctx.window.cosHistory.ready,true);
  const boot=harness(false);
  await boot.button.onclick(); // New chat cannot race the initial selection read.
  assert.equal(boot.calls.length,0);
  await assert.rejects(boot.ctx.select('stale',{}));
  assert.equal(boot.ctx.window.cosHistory.conversationId,'old');
  const failedPost=harness();
  failedPost.ctx.api=async()=>{throw new Error('selection unavailable');};
  await assert.rejects(failedPost.ctx.select('new',{}));
  assert.equal(failedPost.ctx.window.cosHistory.conversationId,'old');
  assert.equal(failedPost.ctx.log.text,'old transcript');
  assert.equal(failedPost.ctx.window.cosHistory.ready,true);
  const failedRead=harness();
  failedRead.ctx.api=async()=>({});
  failedRead.ctx.reload=async()=>{throw new Error('history unavailable');};
  await assert.rejects(failedRead.ctx.select('new',{}));
  assert.equal(failedRead.ctx.window.cosHistory.conversationId,'new');
  assert.equal(failedRead.ctx.log.text,'');
  assert.equal(failedRead.ctx.window.cosHistory.ready,true);
  const failedCreate=harness();
  failedCreate.ctx.api=async()=>{throw new Error('creation unavailable');};
  await failedCreate.button.onclick();
  assert.equal(failedCreate.ctx.window.cosHistory.conversationId,'old');
  assert.equal(failedCreate.ctx.log.text,'old transcript');
  assert.equal(failedCreate.ctx.window.cosHistory.ready,true);
  // Dev, 11 Nov: words typed while New chat opened were saved as the OLD chat's draft and vanished from view.
  const typed=harness();
  const drafts={};
  typed.input.value='old draft';
  typed.ctx.persistDraft=()=>{drafts[typed.ctx.cid]=typed.input.value;};
  typed.ctx.api=async(path)=>{if(path==='history/conversations'){typed.input.value='old draft'+'Hello new chat';return {conversation_id:'fresh'};}return {};};
  typed.ctx.closeDrawer=()=>{};typed.ctx.refreshList=()=>{};
  typed.ctx.document.getElementById=()=>({disabled:false,close(){},classList:{contains:()=>true},title:'',textContent:''});
  await typed.button.onclick();
  assert.equal(typed.ctx.window.cosHistory.conversationId,'fresh');
  assert.equal(drafts.old,'old draft');
  assert.equal(typed.input.value,'Hello new chat');
  console.log('navigation blocks sends and premature new-chat selection');
})().catch(e=>{console.error(e);process.exitCode=1;});
