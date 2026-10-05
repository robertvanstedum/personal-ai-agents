// Reuse the reviewed MiniMoi streaming relay; bind only to host loopback.
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';

const source=process.env.MC_RELAY_SOURCE;
const expected=process.env.MC_RELAY_SOURCE_SHA256;
if (!source || createHash('sha256').update(readFileSync(source)).digest('hex')!==expected) {
  throw new Error('MC relay source changed; review and repin before starting');
}
const caller=process.env.MC_RELAY_TOKEN || '';
const upstream=process.env.MC_OPENCLAW_GATEWAY_TOKEN || '';
if (caller.length<32 || upstream.length<16 || caller===upstream || process.env.MC_RELAY_TARGET!=='http://127.0.0.1:18789') {
  throw new Error('MC relay credentials or local target invalid');
}
const {server}=await import(pathToFileURL(source).href);
server.listen(18796,'127.0.0.1',()=>console.log('MC native relay listening on loopback:18796'));
