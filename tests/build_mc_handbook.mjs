import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {compile} from '../docker/cos-agent-a/build-mc-handbook.mjs';
const sha = b => crypto.createHash('sha256').update(b).digest('hex');
// Default: the repository's own handbook, so a bare `node tests/build_mc_handbook.mjs` checks what the image builds.
const source = process.argv[2] ?? fileURLToPath(new URL('../services/mc_operations/handbook', import.meta.url));
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'mc-handbook-fixture-'));
try {
  fs.cpSync(source, root, {recursive:true});
  const base = 'Existing owner role and common handoffs.\n';
  const receipt = {format:'minimoi-role-load/1', role_version:'same-role', policy_sha256:sha(base), acknowledgement:{state:'pending',runtime_receipt:null}};
  const first = compile(base, root, receipt);
  const second = compile(first.policy, root, first.receipt);
  assert.equal(first.policy, second.policy);
  assert.deepEqual(first.receipt, second.receipt);
  assert.equal(first.receipt.role_version,'same-role');
  assert.deepEqual(first.receipt.acknowledgement,receipt.acknowledgement);
  assert.equal(first.receipt.handbook.owner_accepted,false);
  assert.equal(first.receipt.handbook.version,'0.1.1');assert.ok(first.policy.startsWith('BEGIN_MINIMOI_MC_HANDBOOK 0.1.1\n'));
  assert.ok(fs.readFileSync(path.join(root,'v0.1.1/skills/minimoi-engineering/references/build-and-deploy.md'),'utf8').includes('\n## Outside reports and GitHub issues\n'));
  assert.ok(first.policy.endsWith(base));
  for (const name of ['board-and-services.md','build-and-deploy.md','regression.md']) assert.ok(first.policy.includes('/opt/minimoi/cos-agent-a/handbook/v0.1.1/skills/minimoi-engineering/references/'+name));
  assert.throws(()=>compile('x'.repeat(32000),root,{...receipt,policy_sha256:sha('x'.repeat(32000))}),/bootstrap_budget/);
  assert.throws(()=>compile('BEGIN_MINIMOI_MC_HANDBOOK stale\n'+base,root,receipt),/prefix_conflict/);
  assert.throws(()=>compile(base+'changed',root,receipt),/receipt_mismatch/);
  const project=path.join(root,'v0.1.1/PROJECT.md');const orig=fs.readFileSync(project);
  fs.appendFileSync(project,'tamper');assert.throws(()=>compile(base,root,receipt),/source_changed/);fs.writeFileSync(project,orig);
  const manifest=path.join(root,'manifest.json');fs.appendFileSync(manifest,' ');assert.throws(()=>compile(base,root,receipt),/manifest_changed/);
  console.log('10 bounded handbook checks passed');
} finally {fs.rmSync(root,{recursive:true,force:true});}
