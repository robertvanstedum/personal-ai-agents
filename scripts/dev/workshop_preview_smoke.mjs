// Browser smoke for the topic Workshop over the shared Workshop record (local preview only).
//   PLAYWRIGHT_MODULE=/abs/path/to/playwright node scripts/dev/workshop_preview_smoke.mjs <base-url> <seeded.json> <out-dir>
// For each state it loads the page at the five baseline widths, records console errors, horizontal overflow, small touch targets and
// whether the expected honest wording is on the page, saves a screenshot, and drives the link, unlink and Record-open interactions.
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const pw = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const [base, seededPath, out] = process.argv.slice(2);
const seeded = JSON.parse(fs.readFileSync(seededPath, 'utf8'));
fs.mkdirSync(out, { recursive: true });
const WIDTHS = [320, 390, 768, 900, 1440];
const STATES = {
  garden: { topic: seeded.garden, expect: ['Waiting for you (2)', 'Claimed, not confirmed.', 'worker report', 'not connected yet'], open: true },
  vault: { topic: seeded.vault, expect: ['Waiting for you (1)'], open: true },
  plain: { topic: seeded.plain, expect: ['not linked to a shared record'], open: true },
  damaged: { topic: seeded.damaged, expect: ['is damaged', 'partial or wrong picture', 'No entries are shown'], open: true },
  cutoff: { topic: seeded.cutoff, expect: ['cut off'], open: true },
  absent: { topic: seeded.absent, expect: ['not on this server yet'], open: true },
};
const results = [];
const browser = await pw.chromium.launch({ executablePath: process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' });
async function openRecord(page) {
  const sum = page.locator('details.wt-journal > summary');
  await sum.waitFor({ timeout: 8000 });
  if (!(await page.locator('details.wt-journal[open]').count())) await sum.click();
}
for (const [id, st] of Object.entries(STATES)) {
  for (const width of WIDTHS) {
    const ctx = await browser.newContext({ viewport: { width, height: width <= 390 ? 800 : 900 } });
    const page = await ctx.newPage();
    const errors = [];
    let navCsp = 0;
    // The portal's top navigation bar carries inline styles that the Guild page's strict style CSP blocks; that is in every Guild page
    // already and is counted here, not hidden, but it is not a failure of this screen.
    page.on('console', (m) => { if (m.type() !== 'error') return; if (/Applying inline style violates/.test(m.text())) navCsp += 1; else errors.push(m.text()); });
    page.on('pageerror', (e) => errors.push(String(e)));
    page.on('requestfailed', (r) => errors.push(`failed ${r.url()}`));
    await page.goto(`${base}/guild-next/guild/workshop?topic=${st.topic}&view=cards`, { waitUntil: 'networkidle' });
    await openRecord(page);
    await page.waitForTimeout(300);
    const text = await page.evaluate(() => document.body.innerText);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
    const small = width <= 768 ? await page.evaluate(() => [...document.querySelectorAll('.wt-journal button, .wt-journal summary, .wt-journal a')]
      .filter((e) => { const r = e.getBoundingClientRect(); return r.width > 0 && (r.height < 32); }).map((e) => `${e.tagName}:${(e.textContent || '').trim().slice(0, 30)}:${Math.round(e.getBoundingClientRect().height)}`)) : [];
    const missing = st.expect.filter((t) => !text.includes(t));
    await page.screenshot({ path: path.join(out, `${id}-${width}.png`), fullPage: true });
    results.push({ state: id, width, errors, nav_csp_notices: navCsp, overflow, missing, small_targets: small, pass: !errors.length && !overflow && !missing.length });
    await ctx.close();
  }
}
// interactions on the unlinked topic: link through the dialog, see entries, unlink again
{
  const ctx = await browser.newContext({ viewport: { width: 1000, height: 900 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(`${base}/guild-next/guild/workshop?topic=${seeded.plain}&view=cards`, { waitUntil: 'networkidle' });
  await openRecord(page);
  await page.getByRole('button', { name: 'Link a shared record' }).click();
  await page.getByRole('button', { name: 'Find topics' }).click();
  await page.waitForSelector('dialog[open] button:has-text("garden-build")');
  const listed = await page.locator('dialog[open]').innerText();
  await page.screenshot({ path: path.join(out, 'link-dialog-1000.png') });
  await page.locator('dialog[open] button:has-text("garden-build")').click();
  await page.waitForSelector('[data-wt-waiting]');
  await openRecord(page);
  const linkedText = await page.evaluate(() => document.body.innerText);
  await page.screenshot({ path: path.join(out, 'linked-after-dialog-1000.png'), fullPage: true });
  await page.getByRole('button', { name: 'Unlink the shared record' }).click();
  await page.waitForSelector('[data-wt-record-unlinked]');
  results.push({ state: 'interaction:link-then-unlink', width: 1000, errors, overflow: false, missing: [
    ...['garden-build', 'vault-backup'].filter((t) => !listed.includes(t)).map((t) => `dialog lists ${t}`),
    ...(linkedText.includes('Waiting for you (2)') ? [] : ['linked view shows waiting for you']),
  ], small_targets: [], pass: false });
  const last = results[results.length - 1];
  last.pass = !last.errors.length && !last.missing.length;
  await ctx.close();
}
// failure and recovery (R19): a failed record call is shown as a failure, never as empty or unlinked, never crashes, and Try again recovers
for (const mode of ['http500', 'network']) {
  for (const width of [390, 1440]) {
    const ctx = await browser.newContext({ viewport: { width, height: 900 } });
    const page = await ctx.newPage();
    const errors = [];
    page.on('pageerror', (e) => errors.push(String(e)));
    let failing = true;
    await page.route('**/topics/*/record', (route) => (failing ? (mode === 'http500' ? route.fulfill({ status: 500, contentType: 'application/json', body: '{"error":"x"}' }) : route.abort('failed')) : route.continue()));
    await page.goto(`${base}/guild-next/guild/workshop?topic=${seeded.garden}&view=cards`, { waitUntil: 'networkidle' });
    await openRecord(page);
    const down = await page.evaluate(() => document.body.innerText);
    await page.screenshot({ path: path.join(out, `failed-${mode}-${width}.png`), fullPage: true });
    const missing = [];
    for (const t of ['could not be reached', 'Try again']) if (!down.includes(t)) missing.push(`failed state says "${t}"`);
    for (const t of ['not linked to a shared record', 'Nothing recorded yet', 'Waiting for you', 'garden-build']) if (down.includes(t)) missing.push(`failed state must not show "${t}"`);
    failing = false;
    await page.locator('[data-wt-record-retry]').first().click();
    await page.waitForSelector('[data-wt-waiting]', { timeout: 8000 }).catch(() => missing.push('recovers after Try again'));
    results.push({ state: `interaction:record-${mode}-then-retry`, width, errors, overflow: false, missing, small_targets: [], pass: !errors.length && !missing.length });
    await ctx.close();
  }
}
await browser.close();
fs.writeFileSync(path.join(out, 'results.json'), JSON.stringify({ checked_at: new Date().toISOString(), base, results }, null, 1));
const failed = results.filter((r) => !r.pass);
console.log(`${results.length} checks, ${failed.length} failed`);
for (const f of failed) console.log('FAIL', f.state, f.width, JSON.stringify({ errors: f.errors, overflow: f.overflow, missing: f.missing }));
const smalls = results.filter((r) => r.small_targets.length);
console.log(`${smalls.length} cells list small touch targets in the Record pane`);
process.exit(failed.length ? 1 : 0);
