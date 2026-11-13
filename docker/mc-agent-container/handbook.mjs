// Master Craftsman's handbook and skills, loaded at start (start.sh step 2). Run by start.sh only:
//   node handbook.mjs <image handbook dir> <workspace> <version> <manifest sha256> [<persona release dir>]
//
// Skill mode (#553; a persona release whose manifest says "handbook": {"load": "skill"}, given as the fifth argument):
// MC's procedures are OpenClaw workspace skills, read on demand. Only each skill's name and one-line description sit in
// the system prompt (OpenClaw docs, tools/skills: <workspace>/skills is the highest-precedence skill root). This replaces
// <workspace>/skills wholesale with the release's own skills (every file under skills/ in its manifest, checked again
// against its hash) plus the handbook's engineering skill folder, all read-only, and prints a short block naming the
// project brief and the manifest instead of pasting SKILL.md. Without the fifth argument, or for a release without
// "load": "skill", the earlier projection below is unchanged.
//
// The persona release names the handbook it loads (manifest.json "handbook": version and manifest hash). This checks
// the image's root-owned handbook against that pin and every file against the handbook's own manifest, lays the
// release out under <workspace>/handbook/ (mc-chat's file tools are workspace-only, so the files MC is told to read must
// be there), and prints the block start.sh appends to the workspace AGENTS.md. The block is the one native mc-chat
// loads (operators/dev-mc-handbook-0-1-1-20261030): a header naming the pinned paths, the manifest and its hash, then
// the engineering SKILL.md verbatim. PROJECT.md, ADOPTION.md and the references are read at their paths, which keeps
// the projection inside the persona budget. Exits non-zero, printing nothing, on any refusal.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

const MAX_FILE = 32768;
const SKILL = 'skills/minimoi-engineering/SKILL.md';
const REFERENCES = ['board-and-services.md', 'build-and-deploy.md', 'regression.md'];
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
function refuse(why) { console.error(`handbook: ${why}`); process.exit(1); }

function boundedRead(file) {
  const stat = fs.lstatSync(file, {throwIfNoEntry: false});
  if (!stat || !stat.isFile() || stat.size > MAX_FILE) refuse(`${file} is not a regular file of at most ${MAX_FILE} bytes`);
  return fs.readFileSync(file);
}

const [src, workspace, version, pin, personaDir] = process.argv.slice(2);
if (![6, 7].includes(process.argv.length) || !/^[0-9]+\.[0-9]+\.[0-9]+$/.test(version) || !/^[0-9a-f]{64}$/.test(pin))
  refuse('usage: handbook.mjs <image handbook dir> <workspace> <version> <manifest sha256> [<persona release dir>]');

const manifestBytes = boundedRead(path.join(src, 'manifest.json'));
if (sha(manifestBytes) !== pin) refuse(`manifest.json is ${sha(manifestBytes).slice(0, 12)}, the persona pins ${pin.slice(0, 12)}`);
const manifest = JSON.parse(manifestBytes);
const release = `v${version}`;
const names = Object.keys(manifest.files || {});
if (manifest.format !== 'minimoi-mc-handbook/1' || manifest.candidate_version !== version ||
    manifest.release_path !== release || !names.length ||
    names.some(n => !n.startsWith(`${release}/`) || n.split('/').some(part => !part || part === '.' || part === '..')) ||
    !['ADOPTION.md', 'PROJECT.md', SKILL, ...REFERENCES.map(r => `skills/minimoi-engineering/references/${r}`)]
      .every(n => names.includes(`${release}/${n}`)))
  refuse('manifest.json is not a minimoi-mc-handbook/1 manifest for this version');
const files = names.map(name => {
  const bytes = boundedRead(path.join(src, name));
  if (sha(bytes) !== manifest.files[name]) refuse(`${name} differs from the manifest`);
  return [name, bytes];
});

// Lay out: replace <workspace>/handbook wholesale with the checked bytes. A symlink there is removed, never followed.
// MC can write its workspace, so a folder it made unwritable (chmod) is given back owner access first: otherwise the
// removal fails and the container could not start until an operator stepped in.
function removable(p) {
  const st = fs.lstatSync(p);
  if (!st.isDirectory()) return;
  fs.chmodSync(p, 0o700);
  for (const name of fs.readdirSync(p)) removable(path.join(p, name));
}
function replace(dir) {
  if (fs.lstatSync(dir, {throwIfNoEntry: false})) {
    removable(dir);
    fs.rmSync(dir, {recursive: true, force: true});
  }
}
function writeAll(dir, entries) {
  for (const [name, bytes] of entries) {
    const out = path.join(dir, name);
    fs.mkdirSync(path.dirname(out), {recursive: true, mode: 0o755});
    fs.writeFileSync(out, bytes, {mode: 0o444, flag: 'wx'});
  }
}
const root = path.join(path.resolve(workspace), 'handbook');
replace(root);
writeAll(root, [['manifest.json', manifestBytes], ...files]);

