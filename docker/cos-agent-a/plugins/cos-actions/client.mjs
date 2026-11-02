import { readFile, writeFile, mkdir, link, unlink } from 'node:fs/promises';
import { createHash, randomUUID } from 'node:crypto';
import { join } from 'node:path';
function canonical(v) { return JSON.stringify(v, Object.keys(v).sort()); }
async function stableRequest(id, args, root) {
  if (typeof id !== 'string' || !id) throw new Error('Missing tool call identity; nothing saved');
  const key=createHash('sha256').update(id).digest('hex');
  await mkdir(root,{recursive:true,mode:0o700});
  const path=join(root,key+'.json');
  const request={args:canonical(args),body:{...args,op_id:key,at:new Date().toISOString()}};
  delete request.body.operation;
  const temporary=path+'.'+randomUUID();
  await writeFile(temporary,JSON.stringify(request),{flag:'wx',mode:0o600});
  try { await link(temporary,path); } catch(e) { if(e.code!=='EEXIST')throw e; }
  finally { await unlink(temporary); }
  const saved=JSON.parse(await readFile(path,'utf8'));
  if(saved.args!==request.args) throw new Error('Tool call identity reused with different arguments; nothing saved');
  return saved.body;
}
export async function executeActions(id,args,deps={}) {
  const {operation, ...body} = args;
  if (!['list','get','open','update','comment','close'].includes(operation)) throw new Error('Unknown Actions operation');
  const read = operation === 'list' || operation === 'get';
  const payload=read?null:await stableRequest(id,args,deps.cacheRoot || '/home/node/.openclaw/cos-actions-requests');
  const token = (await (deps.readToken || (()=>readFile(process.env.COS_ACTIONS_TOKEN_FILE || '/run/secrets/cos-actions-token','utf8')))()).trim();
  const base = 'http://cos-scheduler:8769/internal/cos-actions/v1';
  const suffix = operation === 'list' ? '/items?offset='+encodeURIComponent(body.offset || 0) : operation === 'get' ? '/items/'+encodeURIComponent(body.item_id) : '/'+operation;
  const response = await (deps.fetch || fetch)(base+suffix, {method:read?'GET':'POST', redirect:'error', signal:AbortSignal.timeout(15000),
    headers:{Authorization:'Bearer '+token, 'Content-Type':'application/json'},
    ...(read?{}:{body:JSON.stringify(payload)})});
  const result = await response.json();
  return {content:[{type:'text',text:JSON.stringify(result)}],details:result};
}
