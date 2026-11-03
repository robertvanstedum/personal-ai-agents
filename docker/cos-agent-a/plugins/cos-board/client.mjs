// CoS adapter for the existing signed Board v1 service. No MC key or database access.
import { open, mkdir, writeFile, readFile, link, unlink } from 'node:fs/promises';
import { constants } from 'node:fs';
import { createHash, createHmac, randomBytes, randomUUID, timingSafeEqual } from 'node:crypto';
import { request } from 'node:http';
import { join } from 'node:path';

const BASE = '/internal/domain-api/v1/guild/board';
const OPS = {
  list: ['POST', '/notes/query', 'notes.read', ['query','state','limit','cursor']],
  read: ['GET', '/notes/', 'notes.read', ['note_ref']],
  save: ['POST', '/notes', 'note.create', ['text','label']],
  update: ['PATCH', '/notes/', 'note.update', ['note_ref','expected_revision','changes']],
  result: ['GET', '/operations/', 'operations.read', ['operation_ref']],
  undo: ['POST', '/operations/', 'operation.undo', ['operation_ref','expected_revision']],
  capabilities: ['GET', '/capabilities', 'capabilities.read', []],
};
const mutations = new Set(['save','update','undo']);
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const hash = v => createHash('sha256').update(v).digest('hex');
const canonical = v => JSON.stringify(sort(v));
function sort(v) {
  if (Array.isArray(v)) return v.map(sort);
  if (v && typeof v === 'object') return Object.fromEntries(Object.keys(v).sort().map(k=>[k,sort(v[k])]));
  return v;
}
function requireValue(ok) { if (!ok) throw new Error('Board request invalid; nothing sent'); }
export function wire(args) {
  requireValue(args && typeof args === 'object' && !Array.isArray(args));
  const op = OPS[args.operation]; requireValue(op);
  const [method, path, cap, fields] = op;
  requireValue(Object.keys(args).every(k=>k==='operation'||fields.includes(k)));
  const body = Object.fromEntries(fields.filter(k=>k in args&&!['note_ref','operation_ref'].includes(k)).map(k=>[k,args[k]]));
  let route = BASE + path;
  if (['read','update'].includes(args.operation)) {
    requireValue(typeof args.note_ref==='string' && /^[1-9][0-9]{0,17}$/.test(args.note_ref)); route+=args.note_ref;
  }
  if (['result','undo'].includes(args.operation)) {
    requireValue(typeof args.operation_ref==='string' && uuid.test(args.operation_ref)); route+=args.operation_ref;
    if(args.operation==='undo') route+='/undo';
  }
  if (args.operation==='save') requireValue(typeof args.text==='string' && [...args.text].length<=280 && args.text.trim().length>0);
  if (['update','undo'].includes(args.operation)) requireValue(Number.isSafeInteger(args.expected_revision)&&args.expected_revision>0);
  if(args.operation==='update') {
    requireValue(args.changes && typeof args.changes==='object' && !Array.isArray(args.changes) && Object.keys(args.changes).length>0);
    requireValue(Object.keys(args.changes).every(k=>['text','label','done'].includes(k)));
  }
  const raw = method==='GET'?'':canonical(body);
  requireValue(Buffer.byteLength(raw)<=16384);
  return {method,route,raw,cap:'guild.board.'+cap,mutation:mutations.has(args.operation)};
}
async function profileFromFile() {
  const file = await open('/run/secrets/cos-board-profile.json',constants.O_RDONLY|constants.O_NOFOLLOW);
  try {
    const st=await file.stat(); requireValue(st.isFile() && !(st.mode&0o077) && [0,process.getuid()].includes(st.uid) && st.size<=16384);
    return JSON.parse(await file.readFile('utf8'));
  } finally { await file.close(); }
}
async function operationIdentity(id,args,root,scope) {
  requireValue(typeof id==='string' && id.length>0 && id.length<=512);
  await mkdir(root,{recursive:true,mode:0o700});
  // Stable across cache loss, restart and credential rotation; a changed payload
  // with the same identity is also rejected by the domain's durable receipt.
  const bytes=Buffer.from(hash(canonical(['minimoi.cos-board-tool/1',...scope,id])).slice(0,32),'hex');
  bytes[6]=(bytes[6]&15)|80; bytes[8]=(bytes[8]&63)|128;
  const hex=bytes.toString('hex');
  const stable=[hex.slice(0,8),hex.slice(8,12),hex.slice(12,16),hex.slice(16,20),hex.slice(20)].join('-');
  const path=join(root,stable+'.json'), temporary=path+'.'+randomUUID();
  const value={args:canonical(args),operation_id:stable};
  await writeFile(temporary,JSON.stringify(value),{flag:'wx',mode:0o600});
  try { await link(temporary,path); } catch(e) { if(e.code!=='EEXIST')throw e; }
  finally { await unlink(temporary); }
  const saved=JSON.parse(await readFile(path,'utf8'));
  requireValue(saved.args===value.args && uuid.test(saved.operation_id));
  return saved.operation_id;
}
// Fixed private Docker service. No model destination, proxy, redirects or retry.
export function exchange(method,route,raw,headers) {
  return new Promise((resolve,reject)=>{
    let timer;
    const req=request({hostname:'cos-board',port:5014,path:route,method,headers},res=>{
      const chunks=[];let size=0;
      res.on('data',part=>{size+=part.length;if(size>65536)req.destroy(new Error('Board response too large'));else chunks.push(part);});
      res.on('error',reject);
      res.on('end',()=>{clearTimeout(timer);resolve({status:res.statusCode,raw:Buffer.concat(chunks),signature:res.headers['x-minimoi-receipt']});});
    });
    req.on('error',e=>{clearTimeout(timer);reject(e);});
    timer=setTimeout(()=>req.destroy(new Error('Board timeout')),7500);
    req.end(raw);
  });
}
export async function executeBoard(id,args,deps={}) {
  let operation_id='';
  try {
    const w=wire(args);
    const p=await (deps.readProfile||profileFromFile)();
    requireValue(p.actor==='cos' && p.domain==='guild' && p.board==='cos-board' && ['dev','prod'].includes(p.environment));
    for(const k of ['key_id','principal']) requireValue(typeof p[k]==='string' && /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(p[k]));
    requireValue(typeof p.secret_hex==='string' && /^[a-f0-9]{64}$/.test(p.secret_hex));
    requireValue(p.numeric_user_id==null || Number.isSafeInteger(p.numeric_user_id)&&p.numeric_user_id>0);
    if(w.mutation)operation_id=await operationIdentity(id,args,deps.cacheRoot||'/home/node/.openclaw/cos-board-requests',[p.principal,p.environment,p.board]);
    const key=Buffer.from(p.secret_hex,'hex'), nonce=randomBytes(16).toString('hex'), timestamp=String(Math.floor(Date.now()/1000));
    const identity=[p.key_id,p.domain,p.environment,p.principal,p.actor,p.numeric_user_id??null];
    const sign=v=>createHmac('sha256',key).update(v).digest('hex');
    const headers={'X-MiniMoi-Domain-Version':'1','X-MiniMoi-Key':p.key_id,'X-MiniMoi-Audience':p.domain,
      'X-MiniMoi-Environment':p.environment,'X-MiniMoi-Time':timestamp,'X-MiniMoi-Nonce':nonce,
      'Idempotency-Key':operation_id,'Content-Type':'application/json','Accept':'application/json',
      'X-MiniMoi-Signature':sign(JSON.stringify(['minimoi.domain-request/1',...identity,w.method,w.route,timestamp,nonce,operation_id,hash(w.raw)]))};
    let value;
    try {
      const r=await (deps.exchange||exchange)(w.method,w.route,w.raw,headers);
      requireValue(Number.isInteger(r.status)&&r.status>=200&&r.status<600&&!(r.status>=300&&r.status<400));
      requireValue(Buffer.isBuffer(r.raw)&&r.raw.length<=65536&&typeof r.signature==='string'&&/^[a-f0-9]{64}$/.test(r.signature));
      const expected=sign(Buffer.concat([Buffer.from(JSON.stringify(['minimoi.domain-response/1',...identity,nonce,r.status])+'\n'),r.raw]));
      requireValue(timingSafeEqual(Buffer.from(expected,'hex'),Buffer.from(r.signature,'hex')));
      value=JSON.parse(r.raw.toString('utf8'));
      if(r.status>=400) {
        requireValue(['not_applied','unknown'].includes(value.state) && /^[a-z0-9_]{1,80}$/.test(value.error));
        value={state:value.state,code:value.error,operation_id};
      } else if(w.mutation || args.operation==='result') {
        requireValue(['verified','not_applied','unknown'].includes(value.state));
        if(value.state==='verified') {
          const receipt=value.receipt;
          requireValue(receipt && receipt.operation_id===(w.mutation?operation_id:args.operation_ref)
            && receipt.principal===p.principal && receipt.executor==='cos' && receipt.environment===p.environment
            && receipt.board==='cos-board' && (!w.mutation||receipt.capability===w.cap));
        }
      }
    } catch {
      // Neither an unsigned error nor a dropped reply proves that nothing was saved.
      value={state:'unknown',operation_id,code:'board_result_unconfirmed',instruction:'Read the operation receipt before attempting another write. Do not create a replacement note.'};
    }
    return {content:[{type:'text',text:JSON.stringify(value)}],details:value};
  } catch {
    const value={state:'not_applied',operation_id,code:'board_request_or_configuration_invalid'};
    return {content:[{type:'text',text:JSON.stringify(value)}],details:value};
  }
}
