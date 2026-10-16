// Build-time only: canonical reviewed artifacts become native platform policy.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
const [basePath, docsPath, lockPath, outputPath, receiptPath] = process.argv.slice(2);
if (!receiptPath || process.argv.length !== 7) throw new Error('role_build_arguments');
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const lock = JSON.parse(fs.readFileSync(lockPath, 'utf8'));
const names = ['MASTER_CRAFTSMAN_JOB_DESCRIPTION.md', 'MASTER_CRAFTSMAN_PERSONA.md',
  'MASTER_CRAFTSMAN_WORKING_AGREEMENT_2026-10-12.md'];
if (lock.format !== 'minimoi-owner-role-lock/1' || lock.approved_by !== 'Robert' ||
    JSON.stringify(Object.keys(lock.sources)) !== JSON.stringify(names)) throw new Error('role_lock_invalid');
const sections = names.map(name => {
  const bytes = fs.readFileSync(path.join(docsPath, name));
  if (bytes.length > 16384 || sha(bytes) !== lock.sources[name]) throw new Error('approved_role_changed');
  return `### Canonical source: ${name}\nSHA-256: ${lock.sources[name]}\n\n${bytes.toString('utf8')}`;
});
const version = sha(JSON.stringify(lock));
const role = `\n\n## Authoritative owner-approved MC role\nRole version: ${version}\n` +
  `Owner approval: ${lock.approved_by}, ${lock.approved_on}.\n` +
  `Build-time handshake state: pending. Loading these documents does not acknowledge them.\n` +
  `These exact standing-role artifacts are platform instructions for your shared CoS/MC role,\n` +
  `not retrieved history. They do not expand task authorization or prove access, execution,\n` +
  `capabilities, or acceptance. Use current runtime evidence for those claims.\n` +
  `When Robert requests the handshake, respond in your own words with your understanding,\n` +
  `available and missing action paths, and the first responsibility you take up.\n` +
  `Only a real recorded runtime reply tied to this role version is acknowledgement evidence;\n` +
  `a builder or deployment must never mark acknowledgement on your behalf.\n\n` +
  sections.join('\n\n') + `\n\nEND_MINIMOI_OWNER_ROLE ${version}\n`;
const policy = fs.readFileSync(basePath, 'utf8') + role;
if (policy.length > 32000) throw new Error('role_exceeds_bootstrap_budget');
fs.writeFileSync(outputPath, policy, {mode: 0o644});
fs.writeFileSync(receiptPath, JSON.stringify({format: 'minimoi-role-load/1', role_version: version,
  approval: {by: lock.approved_by, on: lock.approved_on}, sources: lock.sources,
  policy_sha256: sha(policy), policy_chars: policy.length,
  acknowledgement: {state: 'pending', runtime_receipt: null},
  board_followthrough: {state: 'pending', receipt: null}}, null, 2) + '\n');
