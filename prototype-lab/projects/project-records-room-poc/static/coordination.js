/* Real receipt-backed coordination; no simulated agent presence or execution. */
(()=>{
  const labels={requested:'Requested · awaiting pickup',picked_up:'Picked up',result_submitted:'Result submitted · awaiting acknowledgment',acknowledged:'Acknowledged',cancelled:'Cancelled'};
  const panel=element('section');panel.id='coordination-panel';
  const heading=element('h4','Requests & handoffs'),add=element('button','＋ Request','text-button');add.id='new-coordination';heading.append(add);
  const list=element('div');list.id='coordination-items';panel.append(heading,element('p','Pickup and results are recorded by the assigned identity. Posting does not launch an agent.','muted'),list);
  document.querySelector('.room-brief').prepend(panel);
  const brief=element('section'),briefhead=element('h4','Executive briefings'),capture=element('button','＋ Snapshot','text-button');capture.id='new-snapshot';briefhead.append(capture);const snapshots=element('div');snapshots.id='executive-snapshots';brief.append(briefhead,element('p','Selected source records, frozen at capture time. This conversation stays separate from the working session.','muted'),snapshots);panel.after(brief);
  const inbox=element('button','Requests for you','secondary');inbox.id='coordination-inbox';inbox.classList.add('hidden');document.querySelector('.environment').after(inbox);
  let running=false,lastRoom=null,lastAuth=null,signature='',inboxItems=[],seen=new Set();
  const button=(label,fn)=>{const b=element('button',label,'text-button');b.type='button';b.onclick=fn;return b;};
  function actions(item){
    const out=[];if(state.room?.state!=='active')return out;
    if(item.assignee===state.me.id&&item.state==='requested')out.push(['pickup','Acknowledge pickup']);
    if(item.assignee===state.me.id&&item.state==='picked_up')out.push(['submit','Submit result']);
    if(item.kind==='owner_input'&&state.me.id==='robert'&&['requested','picked_up'].includes(item.state))out.push(['answer','Answer request']);
    if(item.requester===state.me.id&&item.state==='result_submitted')out.push(['acknowledge','Acknowledge result']);
    if(['requested','picked_up','result_submitted'].includes(item.state)&&[item.requester,'robert'].includes(state.me.id))out.push(['cancel','Cancel request']);
    return out;
  }
  function change(item,action,label){
    const context=roomContext();modal(label,[field('body','Response / evidence','textarea'),element('p','This records your own response. It does not execute the requested work.')],async data=>{context.check();await write(`/api/v1/rooms/${context.id}/coordination/${item.id}`,{action,body:data.body,version:item.version});signature='';await refresh();},label);
  }
  function renderItem(item){
    const node=element('article',undefined,'coordination-card');node.dataset.item=item.id;node.append(element('strong',item.title),element('small',`${item.kind} · ${labels[item.state]}`),element('p',item.body),element('small',`${item.requester} → ${item.assignee}`));
    if(item.source_snapshot)node.append(element('small',`Explicit handoff from snapshot ${item.source_snapshot}`));
    const audit=element('details');audit.append(element('summary','History & evidence'));for(const step of item.steps)audit.append(element('p',`${date(step.created)} · ${step.actor} · ${step.action}\n${step.body}`));node.append(audit);
    for(const [action,label] of actions(item))node.append(button(label,()=>change(item,action,label)));return node;
  }
  add.onclick=()=>{
    const context=roomContext();modal('Request work or input',[field('kind','Request type','select','review',[['review','Review'],['handoff','Handoff'],['owner_input','Robert input needed']]),field('title','Title'),field('body','Question, candidate reference, or handoff','textarea'),field('assignee','Assigned participant','select','robert',state.room.members.filter(m=>m.role==='contributor').map(m=>[m.id,m.label])),element('p','For Robert input, select Robert. A request is not a running agent or an approval.')],async data=>{context.check();await write(`/api/v1/rooms/${context.id}/coordination`,data);signature='';await refresh();},'Record request');
  };
  capture.onclick=async()=>{
    const context=roomContext();await refreshRooms();context.check();const choices=state.rooms.filter(r=>r.id!==context.id).map(r=>[r.id,r.title]);
    if(!choices.length){toast('Create a separate working session first.',true);return;}
    modal('Choose source working session',[field('source','Source session','select',choices[0][0],choices)],async data=>{
      context.check();const source=await api(`/api/v1/rooms/${data.source}`);context.check();
      setTimeout(()=>{try{context.check();}catch{return;}const checks=element('div');checks.className='snapshot-selection';
        for(const event of source.events.slice(-100)){const label=element('label'),check=element('input');check.type='checkbox';check.name='source-record';check.value=event.id;label.append(check,document.createTextNode(`${event.actor_label} · ${date(event.created)} · ${(event.presentation?.text||event.body).slice(0,200)}`));checks.append(label);}
        modal('Select records to disclose',[element('p',`Copy selected records from “${source.title}” into this session for its participants to read. The working session remains unchanged. Only the latest 100 source records are offered here.`),checks,field('disclosure','I authorize sharing these selected records with this session','checkbox',false)],async()=>{context.check();const ids=[...checks.querySelectorAll('input:checked')].map(n=>n.value);if(!$('field-disclosure').checked)throw new Error('Acknowledge the disclosure.');await write(`/api/v1/rooms/${context.id}/executive-snapshots`,{source:source.id,event_ids:ids,disclosure_acknowledged:true});signature='';await refresh();},'Capture snapshot');},0);
    },'Choose records');
  };
  function handoff(snapshot){
    const context=roomContext();api(`/api/v1/rooms/${snapshot.source}`).then(source=>{
      context.check();modal('Send explicit handoff to working session',[field('title','Handoff title'),field('body','Authorized next steps','textarea'),field('assignee','Assigned participant','select','robert',source.members.filter(m=>m.role==='contributor').map(m=>[m.id,m.label])),element('p',`This records a linked handoff in “${source.title}”. The assignee must acknowledge pickup; no runtime is launched.`)],async data=>{context.check();await write(`/api/v1/rooms/${snapshot.source}/coordination`,{...data,kind:'handoff',source_snapshot:snapshot.id});toast('Handoff recorded in working session; awaiting assignee pickup.');},'Send handoff');
    }).catch(error=>toast(error.message,true));
  }
  async function refresh(){
    if(running||!state.me||state.view!=='rooms'||!state.room)return;
    const room=state.room.id,epoch=state.authEpoch,generation=state.navigation;running=true;
    const current=()=>state.me&&state.authEpoch===epoch&&state.room?.id===room&&state.navigation===generation&&state.view==='rooms';
    if(lastRoom!==room||lastAuth!==epoch){clear(list);clear(snapshots);signature='';lastRoom=room;lastAuth=epoch;}
    add.disabled=state.room.state!=='active'||!state.room.members.some(m=>m.id===state.me.id&&m.role==='contributor');capture.classList.toggle('hidden',state.me.id!=='robert');capture.disabled=state.room.state!=='active';
    try{const data=await api(`/api/v1/rooms/${room}/coordination`);if(!current())return;const next=JSON.stringify([data,state.room.state]);if(next===signature)return;signature=next;clear(list);clear(snapshots);
      for(const item of data.items)list.append(renderItem(item));if(!data.items.length)list.append(element('p','No recorded requests.','muted'));
      for(const snap of data.snapshots){const node=element('article',undefined,'coordination-card');node.append(element('strong',`Snapshot · ${date(snap.created)}`),element('small',`${snap.records.length} selected records · through source sequence ${snap.through_seq}`));
        const details=element('details');details.append(element('summary','Read briefing records'));for(const record of snap.records){details.append(element('p',`${record.actor} · ${date(record.created)}\n${record.text}`));if(record.warning)details.append(element('p',record.warning,'agent-warning'));}node.append(details);
        if(state.me.id==='robert'){node.append(button('Open source session',()=>{location.hash=`room/${snap.source}`;}),button('Send handoff back',()=>handoff(snap)));}snapshots.append(node);
      }
    }catch(error){if(current()){clear(list);clear(snapshots);list.append(element('p','Coordination unavailable. Retrying…','muted'));signature='';}}finally{running=false;}
  }
  inbox.onclick=()=>{modal('Requests for your attention',inboxItems.map(item=>{const card=element('div',undefined,'coordination-card');card.append(element('strong',item.title),element('p',`${labels[item.state]} · ${item.body}`),button('Join session',()=>{$('modal').close();location.hash=`room/${item.room}`;}));return card;}),async()=>{},'Close');};
  async function refreshInbox(){
    if(!state.me){inbox.classList.add('hidden');inboxItems=[];seen.clear();clear(list);clear(snapshots);signature='';lastAuth=null;return;}
    const epoch=state.authEpoch;
    try{const data=await api('/api/v1/coordination/inbox');if(epoch!==state.authEpoch||!state.me)return;inboxItems=data.items;inbox.classList.toggle('hidden',!data.items.length);inbox.textContent=`Requests for you (${data.items.length})`;
      const fresh=data.items.filter(i=>!seen.has(`${i.id}:${i.version}`));for(const item of data.items)seen.add(`${item.id}:${item.version}`);if(fresh.length)toast(`${fresh.length} request(s) need your attention. Open “Requests for you”.`);
    }catch{if(epoch===state.authEpoch){inboxItems=[];inbox.classList.add('hidden');}}
  }
  window.addEventListener('records-signout',()=>{clear(list);clear(snapshots);inbox.classList.add('hidden');inboxItems=[];seen.clear();signature='';lastAuth=null;});
  setInterval(()=>{if(!state.me){clear(list);clear(snapshots);inbox.classList.add('hidden');inboxItems=[];seen.clear();}else refresh();},2500);
  setInterval(refreshInbox,10000);window.addEventListener('hashchange',()=>{refresh();refreshInbox();});refresh();refreshInbox();
})();
