/* Canonical history owns messages; device state only keeps unsent drafts and request identities. */
(() => {
  const input = document.getElementById('send-input'), log = document.getElementById('chat-log');
  const state = document.getElementById('save-state');
  const hero = document.getElementById('cos-greeting');   // the greeting card, shown again whenever a chat is empty
  const greeting = () => { const h = new Date().getHours(); return h >= 5 && h < 12 ? 'Good morning' : h >= 12 && h < 18 ? 'Good afternoon' : 'Good evening'; };
  function greet() { for (const el of hero.querySelectorAll('[data-greeting]')) { const text = greeting() + ', Robert.'; if (el.textContent !== text) el.textContent = text; } }
  greet(); setInterval(greet, 60000);   // a page left open across 12:00, 18:00 or 5:00 follows the clock
  hero.remove();   // it comes back after the first read, and only if the chat is empty
  const base = location.pathname.replace(/\/ui(?:\/[^/]*)?\/?$/, '') + '/ui/';
  let voiceConversation = null, cid = null, conversation = null, before = null, next = null, busy = false, viewingEarlier = false, generation = 0, signature = '';
  const storage = (key, value) => { try { if (value !== undefined) sessionStorage.setItem(key, value); return sessionStorage.getItem(key); } catch (_) { return null; } };
  let device; try { device = localStorage.getItem('minimoi-device'); if (!device) { device = crypto.randomUUID(); localStorage.setItem('minimoi-device', device); } } catch (_) { device = 'browser'; }
  const draftKey = () => 'cos-draft:' + cid;
  const pendingKey = () => 'cos-pending:' + cid;
  const headers = () => ({'Content-Type':'application/json', ...cosWriteHeaders()});
  async function api(path, body) {
    const r = await fetch(base + path, {method: body === undefined ? 'GET' : 'POST', headers: headers(), cache: 'no-store', ...(body === undefined ? {} : {body:JSON.stringify(body)})});
    const data = await r.json(); if (!r.ok) throw new Error(data.error || 'Unavailable'); return data;
  }
  const pending = () => { try { return JSON.parse(storage(pendingKey()) || 'null'); } catch (_) { return null; } };
  function clearPending() { storage(pendingKey(), 'null'); }
  function persistDraft() { if (cid) {storage(draftKey(), input.value);storage(draftKey()+':files',JSON.stringify(attached));} }
  // Review 06: your turns on the right; CoS's on the left under its name and the time.
  const clock = iso => { const d = new Date(iso || ''); if (isNaN(d)) return ''; const t = d.toLocaleTimeString([], {hour:'numeric', minute:'2-digit'});
    return d.toDateString() === new Date().toDateString() ? t : d.toLocaleDateString([], {day:'numeric', month:'short'}) + ', ' + t; };
  function row(label, text, id, at) {
    const mine = label === 'You';
    const item = document.createElement('div'); item.className = 'msg ' + (mine ? 'msg-user' : 'msg-cos'); item.dataset.turn = id || '';
    const who = document.createElement('div'); who.className = 'msg-label'; who.textContent = mine ? label : 'Chief of Staff';
    const time = clock(at); if (time) who.dataset.time = time;
    const words = document.createElement('div'); words.className = 'msg-text'; words.textContent = text;
    item.append(who, words); return item;
  }
  async function reload(earlier = false) {
    if (!cid || busy || input.disabled || (viewingEarlier && !earlier)) return;
    const selected = cid, epoch = generation;
    const d = await api('history/conversations/' + encodeURIComponent(cid) + '/turns?limit=50' + (earlier && before ? '&before_seq=' + before : ''));
    if (selected !== cid || epoch !== generation || input.disabled) return;
    const sig = JSON.stringify(d); if (!earlier && sig === signature) return;
    signature = sig; before = d.next_before_seq;
    const frag = document.createDocumentFragment();
    for (const turn of d.items) {
      frag.append(row('You', turn.user_text, turn.turn_id, turn.created_at));
      if (turn.reply_text) frag.append(row('CoS', turn.reply_text, turn.turn_id, turn.answered_at || turn.created_at));
      else frag.append(row('CoS', turn.status === 'pending' ? 'Working on it' : 'This reply was interrupted. Your message is saved.', turn.turn_id, turn.created_at));
      const p = pending();
      if (p && turn.turn_id === 't-' + p.id.replaceAll('-', '')) { state.textContent = 'Saved'; if (turn.status !== 'pending') { if (input.value === JSON.parse(p.key)[0]) { input.value=''; persistDraft(); } const sentIds=new Set((p.files||[]).map(f=>f.file_id)); attached=attached.filter(f=>!sentIds.has(f.file_id));renderTray();clearPending(); } }
    }
    for (const note of d.notes || []) frag.append(row('CoS', note.text || note.body || 'Saved update'));
    const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 80;
    if (earlier) { viewingEarlier = true; const h = log.scrollHeight; log.prepend(frag); log.scrollTop += log.scrollHeight - h; }
    else { log.replaceChildren(frag); if (!d.items.length && !(d.notes || []).length) { greet(); log.append(hero); } if (atBottom) log.scrollTop = log.scrollHeight; }
    document.getElementById('history-earlier').hidden = !before;
  }
  async function releaseVoice() {
    const pending=[]; let deadline;
    document.dispatchEvent(new CustomEvent('minimoi:companion-opening',{detail:{waitUntil:p=>pending.push(Promise.resolve(p))}}));
    try { await Promise.race([Promise.all(pending),new Promise((_,reject)=>{deadline=setTimeout(()=>reject(new Error('Voice termination pending')),8000);})]); }
    finally { clearTimeout(deadline); }
  }
  async function select(id, doc) {
    if (input.disabled || uploading) throw new Error('Wait for the current message or upload.');
    if (cid && cid !== id) await releaseVoice();
    persistDraft(); await api('history/selection', {device, conversation_id:id});
    cid = id; conversation = doc; generation++; signature = ''; before = null; viewingEarlier = false;
    input.value = storage(draftKey()) || '';
    const recover=pending();
    let draftFiles=[]; try {draftFiles=JSON.parse(storage(draftKey()+':files')||'[]');}catch(_){}
    attached=recover?.files || (Array.isArray(draftFiles)?draftFiles:[]);
    if(recover && !input.value) input.value=recover.text || '';
    renderTray(); persistDraft();
    state.textContent = pending() ? 'Checking…' : 'Saved';
    window.cosHistory.conversationId = cid; showTitle(doc); markCurrent();
    // Existing voice sessions address owner only; never leak another conversation into that context.
    document.body.dataset.historyPrivate = String(Boolean(doc?.private));
    const voice = document.getElementById('btn-voice'); window.cosVoiceConversationAllowed = cid === voiceConversation && !doc?.private;
    voice.disabled = !window.cosVoiceConversationAllowed;
    if (!window.cosVoiceConversationAllowed) { voice.title = 'Voice is available in the original CoS conversation.'; document.getElementById('voice-status').textContent = 'Voice is available in the original CoS conversation.'; }
    await reload();
    log.scrollTop = Number(storage('cos-scroll:' + cid) || log.scrollHeight);
  }
  async function list(more = false) {
    const d = await api('history/conversations?limit=50' + (more && next ? '&cursor=' + encodeURIComponent(next) : ''));
    const el = document.getElementById('history-list'); if (!more) el.replaceChildren();
    for (const c of d.items) { const b = document.createElement('button'); b.type='button'; b.className='history-row'; b.textContent=(c.title || 'Conversation') + (c.private ? ' · Private' : ''); b.onclick=async()=>{try { await select(c.conversation_id,c); document.getElementById('history-sheet').close(); } catch (_) { state.textContent='Could not open that conversation.'; }}; el.append(b); }
    next=d.next; document.getElementById('history-more').hidden=!next;
  }
  window.cosHistory = {ready:false, device, conversationId:null, persistDraft,
    requestId(text, files) { const previous=pending(), key=JSON.stringify([text, files.map(f=>f.file_id)]); if(previous && previous.key!==key){state.textContent='Checking the previous message before sending another.';return null;} const id=previous?.id || crypto.randomUUID(); storage(pendingKey(),JSON.stringify({id,key,text,files})); state.textContent='Checking…'; return id; },
    confirm(h, operation) { const receipt = operation && ((['note_save','reminder_set'].includes(operation.type) && ['saved','deduplicated'].includes(operation.status) && operation.operation_id) || (operation.type==='session_reset' && operation.status==='reset')); if (h?.saved || receipt) {clearPending();state.textContent='Saved';} else state.textContent='Save not confirmed'; refreshList(); },
    uncertain(h) { if(h?.refused){clearPending();state.textContent='Not saved';}else state.textContent='Checking…'; }
  };
  log.addEventListener('scroll',()=>{if(cid)storage('cos-scroll:' + cid,String(log.scrollTop));});
  input.addEventListener('input',persistDraft); window.addEventListener('pagehide',persistDraft);
  for(const b of document.querySelectorAll('[data-close-sheet]')) b.onclick=()=>b.closest('dialog').close();
  document.getElementById('history-open').onclick=()=>{document.getElementById('history-sheet').showModal();list().catch(()=>state.textContent='History is unavailable.');};
  document.getElementById('history-more').onclick=()=>list(true).catch(()=>state.textContent='Could not load more history.');
  document.getElementById('history-earlier').onclick=()=>{if(viewingEarlier){viewingEarlier=false;signature='';document.getElementById('history-earlier').textContent='Earlier messages';reload().catch(()=>state.textContent='Could not load messages.');}else{reload(true).then(()=>{document.getElementById('history-earlier').hidden=false;document.getElementById('history-earlier').textContent='Return to latest';}).catch(()=>state.textContent='Could not load earlier messages.');}};
  let creatingConversation = false;
  document.querySelectorAll('[data-new-conversation]').forEach(button => { button.onclick=async()=>{
    if(creatingConversation)return;
    if(input.disabled||uploading){state.textContent='Wait for the current message or upload.';return;}
    creatingConversation=true;
    const controls=[...document.querySelectorAll('[data-new-conversation]')];controls.forEach(b=>b.disabled=true);
    try{const c=await api('history/conversations',{title:'New conversation'});await select(c.conversation_id,c);closeDrawer();refreshList();document.getElementById('history-sheet').close();if(!document.getElementById('tab-confer').classList.contains('active'))document.querySelector('.tab-btn[data-tab="confer"]').click();input.focus();}
    catch(_){state.textContent='Could not create a conversation.';}
    finally{creatingConversation=false;controls.forEach(b=>b.disabled=false);}
  };});
  document.getElementById('mc-page').href=location.origin + '/guild-next/guild/build';
  document.getElementById('existing-rooms').href=location.origin + '/guild-next/guild/rooms';
  document.getElementById('both-open').onclick=async()=>{
    const frame=document.getElementById('mc-both'), button=document.getElementById('both-open'), open=frame.hidden;
    button.disabled=true;
    try {
      if(open) await releaseVoice();
      else { try {frame.contentDocument?.dispatchEvent(new frame.contentWindow.CustomEvent('guild:mic-claim',{detail:{who:'companion-closed'}}));} catch(_){} }
      frame.hidden=!open;document.body.classList.toggle('both-open',open);button.setAttribute('aria-pressed',String(open));
      if(open&&!frame.src)frame.src=location.origin + '/guild-next/guild/companion/cos';
    } catch(_) {state.textContent='Voice has not confirmed it ended. Finish it before opening MC.';}
    finally {button.disabled=false;}
  };
  document.addEventListener('keydown',e=>{if(e.ctrlKey && e.code==='Backquote'){e.preventDefault();document.getElementById('both-open').click();}});
  const files=document.getElementById('cos-files'); document.getElementById('files-sheet-content').append(files); files.open=true;
  document.getElementById('files-open').onclick=()=>{document.getElementById('files-sheet').showModal();loadCosFiles();};
  document.getElementById('popout').onclick=()=>window.open(location.href, '_blank', 'popup,width=1000,height=850');
  document.getElementById('picture-mode').onclick=async()=>{const modes=['shown','faded','hidden'];const value=modes[(modes.indexOf(document.body.dataset.picture)+1)%3];try{await api('history/preferences',{device,picture:value});document.body.dataset.picture=value;}catch(_){state.textContent='Picture preference was not saved.';}};
  // ── Review 06: the conversation list, the slim header, ⋯, and the phone bar. Presentation only: rows select with the
  // same calls as the History sheet; every control below hands its click to the control that already owned it. ──
  const body = document.body, wide = matchMedia('(min-width: 900px)');
  const rows = document.querySelector('[data-conv-list]'), filter = document.querySelector('[data-list-filter]');
  const moreRows = document.querySelector('[data-conv-list-more]'), scrim = document.querySelector('.cos-scrim');
  const local = (key, value) => { try { if (value !== undefined) localStorage.setItem(key, value); return localStorage.getItem(key); } catch (_) { return null; } };
  let conversations = [], listNext = null, listing = null;
  const titleOf = c => { const t = String(c?.title || '').trim(); return !t || t === 'New conversation' ? 'New chat' : t; };
  function showTitle(doc) {
    const text = titleOf(doc) + (doc?.private ? ' · Private' : '');
    for (const el of document.querySelectorAll('[data-conv-title],[data-conv-title-short]')) el.textContent = text;
  }
  const dayStart = d => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const daysAgo = d => Math.round((dayStart(new Date()) - dayStart(d)) / 864e5);
  function when(iso) {
    const d = new Date(iso || ''); if (isNaN(d)) return '';
    const n = daysAgo(d);
    if (n <= 0) return d.toLocaleTimeString([], {hour:'numeric', minute:'2-digit'});
    if (n < 7) return d.toLocaleDateString([], {weekday:'short'});
    return d.toLocaleDateString([], {day:'numeric', month:'short'});
  }
  function groupOf(iso) { const d = new Date(iso || ''); if (isNaN(d)) return 'Earlier'; const n = daysAgo(d); return n <= 0 ? 'Today' : n === 1 ? 'Yesterday' : 'Earlier'; }
  function markCurrent() {
    let found = false;
    for (const b of rows.querySelectorAll('[data-conversation-id]')) { const on = b.dataset.conversationId === cid; found ||= on; b.setAttribute('aria-current', String(on)); }
    if (cid && !found && !filter.value.trim() && !listing && !conversations.some(c => c.conversation_id === cid)) refreshList();   // a chat the list hasn't seen yet
  }
  function paintList() {
    const q = filter.value.trim().toLowerCase(); let group = null; rows.replaceChildren();
    const shown = conversations.filter(c => !q || titleOf(c).toLowerCase().includes(q));
    for (const c of shown) {
      const g = groupOf(c.last_activity_at || c.created_at);
      if (g !== group) { group = g; const h = document.createElement('div'); h.className = 'cos-list-group'; h.textContent = g; rows.append(h); }
      const b = document.createElement('button'); b.type = 'button'; b.className = 'cos-list-row'; b.dataset.conversationId = c.conversation_id;
      const t = document.createElement('span'); t.className = 't'; t.textContent = titleOf(c) + (c.private ? ' · Private' : '');
      const w = document.createElement('span'); w.className = 'w'; w.textContent = when(c.last_activity_at || c.created_at);
      b.append(t, w); b.title = t.textContent;
      b.onclick = async () => { try { await select(c.conversation_id, c); closeDrawer(); } catch (_) { state.textContent = 'Could not open that conversation.'; } };
      rows.append(b);
    }
    if (!shown.length) { const p = document.createElement('p'); p.className = 'cos-list-empty'; p.textContent = q ? 'No chat matches.' : 'No chats yet.'; rows.append(p); }
    moreRows.hidden = !listNext || Boolean(q);
    markCurrent();
  }
  async function loadList(more = false) {
    const d = await api('history/conversations?limit=50' + (more && listNext ? '&cursor=' + encodeURIComponent(listNext) : ''));
    conversations = more ? conversations.concat(d.items) : d.items; listNext = d.next; paintList();
  }
  function refreshList() {   // one read at a time; a request while one runs is folded into it
    if (listing) return listing;
    listing = loadList().catch(() => {}).finally(() => { listing = null; });
    return listing;
  }
  filter.addEventListener('input', paintList);
  moreRows.onclick = () => loadList(true).catch(() => { state.textContent = 'Could not load more conversations.'; });
  // ‹ folds the list to the rail (remembered on this device); ☰ unfolds it, or opens it over the chat when there's no room.
  const drawerMode = () => !wide.matches || body.classList.contains('show-cos-board');
  function paintFold() { body.dataset.list = local('cos-list-folded') === '1' ? 'folded' : 'open'; }
  function openList(focus) {
    if (drawerMode()) { body.dataset.drawer = 'open'; scrim.hidden = false; fold.setAttribute('aria-label', 'Close the list'); }
    else { local('cos-list-folded', '0'); paintFold(); }
    refreshList();
    if (focus) requestAnimationFrame(() => filter.focus());
  }
  function closeDrawer() { if (body.dataset.drawer) { delete body.dataset.drawer; scrim.hidden = true; fold.setAttribute('aria-label', 'Fold the list'); } }
  document.querySelectorAll('[data-list-open]').forEach(b => b.addEventListener('click', () => openList(false)));
  document.querySelector('[data-list-search]')?.addEventListener('click', () => openList(true));
  const fold = document.querySelector('[data-list-fold]');
  fold.addEventListener('click', () => { if (body.dataset.drawer) closeDrawer(); else { local('cos-list-folded', '1'); paintFold(); } });
  scrim.addEventListener('click', closeDrawer);
  document.querySelectorAll('[data-new-conversation]').forEach(b => b.addEventListener('click', closeDrawer));
  document.querySelectorAll('.tab-btn[data-tab]').forEach(b => b.addEventListener('click', closeDrawer));
  wide.addEventListener('change', closeDrawer);
  paintFold();
  // ⋯ holds History, Picture, Side by side, its own window and voice; a choice closes it.
  const moreMenu = document.querySelector('[data-conv-more]');
  moreMenu.querySelector('[data-conv-menu]').addEventListener('click', e => { if (e.target.closest('button')) moreMenu.open = false; });
  document.addEventListener('click', e => { if (moreMenu.open && !moreMenu.contains(e.target)) moreMenu.open = false; });
  document.addEventListener('keydown', e => { if (e.key !== 'Escape') return; if (moreMenu.open) { moreMenu.open = false; moreMenu.querySelector('summary').focus(); } closeDrawer(); });
  // "Talk with CoS (voice)" presses the voice button, which keeps every rule about when voice may start.
  const voiceButton = document.getElementById('btn-voice'), talk = document.querySelector('[data-voice-start]');
  const mirrorVoice = () => { talk.disabled = voiceButton.disabled; talk.title = voiceButton.title; };
  talk.addEventListener('click', () => voiceButton.click());
  new MutationObserver(mirrorVoice).observe(voiceButton, {attributes:true, attributeFilter:['disabled','title']}); mirrorVoice();
  // Files shows only when CoS has files to give; its count follows the list in the Files sheet.
  const filesButton = document.getElementById('files-open'), fileList = document.getElementById('cos-files-list');
  const countFiles = () => { const n = fileList.querySelectorAll('a').length; filesButton.hidden = !n; document.querySelector('[data-files-count]').textContent = n ? String(n) : ''; };
  new MutationObserver(countFiles).observe(fileList, {childList:true, subtree:true}); countFiles();
  if (typeof loadCosFiles === 'function') loadCosFiles();
  // MC's face, from the portal's own pictures (like the MC ↗ link itself).
  const face = document.querySelector('[data-mc-face]');
  if (face) { face.onerror = () => document.getElementById('mc-page').classList.add('no-face'); face.src = location.origin + '/static/guild/guild-mc-portrait.jpg'; }
  // On a phone the composer rides the visual viewport, above the keyboard.
  const phoneWidth = matchMedia('(max-width: 639px)');
  if (window.visualViewport) {
    const fit = () => { const root = document.documentElement.style;
      if (phoneWidth.matches) { root.setProperty('--app-h', Math.round(visualViewport.height) + 'px'); if (visualViewport.offsetTop) window.scrollTo(0, 0); }
      else root.removeProperty('--app-h'); };
    visualViewport.addEventListener('resize', fit); visualViewport.addEventListener('scroll', fit); phoneWidth.addEventListener('change', fit); fit();
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshList(); });
  refreshList();
  (async()=>{try{const d=await api('history/selection?device='+encodeURIComponent(device)); voiceConversation=d.voice_conversation_id; await select(d.conversation_id,d.conversation);window.cosHistory.ready=true;const p=await api('history/preferences?device='+encodeURIComponent(device));document.body.dataset.picture=p.picture;}catch(_){state.textContent='History is unavailable. Nothing will be sent until it returns.';}})();
  setInterval(()=>{if(!document.hidden)reload().catch(()=>{state.textContent='History could not be refreshed.';});},5000);
})();
