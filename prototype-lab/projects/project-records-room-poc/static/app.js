"use strict";
const $=id=>document.getElementById(id);
const state={me:null,rooms:[],parents:[],parent:null,room:null,view:"rooms",pending:null,authEpoch:0,navigation:0,drafts:{}};
const modeLabels={conversation:"One-to-one thinking",meeting:"Team meeting",bridge:"Operational bridge"};
const kindLabels={message:"Message",checkpoint:"Checkpoint",proposal:"Proposal",decision:"Owner decision",task:"Assignment",task_update:"Task update"};
const element=(tag,content,cls)=>{const node=document.createElement(tag);if(content!==undefined)node.textContent=content;if(cls)node.className=cls;return node;};
const clear=node=>node.replaceChildren();
function toast(text,error=false){$("toast").textContent=text;$("toast").className="toast"+(error?" error":"");clearTimeout(toast.timer);toast.timer=setTimeout(()=>$("toast").classList.add("hidden"),6500);}
async function api(path,method="GET",payload,operation){
  const epoch=state.authEpoch;
  let response;
  try{response=await fetch(path,{method,headers:{...(payload!==undefined?{"Content-Type":"application/json"}:{}),...(operation?{"Idempotency-Key":operation}:{})},...(payload!==undefined?{body:JSON.stringify(payload)}:{})});}
  catch(cause){const error=new Error("Connection interrupted; the write may or may not have committed.");error.uncertain=true;throw error;}
  if(epoch!==state.authEpoch)throw new Error("Discarded a response from a previous sign-in.");
  let data;try{data=await response.json();}catch(cause){const error=new Error("No readable response; refresh or retry the same operation.");error.uncertain=method!=="GET";throw error;}
  if(epoch!==state.authEpoch)throw new Error("Discarded a response from a previous sign-in.");
  if(!response.ok){const error=new Error(data.error||"Request failed");error.status=response.status;error.uncertain=method!=="GET"&&response.status>=500;if(response.status===401&&path!=="/api/login")showLogin();throw error;}
  return data;
}
async function write(path,payload){
  if(path.endsWith("/events")&&["decision","task","task_update"].includes(payload.kind)&&payload.expected_context===undefined){
    if(path!==`/api/v1/rooms/${state.room?.id}/events`)throw new Error("Refresh the intended session before submitting.");
    payload={...payload,expected_context:{...state.room.contribution_guard}};
  }
  if(path.endsWith("/events")&&payload.context_class===undefined)payload={...payload,context_class:payload.kind==="decision"?"robert_source":$("context-class").value||null};
  if(path.endsWith("/documents")&&payload.context_class===undefined)payload={...payload,context_class:$("field-context_class")?.value||null};
  const actor=state.me.id;
  if(state.pending&&(state.pending.actor!==actor||state.pending.path!==path||state.pending.payload))throw new Error("Resolve the unconfirmed write before submitting another.");
  const request={path,payload,key:state.pending?.key||crypto.randomUUID(),actor};
  savePending(request);
  try{const response=await api(path,"POST",payload,request.key);if(state.me?.id===actor)clearPending();return response;}
  catch(error){if(state.me?.id===actor){if(error.uncertain||(error.status===409&&error.message.includes("Idempotency key")))$("pending-operation").classList.remove("hidden");else clearPending();}throw error;}
}
function savePending(request){state.pending=request;sessionStorage.setItem(`minimoi.pending.${request.actor}`,JSON.stringify({path:request.path,key:request.key,actor:request.actor}));}
function clearPending(){if(state.me)sessionStorage.removeItem(`minimoi.pending.${state.me.id}`);state.pending=null;$("pending-operation").classList.add("hidden");}
async function checkReceipt(){const pending=state.pending;if(!pending||pending.actor!==state.me?.id)return;try{const response=await api(`/api/v1/operations/${encodeURIComponent(pending.key)}`);clearPending();if(pending.payload?.body===$("message-body").value)$("message-body").value="";if($("modal").open)$("modal").close();await refreshRooms();await navigate();toast("Recovered the original committed receipt; do not resubmit.");return response;}catch(error){if(error.status===404)toast("No receipt found yet. Retry the same operation; after reload, re-enter its original content.",true);else toast(error.message,true);}}
function showLogin(){state.authEpoch++;state.navigation++;state.me=null;state.room=null;state.rooms=[];state.parents=[];state.parent=null;$("parent-title").textContent="";$("parent-purpose").textContent="";clear($("parent-sessions"));$("parent-workspace").classList.add("hidden");state.pending=null;state.drafts={};for(const id of ["search-results","principal-list","messages","room-list","modal-fields","checkpoint","decisions","tasks","participants","documents","store-status","artifact-links","meeting-notes"])clear($(id));$("composer").reset();$("search-query").value="";$("backup-result").textContent="";$("room-title").textContent="";$("room-purpose").textContent="";$("messages").dataset.last="";$("pending-operation").classList.add("hidden");$("toast").classList.add("hidden");if($("modal").open)$("modal").close();$("workspace").classList.add("hidden");$("login").classList.remove("hidden");}
async function enter(){state.me=await api("/api/v1/me");$("login").classList.add("hidden");$("workspace").classList.remove("hidden");$("profile-name").textContent=state.me.label;$("profile-initial").textContent=state.me.label[0];const owner=state.me.id==="robert";["new-room","new-room-small","new-room-empty","new-project","new-child-session"].forEach(id=>$(id).classList.toggle("hidden",!owner));$("backup").disabled=!owner;$("new-principal").disabled=!owner;const saved=sessionStorage.getItem(`minimoi.pending.${state.me.id}`);if(saved){try{const pending=JSON.parse(saved);if(pending.actor===state.me.id){state.pending=pending;$("pending-operation").classList.remove("hidden");}}catch(error){toast("Unrecognized recovery metadata; inspect prior records before resubmitting.",true);}}await refreshRooms();await navigate();if(state.pending)await checkReceipt();}
async function refreshRooms(){
  const epoch=state.authEpoch;
  const [sessions,parents]=await Promise.all([api("/api/v1/rooms"),api("/api/v2/rooms")]);
  if(epoch!==state.authEpoch)return;
  state.rooms=sessions.rooms;state.parents=parents.rooms;renderRoomList();
}
function renderRoomList(){
  clear($("room-list"));
  for(const parent of state.parents){
    const group=element("div",undefined,"room-group");
    const heading=element("button",parent.title,"room-link parent-link");heading.dataset.parent=parent.id;
    heading.onclick=()=>{location.hash=`project/${parent.id}`;};group.append(heading);
    for(const room of state.rooms.filter(s=>s.parent_room_id===parent.id)){
      const button=element("button",undefined,"room-link session-link"+(state.room?.id===room.id?" active":""));
      button.append(element("strong",room.title),element("small",`${modeLabels[room.mode]} · ${room.state}`));
      button.onclick=()=>{location.hash=`room/${room.id}`;};group.append(button);
    }
    $("room-list").append(group);
  }
}
async function navigate(){
  if(!state.me)return;const generation=++state.navigation;
  if(state.room)state.drafts[state.room.id]=$("message-body").value;
  const [name,id]=location.hash.slice(1).split("/");
  state.view=name==="vault"?"vault":name==="connections"?"connections":"rooms";
  document.querySelectorAll(".view").forEach(node=>node.classList.toggle("hidden",node.id!==`${state.view}-view`));
  document.querySelectorAll("[data-view]").forEach(node=>node.classList.toggle("selected",node.dataset.view===state.view));
  $("parent-workspace").classList.add("hidden");
  if(state.view==="rooms"){
    if(name==="project"&&id){await loadParent(id,generation);return;}
    const roomId=name==="room"?id:state.room?.id||state.rooms[0]?.id;
    if(roomId){$("message-body").disabled=true;$("send-message").disabled=true;await loadRoom(roomId,generation);}
    else if(state.parents.length){await loadParent(state.parents[0].id,generation);}
    else{state.room=null;state.parent=null;$("empty-room").classList.remove("hidden");$("room-workspace").classList.add("hidden");}
  }
  if(state.view==="connections")await connections();
}
async function loadParent(id,generation=state.navigation){
  const epoch=state.authEpoch;state.room=null;state.parent=null;
  $("message-body").value="";$("message-body").disabled=true;$("send-message").disabled=true;
  $("room-workspace").classList.add("hidden");$("empty-room").classList.add("hidden");
  clear($("parent-sessions"));$("parent-title").textContent="";$("parent-purpose").textContent="";
  const parent=await api(`/api/v2/rooms/${id}`);
  if(epoch!==state.authEpoch||generation!==state.navigation||state.view!=="rooms")return;
  state.parent=parent;$("parent-title").textContent=parent.title;$("parent-purpose").textContent=parent.purpose;
  $("parent-workspace").classList.remove("hidden");$("new-child-session").classList.toggle("hidden",state.me.id!=="robert");
  if(!parent.sessions.length)$("parent-sessions").append(element("p","No sessions yet. This room is quiet; no recording or agent has started.","muted"));
  for(const session of parent.sessions){
    const button=element("button",`${session.title} · ${session.state}`,"room-link");
    button.onclick=()=>{location.hash=`room/${session.id}`;};$("parent-sessions").append(button);
  }
  renderRoomList();
}
async function loadRoom(id,generation=state.navigation){const epoch=state.authEpoch;const room=await api(`/api/v1/rooms/${id}`);if(epoch!==state.authEpoch||generation!==state.navigation||state.view!=="rooms"||(location.hash.startsWith("#room/")&&location.hash!==`#room/${id}`))return;if(state.room?.id!==id){$("message-body").value=state.drafts[id]||"";$("messages").dataset.last="";}state.parent=null;$("parent-workspace").classList.add("hidden");state.room=room;renderRoom();renderRoomList();}
const date=value=>new Date(value).toLocaleString(undefined,{month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"});
function options(select,entries,empty){const previous=select.value;clear(select);if(empty!==undefined)select.append(new Option(empty,""));for(const [value,label]of entries)select.append(new Option(label,value));if([...select.options].some(x=>x.value===previous))select.value=previous;}
function renderRoom(){
  refreshCoS();const room=state.room;if(!room)return;$("empty-room").classList.add("hidden");$("room-workspace").classList.remove("hidden");$("room-title").textContent=room.title;$("room-purpose").textContent=room.purpose;$("room-mode").textContent=modeLabels[room.mode];$("room-state").textContent=room.state==="active"?"● Recording this session":`○ ${room.state[0].toUpperCase()+room.state.slice(1)}`;$("room-state").className="capture-badge"+(room.state==="active"?" recording":"");$("event-count").textContent=`${room.events.length} records`;
  const canModerate=state.me.id==="robert"||room.moderator===state.me.id;const member=room.members.find(m=>m.id===state.me.id);const writable=room.state==="active"&&(state.me.id==="robert"||member?.role==="contributor");$("pause-room").classList.toggle("hidden",!canModerate||room.state==="closed");$("close-room").classList.toggle("hidden",!canModerate||room.state==="closed");$("pause-room").textContent=room.state==="active"?"Pause recording":"Resume session";$("export-md").href=`/api/v1/rooms/${room.id}/export?format=markdown`;$("paused-notice").classList.toggle("hidden",room.state==="active");$("paused-notice").textContent=room.state==="closed"?"Session closed permanently. Start a new session for further discussion.":"Recording is paused. Resume the session before adding a contribution.";$("message-body").disabled=!writable;$("send-message").disabled=!writable;$("invite").classList.toggle("hidden",state.me.id!=="robert");$("upload").classList.toggle("hidden",!(state.me.id==="robert"||member?.role==="contributor"));
  $("upload").classList.toggle("hidden",!writable);options($("target"),room.members.map(m=>[m.id,m.label]),"Everyone in this room");configureComposer();
  const messages=$("messages");const nearBottom=messages.scrollHeight-messages.scrollTop-messages.clientHeight<90;const oldLast=messages.dataset.last;const last=room.events.at(-1)?.id;
  if(oldLast!==last){clear(messages);for(const event of room.events)messages.append(renderEvent(event));messages.dataset.last=last||"";if(nearBottom||!oldLast)messages.scrollTop=messages.scrollHeight;}
  renderBrief();renderEvidence();renderNotes();
}
function renderEvent(event){const system=["session_opened","membership","moderator","state_change","document"].includes(event.kind);const node=element("article",undefined,`event event-${event.kind}${system?" event-system":""}`);node.id=`event-${event.id}`;const top=element("div",undefined,"event-top");top.append(element("span",event.actor_label[0],"avatar"),element("span",event.actor_label,"event-author"));if(!system)top.append(element("span",kindLabels[event.kind]||event.kind,"pill"));top.append(element("time",date(event.created),"event-time"));const contribution=!system&&window.RecordsContribution.render(event.presentation,new Set(state.room.events.map(record=>record.id)));node.append(top,contribution||element("div",system?`${event.body} · ${event.actor_label}`:event.body,"event-body"));if(!system){let note=event.actor_kind==="agent"?"Authenticated client contribution · ":"";note+=`Record ${event.id.slice(0,8)}`;if(event.target)note+=` · to ${event.target}`;if(event.reference)note+=` · references ${event.reference.slice(0,8)}`;node.append(element("div",note,"event-note"));}
  if(!system)node.append(element("div",event.context_class||"Provenance not classified","event-note"));
  if(event.origin?.declared_speaker)node.append(element("div",`Declared speaker: ${event.origin.declared_speaker} · submitted transcript label, not verified identity`,"event-note"));
  if(event.origin?.coverage)node.append(element("div",`Source coverage: ${event.origin.coverage}`,"event-note"));
  if(event.origin)node.append(element("div",`Declared origin: ${event.origin.source_application} · ${event.origin.mode}${event.origin.agent_id?` · ${event.origin.agent_id}`:""} · execution not attested`,"event-note"));
  if(event.kind==="proposal"&&state.me.id==="robert"&&state.room.state==="active"){const button=element("button","Record my decision","text-button event-action");button.onclick=()=>decision(event);node.append(button);}return node;}
function briefItem(text,meta){const node=element("div",text,"brief-item");if(meta)node.append(element("small",meta));return node;}
function renderBrief(){const room=state.room;const events=room.events;for(const id of ["checkpoint","decisions","tasks","participants","documents"])clear($(id));const checkpoints=events.filter(e=>["checkpoint","state_change"].includes(e.kind));const checkpoint=checkpoints.at(-1);$("checkpoint").append(checkpoint?briefItem(checkpoint.body,`${checkpoint.actor_label} · ${date(checkpoint.created)}`):element("p","No checkpoint yet. The moderator or a participant can add one.","muted"));const decisions=events.filter(e=>e.kind==="decision");if(!decisions.length)$("decisions").append(element("p","No owner-confirmed decisions yet.","muted"));for(const event of decisions.slice(-4))$("decisions").append(briefItem(event.body,`${event.actor_label} · proposal ${event.reference.slice(0,8)}`));const tasks=events.filter(e=>e.kind==="task");if(!tasks.length)$("tasks").append(element("p","No assignments. This room does not launch workers.","muted"));for(const task of tasks.slice(-5)){const update=events.filter(e=>e.kind==="task_update"&&e.reference===task.id).at(-1);$("tasks").append(briefItem(task.body,`${task.target} · ${update?update.body:"Awaiting an update"}`));}$("moderator").textContent=`Moderator: ${room.members.find(m=>m.id===room.moderator)?.label||room.moderator}`;for(const member of room.members){const node=element("div",undefined,"participant");const info=element("div");info.append(element("strong",member.label+(member.id===state.me.id?" · you":"")),element("small",member.kind==="agent"?(member.last_contribution?`Last contribution ${date(member.last_contribution)}`:"Identity only · runtime not connected"):"Human participant"),element("small",member.role));node.append(element("span",member.label[0],"avatar"),info);$("participants").append(node);}if(!room.documents.length)$("documents").append(element("p","File source material with a provenance note.","muted"));for(const doc of room.documents){const link=element("a",`↗ ${doc.name}`,"doc-link");link.href=`/api/v1/documents/${doc.id}`;link.title=`${doc.source_note}\nSHA-256: ${doc.sha256}`;$("documents").append(link);}}
function configureComposer(){if(!state.room)return;const kind=$("event-kind").value;$("reference-label").classList.toggle("hidden",kind!=="task_update");$("target-label").classList.toggle("hidden",kind==="task_update");if(kind==="task_update")options($("reference"),state.room.events.filter(e=>e.kind==="task").map(e=>[e.id,`${e.target}: ${e.body.slice(0,55)}`]));}
function field(name,label,type="text",value="",choices){const wrapper=element("label",label);let input;if(type==="select"){input=element("select");for(const [key,text] of choices)input.append(new Option(text,key));}else input=element(type==="textarea"?"textarea":"input");if(type!=="textarea"&&type!=="select")input.type=type;input.name=name;input.id=`field-${name}`;if(type==="checkbox")input.checked=Boolean(value);else input.value=value;input.required=name!=="context_class";if(type==="text")input.maxLength=2400;wrapper.append(input);return wrapper;}
function modal(title,fields,submit,label="Save"){clear($("modal-fields"));$("modal-title").textContent=title;for(const item of fields)$("modal-fields").append(item);$("modal-submit").textContent=label;$("modal-submit").disabled=false;$("modal-form").onsubmit=async event=>{event.preventDefault();$("modal-submit").disabled=true;try{const data=Object.fromEntries(new FormData($("modal-form")));await submit(data);$("modal").close();}catch(error){toast(error.message,true);}finally{$("modal-submit").disabled=false;}};$("modal").showModal();}
function newRoom(){
  const parentId=state.parent?.id||state.room?.parent_room_id||"__new__";
  const choices=[["__new__","Create a new room with this first session"],...state.parents.map(p=>[p.id,p.title])];
  modal("Open a working session",[field("parent","Persistent room","select",parentId,choices),field("title","Session title"),field("purpose","What are we working through?","textarea"),field("mode","Session mode","select","conversation",Object.entries(modeLabels)),field("capture","Record the complete discussion in this session","checkbox",false),element("p","Every submitted message is retained. Participants are not inherited from other sessions. A new room uses this title and purpose as shared metadata.")],async data=>{
    const path=data.parent==="__new__"?"/api/v1/rooms":`/api/v2/rooms/${data.parent}/sessions`;
    const response=await write(path,{title:data.title,purpose:data.purpose,mode:data.mode,recording_acknowledged:data.capture==="on"});
    await refreshRooms();location.hash=`room/${response.result.id}`;toast("Session opened · committed locally");
  },"Open session");
}
function newPersistentRoom(){
  modal("Create a quiet room",[field("title","Room title"),field("purpose","Shared room purpose","textarea"),element("p","Visible to participants of any session in this room. Do not include sibling-private information. Creating a room does not start recording or agents.")],async data=>{
    const response=await write("/api/v2/rooms",{title:data.title,purpose:data.purpose});
    await refreshRooms();location.hash=`project/${response.result.id}`;toast("Quiet room created · no session opened");
  },"Create room");
}
function changeState(next){const room=state.room;modal(next==="active"?"Resume the thread":"Leave a useful stopping point",[field("checkpoint",next==="active"?"Where are we picking up?":"What is settled, open, or next?","textarea"),element("p",next==="active"?"New contributions will be recorded again.":"This closes or pauses recording. It does not approve proposals or deployments.")],async data=>{await write(`/api/v1/rooms/${room.id}/state`,{state:next,version:room.version,checkpoint:data.checkpoint});await loadRoom(room.id);await refreshRooms();toast(`Session ${next} · checkpoint preserved`);},next==="active"?"Resume recording":"Save checkpoint");}
function roomContext(){const id=state.room.id,epoch=state.authEpoch;return {id,check(){if(epoch!==state.authEpoch||state.room?.id!==id||state.view!=="rooms"||(location.hash.startsWith("#room/")&&location.hash!==`#room/${id}`))throw new Error("The target session changed. Reopen this action in the intended room.");}};}
function decision(event){const context=roomContext(),guard={...state.room.contribution_guard};modal("Record your decision",[element("p",`Proposal: ${event.body}`),field("body","Your decision and its scope","textarea"),element("p","This records your position. It does not run a task, approve a code diff, or deploy anything.")],async data=>{context.check();await write(`/api/v1/rooms/${context.id}/events`,{kind:"decision",body:data.body,reference:event.id,expected_context:guard});await loadRoom(context.id);toast("Your decision was recorded with its source proposal");},"Record my decision");}
async function members(){const context=roomContext();const principals=(await api("/api/v1/principals")).principals.filter(p=>p.id!=="robert");context.check();if(!principals.length){toast("Provision a client in Connections first.");location.hash="connections";return;}modal("Room membership",[field("actor","Client identity","select",principals[0].id,principals.map(p=>[p.id,p.label])),field("role","Room access","select","contributor",[["contributor","Read and contribute"],["observer","Read only"],["remove","Remove room access"]]),element("p","Membership does not start an agent or give it command-execution permission.")],async data=>{context.check();await write(`/api/v1/rooms/${context.id}/members`,data);await loadRoom(context.id);toast("Room membership updated");});}
function upload(){const context=roomContext();modal("File a source",[field("file","Document (up to 2 MB)","file"),field("source_note","Where did it come from?","textarea"),field("context_class","Material provenance","select","",provenanceChoices),element("p","Original bytes are preserved. Text files are searchable; other formats can be downloaded. Imports are sources, not live conversation turns.")],async data=>{context.check();const file=$("field-file").files[0];if(!file||file.size>2000000)throw new Error("Choose a file up to 2 MB.");const bytes=new Uint8Array(await file.arrayBuffer());let binary="";for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));context.check();await write(`/api/v1/rooms/${context.id}/documents`,{name:file.name,source_note:data.source_note,base64:btoa(binary)});await loadRoom(context.id);toast("Source filed · original bytes preserved");},"File source");}
async function connections(){if(state.me.id!=="robert"){$("store-status").textContent="Owner-only system controls.";$("backup").disabled=true;$("new-principal").disabled=true;return;}const [status,clients]=await Promise.all([api("/api/v1/status"),api("/api/v1/principals")]);clear($("store-status"));for(const [name,count]of Object.entries(status.counts)){const row=element("div",undefined,"store-metric");row.append(element("span",name),element("strong",String(count)));$("store-status").append(row);}clear($("principal-list"));for(const principal of clients.principals){const row=element("div",undefined,"principal-row");row.append(element("span",principal.label[0],"avatar"),element("strong",principal.label),element("span",principal.id,"muted"),element("small",principal.kind==="human"?"Owner":"Provisioned identity · no runtime bridge"));$("principal-list").append(row);}}
function provision(){modal("Provision a client identity",[field("id","Stable ID (e.g. cos-dev)"),field("label","Display name"),element("p","This creates a scoped API identity, not a live agent connection. Invite it to specific rooms separately.")],async data=>{const response=await write("/api/v1/principals",data);await connections();setTimeout(()=>{modal("Client access key",[element("p","Store this local-test credential securely. It is shown once and must not be pasted into a room."),element("div",response.access_token||"Token was not returned on this retry. Provision a different identity before use.","token-box")],async()=>{},"I have saved it");},10);},"Create identity");}
$("login-form").onsubmit=async event=>{event.preventDefault();try{await api("/api/login","POST",{token:$("access-key").value});$("access-key").value="";await enter();}catch(error){toast(error.message,true);}};
$("logout").onclick=async()=>{try{await api("/api/logout","POST",{});showLogin();}catch(error){toast(error.message,true);}};
document.querySelectorAll("[data-view]").forEach(button=>button.onclick=()=>{location.hash=button.dataset.view;});
for(const id of ["new-room","new-room-small","new-room-empty"])$(id).onclick=newRoom;
$("new-project").onclick=newPersistentRoom;
$("new-child-session").onclick=newRoom;
$("pause-room").onclick=()=>changeState(state.room.state==="active"?"paused":"active");$("close-room").onclick=()=>changeState("closed");$("invite").onclick=()=>members().catch(error=>toast(error.message,true));$("upload").onclick=upload;$("event-kind").onchange=configureComposer;
$("composer").onsubmit=async event=>{event.preventDefault();const button=$("send-message"),context=roomContext(),roomId=context.id,body=$("message-body").value,actor=state.me.id;button.disabled=true;try{context.check();const kind=$("event-kind").value;await write(`/api/v1/rooms/${roomId}/events`,{kind,body,target:kind==="task_update"?null:$("target").value||null,reference:kind==="task_update"?$("reference").value:null});if(state.me?.id!==actor)return;if(state.drafts[roomId]===body)state.drafts[roomId]="";if(state.room?.id===roomId){if($("message-body").value===body)$("message-body").value="";await loadRoom(roomId);$("messages").scrollTop=$("messages").scrollHeight;}await refreshRooms();toast("Contribution committed locally");}catch(error){toast(error.message,true);}finally{button.disabled=state.room?.state!=="active"||(location.hash.startsWith("#room/")&&location.hash!==`#room/${state.room?.id}`);}};
$("search-form").onsubmit=async event=>{event.preventDefault();try{const data=await api(`/api/v1/search?q=${encodeURIComponent($("search-query").value)}`);clear($("search-results"));if(!data.results.length)$("search-results").append(element("div","No matching records in your authorized sessions.","vault-empty"));for(const result of data.results){const item=element("button",undefined,"search-result");item.append(element("span",result.kind,"pill"),element("h3",result.title),element("p",result.body),element("small",`${result.supporting_actor_label?`Linked by ${result.author} · supporting ${result.supporting_kind} by ${result.supporting_actor_label}`:`Submitted by ${result.author}`} · ${result.context_class||"Not classified"} · ${date(result.created)} · record ${result.id.slice(0,8)}`));item.onclick=()=>{location.hash=`room/${result.room}`;};$("search-results").append(item);}}catch(error){toast(error.message,true);}};
$("new-principal").onclick=provision;$("backup").onclick=async()=>{try{const backup=await api("/api/v1/backup","POST",{});$("backup-result").textContent=`Verified ${backup.file}. Same-disk test backup only; no independent backup or production copy.`;toast("Backup created and integrity checked");}catch(error){toast(error.message,true);}};
$("modal-close").onclick=$("modal-cancel").onclick=()=>$("modal").close();
$("retry-operation").onclick=async()=>{const pending=state.pending;if(!pending||pending.actor!==state.me?.id)return;if(!pending.payload){await checkReceipt();return;}try{await api(pending.path,"POST",pending.payload,pending.key);if(state.me?.id!==pending.actor)return;clearPending();if(pending.payload.body===$("message-body").value)$("message-body").value="";if($("modal").open)$("modal").close();await refreshRooms();await navigate();toast("Original operation confirmed; no duplicate created");}catch(error){toast(error.message,true);}};
$("dismiss-operation").onclick=checkReceipt;
window.addEventListener("hashchange",()=>{if($("modal").open)$("modal").close();navigate().catch(error=>toast(error.message,true));});
setInterval(async()=>{if(!state.me||state.view!=="rooms"||!state.room||document.hidden)return;const id=state.room.id,generation=state.navigation;if(location.hash.startsWith("#room/")&&location.hash!==`#room/${id}`)return;try{const room=await api(`/api/v1/rooms/${id}`);if(state.room?.id===id&&generation===state.navigation&&state.view==="rooms"&&(!location.hash.startsWith("#room/")||location.hash===`#room/${id}`)){state.room=room;renderRoom();}}catch(error){if(error.status!==401&&state.me)$("room-state").textContent="Connection unavailable · showing last received record";}},2500);
const provenanceChoices=[["","Not classified"],["robert_source","Robert's own source"],["external_source","External source / quotation"],["agent_draft","Agent draft"],["coauthored_output","Coauthored output"]];
const provenanceLabel=element("label","Material provenance");
const provenanceSelect=element("select");provenanceSelect.id="context-class";
for(const [value,label]of provenanceChoices)provenanceSelect.append(new Option(label,value));
provenanceLabel.append(provenanceSelect);document.querySelector(".composer-controls").append(provenanceLabel);
const evidenceSection=element("section");const evidenceHeading=element("h4","Artifact versions");
const linkButton=element("button","＋ Link","text-button");linkButton.id="link-artifact";linkButton.onclick=linkArtifact;
evidenceHeading.append(linkButton);const evidenceList=element("div");evidenceList.id="artifact-links";
evidenceSection.append(evidenceHeading,evidenceList);document.querySelector(".room-brief").append(evidenceSection);

