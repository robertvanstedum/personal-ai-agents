// Image-build only. Canonical handbook bytes remain root-owned under /opt.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {pathToFileURL} from 'node:url';
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
export const MANIFEST_SHA256 = 'f6e9575ee765374418b12ed2b68e4856e7a5d6496397320e6e93d7ad410676fb';
const names = ['v0.1.1/ADOPTION.md', 'v0.1.1/PROJECT.md',
  'v0.1.1/skills/minimoi-engineering/SKILL.md',
  'v0.1.1/skills/minimoi-engineering/references/board-and-services.md',
  'v0.1.1/skills/minimoi-engineering/references/build-and-deploy.md',
  'v0.1.1/skills/minimoi-engineering/references/regression.md'];
const start = 'BEGIN_MINIMOI_MC_HANDBOOK 0.1.1';
const end = 'END_MINIMOI_MC_HANDBOOK 0.1.1';
function boundedRead(file) {
  const stat = fs.lstatSync(file);
  if (!stat.isFile() || stat.size > 32768) throw new Error('handbook_file_invalid');
  const bytes = fs.readFileSync(file);
  if (bytes.length > 32768) throw new Error('handbook_file_invalid');
  return bytes;
}
export function compile(base, root, roleReceipt) {
  const manifestBytes = boundedRead(path.join(root, 'manifest.json'));
  if (sha(manifestBytes) !== MANIFEST_SHA256) throw new Error('handbook_manifest_changed');
  const manifest = JSON.parse(manifestBytes);
  if (manifest.format !== 'minimoi-mc-handbook/1' || manifest.candidate_version !== '0.1.1' ||
      manifest.status !== 'review_candidate' || manifest.active_runtime_version !== null ||
      JSON.stringify(Object.keys(manifest.files)) !== JSON.stringify(names)) throw new Error('handbook_manifest_invalid');
  const files = Object.fromEntries(names.map(name => {
    const bytes = boundedRead(path.join(root, name));
    if (sha(bytes) !== manifest.files[name]) throw new Error('handbook_source_changed');
    return [name, bytes.toString('utf8')];
  }));
  const prefix = `${start}\n` +
    `Pinned handbook: /opt/minimoi/cos-agent-a/handbook/v0.1.1/\n` +
    `Manifest SHA-256: ${MANIFEST_SHA256}\n` +
    `Source status remains review_candidate; loading is not behavior verification or owner acceptance.\n` +
    `PROJECT.md and engineering SKILL.md follow verbatim. Resolve their relative references under\n` +
    `/opt/minimoi/cos-agent-a/handbook/v0.1.1/skills/minimoi-engineering/; read ADOPTION.md under the pinned handbook path.\n` +
    ['board-and-services.md', 'build-and-deploy.md', 'regression.md'].map(name =>
      `/opt/minimoi/cos-agent-a/handbook/v0.1.1/skills/minimoi-engineering/references/${name}`).join('\n') + '\n\n' +
    files['v0.1.1/PROJECT.md'] + '\n' + files['v0.1.1/skills/minimoi-engineering/SKILL.md'] + `\n${end}\n\n`;
  // Reapplying this exact compiler does not duplicate the prefix. Foreign/stale prefixes refuse.
  const unprefixed = base.startsWith(prefix) ? base.slice(prefix.length) : base;
  if (unprefixed.includes('BEGIN_MINIMOI_MC_HANDBOOK') || unprefixed.includes('END_MINIMOI_MC_HANDBOOK')) throw new Error('handbook_prefix_conflict');
  if (roleReceipt.format !== 'minimoi-role-load/1' || sha(unprefixed) !== (roleReceipt.handbook ? roleReceipt.handbook.base_policy_sha256 : roleReceipt.policy_sha256)) throw new Error('role_receipt_mismatch');
  const policy = prefix + unprefixed;
  if (policy.length > 32000) throw new Error('role_exceeds_bootstrap_budget');
  return {policy, receipt: {...roleReceipt, policy_sha256: sha(policy), policy_chars: policy.length,
    handbook: {version: '0.1.1', manifest_sha256: MANIFEST_SHA256, sources: manifest.files,
      base_policy_sha256: sha(unprefixed), source_status: manifest.status,
      loaded_into_policy: true, behavior_verified: false, owner_accepted: false}}};
}
if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  const [basePath, root, outputPath, receiptPath] = process.argv.slice(2);
  if (!receiptPath || process.argv.length !== 6) throw new Error('handbook_build_arguments');
  const result = compile(fs.readFileSync(basePath, 'utf8'), root, JSON.parse(fs.readFileSync(receiptPath, 'utf8')));
  fs.writeFileSync(outputPath, result.policy, {mode: 0o644});
  fs.writeFileSync(receiptPath, JSON.stringify(result.receipt, null, 2) + '\n', {mode: 0o644});
}
