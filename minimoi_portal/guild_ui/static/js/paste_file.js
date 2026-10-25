// A long paste becomes a text file (Guild MC composer). Over the message limit, pasted text is not refused and not clipped:
// it goes into the tray as `pasted-text-<time>.txt` and travels with the message like any attached document, which Master
// Craftsman reads as text. No page access here, so the decision is testable on its own (tests/guild_paste_file.mjs).
const codePoints = (s) => { let n = 0; for (const _c of s) n += 1; return n; };   // eslint-disable-line no-unused-vars

// Returns null when the paste should go into the box as usual; otherwise { file, chars, kept, note }.
//   text      what was pasted (plain text)
//   noteMax   the message limit in characters (the page's note_max)
//   docCap    the document upload limit in bytes; a paste larger than this stays in the box, where the length line explains
//   docChars  how many characters the server keeps per document (the page's attach.doc_chars)
//   off       Private is on: nothing can be attached, so the box (with its own, longer limit) takes the paste
export function longPasteFile(text, { noteMax, docCap, docChars, off = false, now = new Date() } = {}) {
  if (off || !text || !noteMax) return null;
  const chars = codePoints(text);
  if (chars <= noteMax) return null;
  const bytes = new TextEncoder().encode(text).length;
  if (docCap && bytes > docCap) return null;
  const stamp = now.toISOString().replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z');
  const file = new File([text], `pasted-text-${stamp}.txt`, { type: 'text/plain' });
  const kept = docChars ? Math.min(chars, docChars) : chars;
  const n = chars.toLocaleString('en-US');
  const note = kept < chars
    ? `Your ${n}-character paste was attached as a text file. Only the first ${kept.toLocaleString('en-US')} characters are kept and read.`
    : `Your ${n}-character paste was attached as a text file; Master Craftsman reads it with your message.`;
  return { file, chars, kept, note };
}