const pinned = path.join(root, release);
const begin = `BEGIN_MINIMOI_MC_HANDBOOK ${version}`, end = `END_MINIMOI_MC_HANDBOOK ${version}`;
const skill = files.find(([name]) => name === `${release}/${SKILL}`)[1].toString('utf8');
if (skill.includes('BEGIN_MINIMOI_MC_HANDBOOK') || skill.includes('END_MINIMOI_MC_HANDBOOK')) refuse('SKILL.md holds a block marker');

// Skill mode: the persona release's skills plus the handbook's engineering skill, under <workspace>/skills.
const persona = personaDir ? JSON.parse(boundedRead(path.join(personaDir, 'manifest.json'))) : null;
if (persona && persona.handbook?.load === 'skill') {
  const SKILL_NAME = /^[a-z0-9][a-z0-9-]{0,62}$/;
  const own = Object.keys(persona.files || {}).filter(n => n.startsWith('skills/'));
  const skillFiles = own.map(name => {
    const parts = name.split('/');
    if (parts.length !== 3 || parts[2] !== 'SKILL.md' || !SKILL_NAME.test(parts[1])) refuse(`${name} is not skills/<name>/SKILL.md`);
    const bytes = boundedRead(path.join(personaDir, name));
    if (sha(bytes) !== persona.files[name]) refuse(`persona ${name} differs from its manifest`);
    const head = bytes.toString('utf8').match(/^---\nname: ([a-z0-9-]+)\ndescription: ([^\n]{1,300})\n---\n/);
    if (!head || head[1] !== parts[1]) refuse(`${name} has no name and description frontmatter for ${parts[1]}`);
    return [name.slice('skills/'.length), bytes];
  });
  const engineering = `${release}/skills/minimoi-engineering/`;
  if (own.some(n => n.startsWith('skills/minimoi-engineering/'))) refuse('the persona may not ship the handbook\'s own skill');
  const handbookSkill = files.filter(([name]) => name.startsWith(engineering))
    .map(([name, bytes]) => [`minimoi-engineering/${name.slice(engineering.length)}`, bytes]);
  const skillsRoot = path.join(path.resolve(workspace), 'skills');
  replace(skillsRoot);
  writeAll(skillsRoot, [...skillFiles, ...handbookSkill]);
  process.stdout.write(`${begin}\n` +
    `Handbook ${version} (${manifest.status}), every file listed with its SHA-256 in ${path.join(root, 'manifest.json')}\n` +
    `(SHA-256 ${pin}). Loading is not behaviour verification or owner acceptance.\n` +
    `Project brief, read before engineering or operations work: ${pinned}/PROJECT.md (adoption notes: ADOPTION.md beside it).\n` +
    `Engineering procedures are the minimoi-engineering skill: ${path.join(skillsRoot, 'minimoi-engineering/SKILL.md')}\n` +
    `${end}\n`);
  process.exit(0);
}

process.stdout.write(`${begin}\n` +
  `Pinned handbook: ${pinned}/\n` +
  `Manifest: ${path.join(root, 'manifest.json')}\n` +
  `Manifest SHA-256: ${pin}\n` +
  `Source status remains ${manifest.status}; loading is not behavior verification or owner acceptance.\n` +
  'Every file under the pinned handbook is listed in the manifest with its SHA-256, so you can check them yourself.\n' +
  'The engineering SKILL.md follows verbatim. PROJECT.md, the project brief, is at the first path below: read it\n' +
  'before engineering or operations work. Resolve SKILL.md\'s relative references under\n' +
  `${pinned}/skills/minimoi-engineering/; read ADOPTION.md under the pinned handbook path.\n` +
  [`${pinned}/PROJECT.md`, ...REFERENCES.map(r => `${pinned}/skills/minimoi-engineering/references/${r}`)].join('\n') +
  '\n\n' + skill + `\n${end}\n`);
