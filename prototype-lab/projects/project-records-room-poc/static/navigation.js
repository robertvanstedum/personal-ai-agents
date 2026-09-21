/* Device-local read positions contain only sequence numbers, scoped by identity. */
(()=>{
  let listing=false;
  const key=room=>`minimoi.read.${state.me?.id}.${room}`;
  const seen=room=>{try{return Number(localStorage.getItem(key(room)))||0;}catch{return 0;}};
  const latest=()=>Math.max(0,...(state.room?.events||[]).map(e=>e.seq));
  const atBottom=()=>{const m=$('messages');return m.scrollHeight-m.scrollTop-m.clientHeight<50;};
  const jump=element('button','Jump to latest','quiet');jump.id='jump-latest';jump.classList.add('hidden');$('messages').before(jump);
  function markRead(){if(!state.me||!state.room||state.view!=='rooms'||document.hidden||!atBottom())return;try{localStorage.setItem(key(state.room.id),String(Math.max(seen(state.room.id),latest())));}catch{}decorate();}
  function decorate(){for(const node of document.querySelectorAll('[data-session]')){const room=state.rooms.find(r=>r.id===node.dataset.session);if(!room)continue;node.querySelector('.unread-badge')?.remove();if(room.latest_seq>seen(room.id)){node.append(element('span','New','unread-badge'));node.setAttribute('aria-label',`${room.title} · unread activity`);}else node.removeAttribute('aria-label');}}
  function scroll(){jump.classList.toggle('hidden',atBottom());markRead();}
  jump.onclick=()=>{$('messages').scrollTo({top:$('messages').scrollHeight,behavior:'instant'});scroll();};
  $('messages').addEventListener('scroll',scroll,{passive:true});
  const toggle=element('button','☰ Rooms','quiet');toggle.id='mobile-rooms';toggle.setAttribute('aria-expanded','false');document.querySelector('#rooms-view>.page-header').prepend(toggle);
  const closeNav=()=>{document.body.classList.remove('room-nav-open');toggle.setAttribute('aria-expanded','false');};
  $('room-list').addEventListener('click',event=>{if(event.target.closest('button'))closeNav();});
  document.addEventListener('keydown',event=>{if(event.key==='Escape')closeNav();});
  const closeButton=element('button','Close navigation','quiet');closeButton.id='mobile-room-close';closeButton.onclick=closeNav;document.querySelector('.sidebar').prepend(closeButton);
  toggle.onclick=()=>{const open=document.body.classList.toggle('room-nav-open');toggle.setAttribute('aria-expanded',String(open));};
  window.addEventListener('hashchange',()=>{document.body.classList.remove('room-nav-open');toggle.setAttribute('aria-expanded','false');});
  window.addEventListener('records-signout',()=>{document.body.classList.remove('room-nav-open');jump.classList.add('hidden');});
  window.RecordsNavigation={decorate,roomRendered({oldLast}){
    if(!oldLast){const position=seen(state.room.id),first=state.room.events.find(e=>e.seq>position);if(position&&first){const divider=element('div','New since your last visit','unread-divider');$('event-'+first.id)?.before(divider);}}
    scroll();
  }};
  setInterval(async()=>{if(listing||!state.me||document.hidden)return;listing=true;try{await refreshRooms();}catch{}finally{listing=false;}},10000);
  if(state.room)window.RecordsNavigation.roomRendered({oldLast:''});
})();
