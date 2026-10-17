// Image-build seed only. Never run against a mounted/adaptable workspace.
import fs from 'node:fs';
import crypto from 'node:crypto';
const [source, lockPath, seed] = process.argv.slice(2);
if (!seed || process.argv.length !== 5) throw new Error('soul_seed_arguments');
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const expected = 'f07078f5c913323f1098381faf44d38f77223258e48498ddd0552f53042c4f1b';
const bytes = fs.readFileSync(source);
const lock = JSON.parse(fs.readFileSync(lockPath, 'utf8'));
if (bytes.length > 16384 || sha(bytes) !== expected ||
    lock.sources?.['MASTER_CRAFTSMAN_PERSONA.md'] !== expected ||
    lock.format !== 'minimoi-owner-role-lock/1' || lock.approved_by !== 'Robert')
  throw new Error('canonical_persona_changed');
const text = bytes.toString('utf8');
const supersession = 'Supersedes the Scottish voice\npreference recorded on 14 October, including in the older working agreement.';
const first = text.indexOf('## Your manner\n');
const stop = text.indexOf('## Persistence and revision\n');
const final = text.indexOf('Preserve adaptable SOUL, USER, MEMORY, credentials and history.');
const tail = text.indexOf('\n\nRevision record:', final);
if (!text.includes(supersession) || first < 0 || stop <= first || final <= stop || tail <= final)
  throw new Error('canonical_projection_changed');
const rendered = supersession + '\n\n' + text.slice(first, stop).trimEnd() + '\n\n' + text.slice(final, tail).trimEnd();
if (sha(rendered) !== 'dfa60e72b7158fcf303fa4f5fcf97386031cf2f46d262421127cff2f50420efc')
  throw new Error('rendered_persona_changed');
const old = fs.readFileSync(seed, 'utf8');
if (old.includes('MINIMOI_PARTNER_PERSONA_')) throw new Error('seed_already_managed');
const block = '<!-- MINIMOI_PARTNER_PERSONA_BEGIN v1 -->\n' +
  `Canonical: MASTER_CRAFTSMAN_PERSONA.md version1.1; source_sha256=${expected}; content_sha256=${sha(rendered)}\n` +
  rendered + '\n<!-- MINIMOI_PARTNER_PERSONA_END v1 -->\n';
const result = old + (old.endsWith('\n\n') ? '' : old.endsWith('\n') ? '\n' : '\n\n') + block;
if (result.length > 32000) throw new Error('soul_seed_exceeds_bootstrap_budget');
fs.writeFileSync(seed, result);