function renderEvidence(){
  const room=state.room;clear(evidenceList);
  const member=room.members.find(m=>m.id===state.me.id);
  linkButton.hidden=room.state!=="active"||!(state.me.id==="robert"||member?.role==="contributor");
  for(const ref of room.artifact_refs||[]){
    const card=element("div",undefined,"brief-item");
    card.append(element("strong",ref.label),element("small",`${ref.kind} · ${ref.value}`),element("small",`Revision ${ref.revision}`));
    const button=element("button","Read supporting statement","text-button");
    button.onclick=()=>{const event=room.events.find(e=>e.id===ref.event_id);if(event)modal("Supporting statement",[element("p",`${event.actor_label} · ${event.context_class||"Not classified"}`),element("p",event.body),element("p",`Record ${event.id}`)],async()=>{},"Close");};
    card.append(button);evidenceList.append(card);
  }
  if(!room.artifact_refs?.length)evidenceList.append(element("p","Link an exact artifact version to the statement explaining it.","muted"));
  const decisions=room.events.filter(e=>e.kind==="decision");
  // Render actual authenticated labels, not a hard-coded owner name.
  clear($("decisions"));
  if(!decisions.length)$("decisions").append(element("p","No owner-confirmed decisions yet.","muted"));
  for(const e of decisions.slice(-4))$("decisions").append(briefItem(e.body,`${e.actor_label} · proposal ${e.reference.slice(0,8)}`));
  for(const [id,kind,limit,label] of [["decisions","decision",4,"decisions"],["tasks","task",5,"assignments"]]){
    const records=room.events.filter(e=>e.kind===kind);
    if(records.length>limit){const button=element("button",`${records.length-limit} earlier ${label} — view all ${records.length}`,"text-button");button.onclick=()=>modal(`All ${label}`,records.map(e=>briefItem(e.body,`${e.actor_label} · ${date(e.created)} · ${e.id}`)),async()=>{},"Close");$(id).append(button);}
  }
  for(const [index,doc] of room.documents.entries())$("documents").children[index]?.append(element("small",` · ${doc.context_class||"Not classified"}`));
}

