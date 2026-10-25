// Board text convention unicode_scalar_lf_v1; shared fixture tests cover Python parity.
export const LIMIT = 280;
const whitespace = new Set(Array.from("\t\n\v\f\r\x1c\x1d\x1e\x1f \x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"));
export function inspectText(value) {
  if (typeof value !== 'string') return { valid: false, count: 0, text: '', code: 'invalid_text' };
  const normalized = value.replace(/\r\n?/g, '\n');
  const chars = Array.from(normalized);
  if (chars.some(c => c.codePointAt(0) >= 0xd800 && c.codePointAt(0) <= 0xdfff))
    return { valid: false, count: chars.length, text: normalized, code: 'invalid_text' };
  let start = 0, end = chars.length;
  while (start < end && whitespace.has(chars[start])) start++;
  while (end > start && whitespace.has(chars[end - 1])) end--;
  const text = chars.slice(start, end).join('');
  const code = chars.length > LIMIT ? 'text_too_long' : !text ? 'text_empty' : null;
  return { valid: code === null, count: chars.length, text, normalized, code };
}

let serial = 0;
export function attachCounter(input) {
  input.removeAttribute('maxlength'); // retain the whole draft, including an over-limit paste
  const counter = document.createElement('small');
  counter.id = `board-text-count-${++serial}`;
  counter.className = 'board-text-count';
  counter.setAttribute('aria-live', 'polite');
  input.setAttribute('aria-describedby', [input.getAttribute('aria-describedby'), counter.id].filter(Boolean).join(' '));
  const update = () => {
    const state = inspectText(input.value);
    const invalid = !state.valid && state.code !== 'text_empty';
    counter.textContent = `${state.count} / ${LIMIT}${invalid ? ' · Shorten before saving; your full draft is kept.' : ''}`;
    input.setAttribute('aria-invalid', invalid ? 'true' : 'false');
  };
  input.addEventListener('input', update);
  update();
  return counter;
}
