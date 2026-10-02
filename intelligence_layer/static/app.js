'use strict';
const $ = id => document.getElementById(id);
async function call(path, body = {}) {
  const response = await fetch('/api/' + path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Request failed');
  $('details').textContent = JSON.stringify(data, null, 2);
  return data;
}
async function run(action) {try {$('notice').textContent = ''; await action();} catch(e) {$('notice').textContent = e.message;}}
$('login').onsubmit = e => {e.preventDefault();run(async()=>{await call('login',{token:$('token').value});$('token').value='';$('login').hidden=true;$('workspace').hidden=false;await call('status');});};
$('demo').onclick=()=>run(async()=>{const r=await call('demo');$('notice').textContent=`${r.new_records} new synthetic source records. Try “citations”, “graph” or “correction”.`;});
$('status').onclick=()=>run(()=>call('status'));
$('rebuild').onclick=()=>run(async()=>{await call('rebuild');$('notice').textContent='Index rebuilt from preserved files.';});
$('search').onsubmit=e=>{e.preventDefault();run(async()=>{const body={query:$('query').value,topic:'demo'};if($('asof').value)body.as_of=$('asof').value;const answer=await call('search',body);$('results').replaceChildren();for(const r of answer.results){const card=document.createElement('article');const h=document.createElement('h2');h.textContent=`${r.type} · ${r.authority}`;const p=document.createElement('p');p.textContent=r.text;card.append(h,p);for(const c of r.citations){const b=document.createElement('button');b.textContent=`Verify ${c.provider} / ${c.event_id}`;b.onclick=()=>run(async()=>{await call('resolve',{citation:c});$('notice').textContent='Exact passage and preserved source bytes verified.';});card.append(b);}const gap=document.createElement('small');gap.textContent=r.gaps.join(' · ');card.append(gap);$('results').append(card);}$('notice').textContent=`${answer.results.length} results. ${answer.withheld_in_topic} withheld in this topic.`;});};