function linkArtifact(){
  const context=roomContext();
  const choices=state.room.events.filter(e=>["message","proposal","decision","checkpoint"].includes(e.kind)).map(e=>[e.id,`${e.actor_label}: ${e.body.slice(0,65)}`]);
  if(!choices.length){toast("Add the supporting statement first.");return;}
  modal("Link an exact artifact version",[
    field("label","Artifact label"),field("kind","Identifier type","select","sha256",[["sha256","File SHA-256"],["git_commit","Git repository + full commit"],["spec_path","Specification path + revision"],["work_artifact","Work artifact reference + revision"]]),
    field("value","Identifier (hash, repository, path, or Work reference)"),field("revision","Exact revision (version label or full git commit)"),
    field("event_id","Supporting statement","select",choices[0][0],choices),
    element("p","This records a relationship, not approval or independent verification. No path or URL is opened; no artifact is fetched.")
  ],async data=>{context.check();await write(`/api/v1/rooms/${context.id}/artifact-refs`,data);await loadRoom(context.id);toast("Artifact version linked to its supporting record");},"Link version");
}

// Source filing gets an explicit provenance choice. Missing/legacy classification stays unknown.
const noteLabels={meeting_notes:"Meeting notes",decision_summary:"Decision summary",next_steps:"Next steps",executive_brief:"Executive brief"};
function renderNotes(){
  const room=state.room,list=$("meeting-notes");clear(list);
  const member=room.members.find(m=>m.id===state.me.id);
  $("new-note").hidden=!Array.isArray(room.notes)||!(state.me.id==="robert"||member?.role==="contributor");
  for(const note of room.notes||[]){
    const card=element("div",undefined,"brief-item");
    card.append(element("strong",note.title),element("small",`${noteLabels[note.kind]} · submitted by ${note.actor_label} · ${note.context_class||"Not classified"} · through record ${note.source_through_seq}`));
    const read=element("button","Read / download","text-button");
    read.onclick=()=>{
      const download=element("button","Download note","text-button");download.type="button";
      download.onclick=()=>{const blob=new Blob([`# ${note.title}\n\nSubmitted by: ${note.actor_label}\nMaterial provenance: ${note.context_class||"Not classified"}\nDeclared origin (not runtime attestation): ${note.origin?JSON.stringify(note.origin):"Not supplied"}\nSession: ${note.room}\nSource through sequence: ${note.source_through_seq}\nRevision: ${note.id}\nSupersedes: ${note.supersedes||"none"}\nStatus: derived document; no artifact approval\n\n${note.body}\n`],{type:"text/markdown"});const url=URL.createObjectURL(blob);const anchor=element("a");anchor.href=url;anchor.download=`meeting-note-${note.id}.md`;anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
      modal(note.title,[element("p",`Submitted by ${note.actor_label}. Material provenance: ${note.context_class||"Not classified"}. Source through record ${note.source_through_seq}.`),element("p",`Declared origin (not runtime attestation): ${note.origin?JSON.stringify(note.origin):"Not supplied"}`),element("p",note.body,"event-body"),download],async()=>{},"Close");
    };
    card.append(read);list.append(card);
  }
  if(!room.notes?.length)list.append(element("p","No separate notes yet. The transcript is retained in full.","muted"));
}
$("new-note").onclick=()=>{
  const context=roomContext(),through=state.room.contribution_guard.last_seq;
  modal("Write meeting notes",[field("title","Document title"),field("kind","Document type","select","meeting_notes",Object.entries(noteLabels)),field("body","Notes / decisions / next steps","textarea"),field("context_class","Material provenance","select","",provenanceChoices),element("p",`Based on the transcript through record ${through}. The original discussion stays intact. You may file notes after the meeting closes.`)],async data=>{
    context.check();await write(`/api/v1/rooms/${context.id}/notes`,{...data,source_through_seq:through,context_class:data.context_class||null});await loadRoom(context.id);toast("Separate note saved; transcript retained");
  },"Save note");
};
enter().catch(error=>{if(error.status!==401)toast(error.message,true);showLogin();});

// Explicit owner request; ordinary transcript posts never dispatch an agent.
let cosRefreshRunning=false;
async function refreshCoS(){
  const room=state.room, actor=state.me?.id, generation=state.navigation;
  if(!room||actor!=="robert"){ $("cos-controls").classList.add("hidden"); return; }
  if(cosRefreshRunning)return;
  cosRefreshRunning=true;
  try{
    const data=await api(`/api/v1/rooms/${room.id}/cos-requests`);
    if(state.room?.id!==room.id||state.me?.id!==actor||state.navigation!==generation)return;
    $("cos-controls").classList.remove("hidden");
    const latest=data.requests[0], pending=data.requests.some(r=>["queued","queued_reconcile","running","uncertain"].includes(r.state));
    $("ask-cos").disabled=!data.enabled||room.state!=="active"||pending;
    $("reconcile-cos").classList.toggle("hidden",latest?.state!=="uncertain");
    $("reconcile-cos").dataset.requestId=latest?.id||"";
    const labels={queued_reconcile:"Checking the saved outcome — no new model call",queued:"Request saved — awaiting CoS",running:"CoS is responding",committed:"CoS reply saved in this session",cancelled:"Request cancelled before inference: session context changed or request expired",uncertain:"Outcome uncertain — no automatic retry. Reconciliation required."};
    $("cos-request-status").textContent=!data.enabled?"CoS has not been enabled for this session.":latest?`${labels[latest.state]||latest.state} · ${latest.id}`:"Save your message, then ask CoS to respond.";
  }catch(error){
    if(state.room?.id===room.id){$("ask-cos").disabled=true;$("cos-request-status").textContent="CoS request status unavailable";}
  }finally{cosRefreshRunning=false;}
}
$("ask-cos").onclick=async()=>{
  const context=roomContext(), actor=state.me.id;
  if($("message-body").value.trim()){toast("Save your message before asking CoS to respond.",true);return;}
  $("ask-cos").disabled=true;
  const storageKey=`minimoi.cos-request.${actor}.${context.id}`;
  let requestId=sessionStorage.getItem(storageKey)||crypto.randomUUID();
  sessionStorage.setItem(storageKey,requestId);
  try{
    context.check();
    const result=await api(`/api/v1/rooms/${context.id}/cos-requests`,"POST",{action:"contribute"},requestId);
    if(result.id===requestId)sessionStorage.removeItem(storageKey);
    if(state.me?.id===actor&&state.room?.id===context.id)await refreshCoS();
  }catch(error){toast("CoS request not confirmed. The same request ID is retained. "+error.message,true);await refreshCoS();}
};
setInterval(()=>{if(state.view==="rooms"&&state.room)refreshCoS();},2500);

$("reconcile-cos").onclick=async()=>{
  const context=roomContext(), id=$("reconcile-cos").dataset.requestId;
  if(!id)return;
  try{context.check();await api(`/api/v1/rooms/${context.id}/cos-requests/${id}/reconcile`,"POST",{});await refreshCoS();}
  catch(error){toast(error.message,true);}
};
