#!/usr/bin/env node
// web/generate.js — Research Intelligence HTML generator
// Run: node web/generate.js (from project root)
// Outputs: web/index.html
// No npm dependencies — Node.js built-ins only

const fs   = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const LIB  = path.join(ROOT, 'library');
const WEB  = path.join(ROOT, 'web');

// ── Helpers ───────────────────────────────────────────────────────────────────

function readFile(filePath) {
  try { return fs.readFileSync(filePath, 'utf8'); }
  catch { return null; }
}

function esc(str) {
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function isUrl(str) {
  return /^https?:\/\//i.test(str.trim());
}

// Truncate to at most 2 sentences. Splits on . ? ! followed by space or end.
// Never truncates mid-word. Single sentence (or no punctuation) returned as-is.
function twoSentences(text) {
  const re = /[.!?]+(?=\s|$)/g;
  let count = 0, cutAt = text.length;
  let match;
  while ((match = re.exec(text)) !== null) {
    count++;
    if (count === 2) { cutAt = match.index + match[0].length; break; }
  }
  return text.substring(0, cutAt).trim();
}

function parseMarkdownTable(text) {
  if (!text) return { headers: [], rows: [] };
  const lines = text.split('\n').filter(l => l.trim().startsWith('|'));
  if (lines.length < 2) return { headers: [], rows: [] };

  const isSep  = l => /^\|[-| :]+\|$/.test(l.trim());
  const parseRow = l => l.split('|').slice(1, -1).map(c => c.trim());

  const headers = parseRow(lines[0]);
  const rows    = lines.slice(1).filter(l => !isSep(l)).map(parseRow);
  return { headers, rows };
}

// ── Parsers ───────────────────────────────────────────────────────────────────

function parseLibrary() {
  return parseMarkdownTable(readFile(path.join(LIB, 'README.md')));
}

function parseSessionLog() {
  const text = readFile(path.join(LIB, 'session-log.md'));
  if (!text) return { budget: null, warn: null, cumulative: '$0.00', sessions: [] };

  const budgetMatch = text.match(/_Pilot budget: ([^\n_]+)_/);
  const warnMatch   = text.match(/_Warn at: ([^\n_]+)_/);

  const { rows } = parseMarkdownTable(text);
  const sessions  = rows.filter(r => r.some(c => c && c !== '—'));
  const last      = sessions[sessions.length - 1];

  return {
    budget:     budgetMatch ? budgetMatch[1].trim() : null,
    warn:       warnMatch   ? warnMatch[1].trim()   : null,
    cumulative: last        ? last[4]                : '$0.00',
    sessions:   sessions.slice(-10),
  };
}

function parseReadingList() {
  return parseMarkdownTable(readFile(path.join(LIB, 'reading-list.md')));
}

function parseEssays() {
  const dir = path.join(LIB, 'essays');
  if (!fs.existsSync(dir)) return [];

  return fs.readdirSync(dir)
    .filter(f => f.endsWith('.md'))
    .sort().reverse()
    .map(filename => {
      const text = readFile(path.join(dir, filename));
      if (!text) return null;

      // Title: first line, strip leading # characters
      const title = text.split('\n')[0].replace(/^#+\s*/, '').trim() || filename;

      // Summary: second non-empty paragraph (blank-line separated), truncated to 2 sentences
      const paragraphs = text.split(/\n\s*\n/)
        .map(p => p.trim())
        .filter(p => p.length > 0);
      const raw     = paragraphs[1] ? paragraphs[1].replace(/^#+\s*/, '').trim() : '';
      const summary = raw ? twoSentences(raw) : '';

      // Date from filename: YYYY-MM-DD-slug.md
      const dateMatch = filename.match(/^(\d{4}-\d{2}-\d{2})/);
      const date = dateMatch ? dateMatch[1] : '—';

      return { filename, title, date, summary, filepath: `library/essays/${filename}` };
    })
    .filter(Boolean);
}

// ── HTML Generation ───────────────────────────────────────────────────────────

function generateHtml({ library, sessionLog, readingList, essays }) {
  const generated = new Date().toLocaleString('en-US', {
    month: 'short', day: 'numeric', year: 'numeric',
    hour: '2-digit', minute: '2-digit'
  });

  const colIdx   = name => library.headers.indexOf(name);
  const topicIdx = colIdx('Topic');
  const langIdx  = colIdx('Language');
  const typeIdx  = colIdx('Type');
  const linkIdx  = colIdx('Path/URL');

  const unique = idx => idx < 0 ? [] :
    [...new Set(library.rows.map(r => r[idx]).filter(v => v && v !== '—'))].sort();

  const filterOpts = vals =>
    vals.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join('');

  // Detect seed-only library (single placeholder row of all dashes)
  const isPlaceholder = library.rows.length === 1 &&
    library.rows[0].every(c => !c || c === '—');

  const libraryRows = library.rows.length === 0 || isPlaceholder
    ? `<tr><td colspan="${library.headers.length || 7}" class="empty">No entries yet</td></tr>`
    : library.rows.map(row =>
        `<tr>${row.map((cell, i) => {
          if (i === linkIdx && cell && cell !== '—') {
            return isUrl(cell)
              ? `<td><a href="${esc(cell)}" target="_blank" rel="noopener">link ↗</a></td>`
              : `<td><code>${esc(cell)}</code></td>`;
          }
          return `<td>${esc(cell)}</td>`;
        }).join('')}</tr>`
      ).join('\n');

  const essaysHtml = essays.length === 0
    ? `<p class="empty">No essays yet.</p>`
    : essays.map(e => `
    <div class="essay-entry">
      <div class="essay-title">${esc(e.title)}</div>
      <div class="essay-meta">${esc(e.date)} · <code>${esc(e.filepath)}</code></div>
      ${e.summary ? `<div class="essay-summary">${esc(e.summary)}</div>` : ''}
    </div>`).join('\n');

  const readingRows = readingList.rows.length === 0
    ? `<tr><td colspan="${readingList.headers.length || 4}" class="empty">No entries yet</td></tr>`
    : readingList.rows.map(row =>
        `<tr>${row.map(cell => `<td>${esc(cell)}</td>`).join('')}</tr>`
      ).join('\n');

  const sessionRows = sessionLog.sessions.length === 0
    ? `<tr><td colspan="6" class="empty">No sessions yet</td></tr>`
    : sessionLog.sessions.map(row =>
        `<tr>${row.map((cell, i) =>
          i === 4
            ? `<td><strong>${esc(cell)}</strong></td>`
            : `<td>${esc(cell)}</td>`
        ).join('')}</tr>`
      ).join('\n');

  const budgetLine = sessionLog.budget
    ? `Budget: ${esc(sessionLog.budget)} · Cumulative: <strong>${esc(sessionLog.cumulative)}</strong>`
    : `Cumulative: <strong>${esc(sessionLog.cumulative)}</strong>`;

  const searchCols = library.headers.map((_, i) => i).filter(i => i !== linkIdx);

  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Research Intelligence Library</title>
<style>
  :root {
    --bg:     #f5f0e8;
    --text:   #2c1a0e;
    --accent: #8b6914;
    --muted:  #6b5a3e;
    --border: #d4c5a9;
    --alt:    #ede8de;
  }
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg); color: var(--text);
    font-family: Georgia, 'Times New Roman', serif;
    font-size: 15px; line-height: 1.6;
    padding: 2rem 1.5rem; max-width: 1100px; margin: 0 auto;
  }
  h1  { font-size: 1.8rem; margin-bottom: 0.2rem; }
  h2  { font-size: 1.05rem; color: var(--accent); margin: 2.5rem 0 0.9rem;
        letter-spacing: 0.07em; text-transform: uppercase; }
  .meta   { font-size: 0.82rem; color: var(--muted); margin-bottom: 0.3rem; }
  .budget { font-family: 'Courier New', monospace; font-size: 0.82rem;
            color: var(--muted); margin-bottom: 2rem; }
  .note   { font-size: 0.82rem; color: var(--muted); font-style: italic; margin-bottom: 0.75rem; }
  .filters { display: flex; gap: 0.6rem; flex-wrap: wrap; margin-bottom: 0.9rem; align-items: center; }
  .filters input, .filters select {
    background: var(--bg); border: 1px solid var(--border); border-radius: 3px;
    color: var(--text); font-family: inherit; font-size: 0.83rem; padding: 0.28rem 0.55rem;
  }
  .filters input { width: 210px; }
  table   { width: 100%; border-collapse: collapse; font-size: 0.87rem; }
  th      { background: var(--text); color: var(--bg); font-family: Georgia, serif;
            font-size: 0.75rem; font-weight: normal; letter-spacing: 0.06em;
            padding: 0.4rem 0.6rem; text-align: left; text-transform: uppercase; }
  td      { border-bottom: 1px solid var(--border); padding: 0.38rem 0.6rem; vertical-align: top; }
  tr:nth-child(even) td { background: var(--alt); }
  tr.hidden { display: none; }
  code    { font-family: 'Courier New', monospace; font-size: 0.8rem; color: var(--muted); }
  a       { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  .empty  { color: var(--muted); font-style: italic; padding: 0.9rem 0.6rem; }
  .essay-entry        { border-bottom: 1px solid var(--border); padding: 0.85rem 0; }
  .essay-entry:last-child { border-bottom: none; }
  .essay-title        { font-weight: bold; }
  .essay-meta         { font-size: 0.78rem; color: var(--muted); font-family: monospace; margin: 0.15rem 0 0.35rem; }
  .essay-summary      { font-size: 0.87rem; color: var(--muted); }
</style>
</head>
<body>

<h1>Research Intelligence Library</h1>
<p class="meta">Generated: ${esc(generated)}</p>
<p class="budget">${budgetLine}</p>

<h2>Library</h2>
<div class="filters">
  <input type="text" id="srch" placeholder="Search title, summary, source…" oninput="applyFilters()">
  <select id="fTopic" onchange="applyFilters()">
    <option value="">Topic: All</option>${filterOpts(unique(topicIdx))}
  </select>
  <select id="fLang" onchange="applyFilters()">
    <option value="">Language: All</option>${filterOpts(unique(langIdx))}
  </select>
  <select id="fType" onchange="applyFilters()">
    <option value="">Type: All</option>${filterOpts(unique(typeIdx))}
  </select>
</div>
<table>
  <thead><tr>${library.headers.map(col => `<th>${esc(col)}</th>`).join('')}</tr></thead>
  <tbody id="libBody">${libraryRows}</tbody>
</table>

<h2>Essays</h2>
<p class="note">Agent-written syntheses. Stored in library/essays/.</p>
${essaysHtml}

<h2>Reading List</h2>
<p class="note">Books and long-form flagged for Robert.</p>
<table>
  <thead><tr>${readingList.headers.map(col => `<th>${esc(col)}</th>`).join('')}</tr></thead>
  <tbody>${readingRows}</tbody>
</table>

<h2>Recent Sessions</h2>
<table>
  <thead><tr><th>Date</th><th>Session</th><th>Duration</th><th>Cost</th><th>Cumulative</th><th>Notes</th></tr></thead>
  <tbody>${sessionRows}</tbody>
</table>

<script>
const TOPIC_IDX   = ${topicIdx};
const LANG_IDX    = ${langIdx};
const TYPE_IDX    = ${typeIdx};
const SEARCH_COLS = [${searchCols.join(', ')}];

function applyFilters() {
  const q     = document.getElementById('srch').value.toLowerCase();
  const topic = document.getElementById('fTopic').value.toLowerCase();
  const lang  = document.getElementById('fLang').value.toLowerCase();
  const type  = document.getElementById('fType').value.toLowerCase();

  document.querySelectorAll('#libBody tr').forEach(row => {
    const cells = [...row.querySelectorAll('td')].map(td => td.textContent.toLowerCase());
    const hit = (!q     || SEARCH_COLS.some(i => cells[i] && cells[i].includes(q)))
             && (!topic || cells[TOPIC_IDX] === topic)
             && (!lang  || cells[LANG_IDX]  === lang)
             && (!type  || cells[TYPE_IDX]  === type);
    row.classList.toggle('hidden', !hit);
  });
}
</script>

</body>
</html>`;
}

// ── Main ──────────────────────────────────────────────────────────────────────

function main() {
  const library     = parseLibrary();
  const sessionLog  = parseSessionLog();
  const readingList = parseReadingList();
  const essays      = parseEssays();

  const html = generateHtml({ library, sessionLog, readingList, essays });

  fs.mkdirSync(WEB, { recursive: true });
  const out = path.join(WEB, 'index.html');
  fs.writeFileSync(out, html, 'utf8');

  console.log(`✓ Generated ${out}`);
  console.log(`  Library rows : ${library.rows.length}`);
  console.log(`  Essays       : ${essays.length}`);
  console.log(`  Sessions     : ${sessionLog.sessions.length}`);
}

main();
