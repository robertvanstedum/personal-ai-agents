// Capture of vendor warnings and refill receipts pasted into the conversation
// (SPEC rev 3.1). Pure helpers: classify, parse, and strip payment details.
// Nothing here guesses: a missing amount or time is reported as missing.
const DAYS = ['sat', 'sun', 'mon', 'tue', 'wed', 'thu', 'fri'];
const BRANDS = '(?:visa|master\\s?card|amex|american\\s+express|discover|diners|jcb|union\\s?pay)';
const PAYMENT = [
  new RegExp(`${BRANDS}[^\\n\\d]{0,20}\\d{2,6}`, 'gi'),        // "Mastercard - 1234"
  /(?:ending|ends)\s+(?:in|with)\s*\d{2,6}/gi,                   // "ending in 1234"
  /[•*xX]{2,}[\s-]*\d{2,6}/g,                                     // "•••• 1234"
  /\b(?:\d[ -]?){13,19}\b/g,                                      // full card numbers
  /[\w.+-]+@[\w-]+(?:\.[\w-]+)+/g,                                // billing email
  new RegExp(BRANDS, 'gi'),                                       // a bare brand name
];
export const REDACTED = '[payment detail removed]';

export function stripPayment(text) {
  let out = String(text || '');
  for (const re of PAYMENT) out = out.replace(re, REDACTED);
  return out;
}

// Ordinary messages: remove card-like details only (brand + digits, masked or full numbers).
export function stripCard(text) {
  let out = String(text || '');
  for (const re of PAYMENT.slice(0, 4)) out = out.replace(re, REDACTED);
  return out;
}

// Payment-method or account detail that must never be kept as typed. The same
// signals exempt a plain question from being read as a receipt.
const PAYMENT_DETAIL = /(?:\b(?:routing|account|ach|iban|swift|card|ending)\b|\b\d{5,}\b)/i;

export function hasPaymentDetail(text) {
  return PAYMENT_DETAIL.test(String(text || ''));
}

export function looksLikeReceipt(text) {
  const t = String(text || '');
  if (/^\s*(?:what|how|why|can|could|where|when|which|is|are|do|does)\b.*\?\s*$/i.test(t)
      && !/\$\s*\d/.test(t) && !hasPaymentDetail(t)) return false;
  return /\b(receipt|paid|payment|top[- ]?up|invoice|credits?\s+(?:purchased|added)|amount\s+paid)\b/i.test(t);
}

export function looksLikeWarning(text) {
  return /(usage limit|limit (?:has been )?reached|reached your (?:usage )?limit|approaching|rate limit|resets?\s+(?:at|on)|\d{1,3}\s*%\s*(?:left|remaining))/i.test(text);
}

// "Mon 6:00", "Monday 6:00 pm" → minutes from Sat 00:00 of the scenario week; null if absent.
export function parseDayTime(text) {
  const m = /\b(sat|sun|mon|tue|wed|thu|fri)[a-z]*\.?,?\s+(\d{1,2}):(\d{2})\s*(am|pm)?\b/i.exec(text || '');
  if (!m) return null;
  let h = Number(m[2]);
  const min = Number(m[3]);
  if (h > 23 || min > 59) return null;
  if (m[4]) { const pm = m[4].toLowerCase() === 'pm'; if (pm && h < 12) h += 12; if (!pm && h === 12) h = 0; }
  return DAYS.indexOf(m[1].toLowerCase()) * 1440 + h * 60 + min;
}

export function label(minutes) {
  const d = ['Sat', 'Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri'][Math.floor(minutes / 1440) % 7];
  const m = ((minutes % 1440) + 1440) % 1440;
  return `${d} ${Math.floor(m / 60)}:${String(m % 60).padStart(2, '0')}`;
}

export function parseAmount(text) {
  const m = /(?:\$|usd\s?)\s?(\d{1,6}(?:[.,]\d{2})?)\b|\b(\d{1,6}[.,]\d{2})\s?(?:usd|\$)/i.exec(text || '');
  if (!m) return null;
  const v = Number((m[1] || m[2]).replace(',', '.'));
  return v > 0 ? Math.round(v * 100) / 100 : null;
}

// Receipt → {source, vendor, amount, paid_at}; vendor, amount and time only.
export function parseReceipt(text, balances) {
  const t = String(text || '');
  let source = null;
  let vendor = null;
  if (/\bx\.?ai\b|grok/i.test(t)) { vendor = 'xAI'; source = 'xai_api'; }
  else if (/anthropic|claude/i.test(t)) { vendor = 'Anthropic'; source = 'anthropic_api'; }
  else if (/openai/i.test(t)) { vendor = 'OpenAI'; }
  if (source && !balances.some((b) => b.id === source)) source = null;
  return { source, vendor, amount: parseAmount(t), paid_at: parseDayTime(t) };
}

// Keep only the fields the prototype needs. The original receipt is never a
// conversation turn or browser-storage value; pattern redaction alone cannot
// safely recognize every account, routing, address or transaction detail.
export function receiptSummary(receipt) {
  const vendor = receipt.vendor || 'vendor not recognised';
  const amount = receipt.amount == null ? 'amount not read' : `$${receipt.amount.toFixed(2)}`;
  const paid = receipt.paid_at == null ? 'time not read' : label(receipt.paid_at);
  return `Receipt · ${vendor} · ${amount} · ${paid} · original receipt text not stored`;
}

// Vendor warning → {tool, kind, stated_reset_at, stated_remaining_pct}.
export function parseWarning(text, tools) {
  const t = String(text || '');
  let tool = null;
  if (/codex|chatgpt|openai/i.test(t)) tool = 'Codex';
  else if (/claude/i.test(t)) tool = 'Claude Code';
  else if (/grok/i.test(t)) tool = 'Grok CLI';
  if (tool && !tools.includes(tool)) tool = null;
  const kind = /(limit (?:has been )?reached|reached your (?:usage )?limit|out of (?:usage|credits)|hit your (?:usage )?limit)/i.test(t)
    ? 'limit_reached' : 'warning';
  const resetM = /resets?\s*(?:at|on)?\s*((?:sat|sun|mon|tue|wed|thu|fri)[a-z]*\.?,?\s+\d{1,2}:\d{2}\s*(?:am|pm)?)/i.exec(t);
  const left = /(\d{1,3})\s*%\s*(?:left|remaining)/i.exec(t);
  return { tool, kind, stated_reset_at: resetM ? parseDayTime(resetM[1]) : null,
    stated_remaining_pct: left ? Math.min(100, Number(left[1])) : null };
}

// Mirror filed evidence into one small cookie so the server can render with it.
export function writeEvidenceCookie(cfg, overlay) {
  const payload = JSON.stringify({ v: (overlay.vendor || []).slice(-6), r: (overlay.refills || []).slice(-6) });
  const bytes = new TextEncoder().encode(payload);
  let bin = '';
  for (const b of bytes) bin += String.fromCharCode(b);
  const b64 = btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  document.cookie = `${cfg.cookie}=${b64}; path=${cfg.cookie_path}; SameSite=Strict; max-age=2592000`;
}

export function clearEvidenceCookie(cfg) {
  if (cfg) document.cookie = `${cfg.cookie}=; path=${cfg.cookie_path}; SameSite=Strict; max-age=0`;
}
