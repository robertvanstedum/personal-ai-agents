import {test} from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';
class Element {
  constructor(tag) { this.tag=tag; this.children=[]; this.dataset={}; this.attrs={}; this.events={}; this.hidden=false; }
  set textContent(value) { this.text=String(value); this.children=[]; }
  get textContent() { return (this.text||'')+this.children.map(x=>x.textContent).join(''); }
  append(...nodes) { for(const n of nodes){ n.parent=this; this.children.push(n); } }
  remove(){if(this.parent)this.parent.children=this.parent.children.filter(n=>n!==this);}
  setAttribute(k,v){this.attrs[k]=v;}
  addEventListener(k,v){this.events[k]=v;}
}
function page(file) {
 const document={body:new Element('body'), createElement:t=>new Element(t),createTextNode:t=>{const e=new Element('#text');e.textContent=t;return e;}};
 const window={};vm.runInNewContext(fs.readFileSync(new URL('../../core/founders_ui/static/'+file,import.meta.url),'utf8'),{document,window});return {document,window};
}
test('notices stay absent until needed, dedupe, preserve unrelated failures and clear on recovery',()=>{
 const {document,window}=page('notices.js'),api=window.MiniMoiNotices;
 assert.equal(document.body.children.length,0);
 api.show('voice','Voice unavailable');api.show('voice','Voice unavailable');api.show('chat:1','Delivery unconfirmed');
 const region=document.body.children[0];assert.equal(region.children.length,2);
 api.clear('chat:2');assert.equal(region.children.length,2);
 api.clear('chat:1');assert.equal(region.children.length,1);
 api.show('voice','Microphone permission denied');assert.equal(region.children.length,1);
 assert.match(region.textContent,/Microphone permission denied/);
 region.children[0].children[1].events.click();assert.equal(region.hidden,true);
});
test('allowance is concise and provider text is not HTML',()=>{
 const {document,window}=page('notices.js');window.MiniMoiNotices.show('voice',"Today's voice allowance is used up ($15.10 of $20.00 reserved; details)");
 const region=document.body.children[0];assert.doesNotMatch(region.textContent,/15\.10/);assert.match(region.textContent,/another session/);
 window.MiniMoiNotices.show('voice','<img src=x onerror=alert(1)>');assert.match(region.textContent,/<img/);
 assert.equal(region.children[0].children[0].children[1].children.length,0);
});
test('agent formatting supports emphasis and code without evaluating markup',()=>{
 const {document,window}=page('message-text.js'),el=document.createElement('p');
 window.MiniMoiMessageText.render(el,'Saved **record** at `/path` <script>alert(1)</script>');
 assert.equal(el.textContent,'Saved record at /path <script>alert(1)</script>');
 assert.equal(el.children.filter(x=>x.tag==='strong').length,1);assert.equal(el.children.filter(x=>x.tag==='code').length,1);
 assert.equal(el.children.filter(x=>x.tag==='script').length,0);
 window.MiniMoiMessageText.render(el,'Replacement');assert.equal(el.textContent,'Replacement');
});
