/**
 * voice-reply-mode.js — the standard voice reply control, shared by every
 * realtime voice page (Confer, Gespräche, Conversas).
 *
 *   "speak"  Speak and write (the default): the reply is spoken and its text shown.
 *   "write"  Write only: the reply's audio is muted on this device; its text
 *            still shows live. The provider still produces audio (both
 *            adapters mute playback; text-only output is not requested).
 *
 * The choice is remembered per page on this device (localStorage, which can be
 * missing or throw: the default then applies). The page supplies the <select>
 * with its own localized "speak"/"write" options.
 *
 *   import { mountReplyModeToggle } from ".../voice-reply-mode.js?v=...";
 *   let replyMode = mountReplyModeToggle(select, {
 *     scope: "german",
 *     onChange(mode) { replyMode = mode; controller?.setReplyMode(mode); },
 *   });
 */
export const REPLY_MODES = ["speak", "write"];
const KEY_PREFIX = "minimoi.voice.reply_mode.";

export function normalizeReplyMode(value) {
  return value === "write" ? "write" : "speak";
}

export function loadReplyMode(scope) {
  try {
    return normalizeReplyMode(window.localStorage.getItem(KEY_PREFIX + scope));
  } catch (_) {
    return "speak";
  }
}

export function saveReplyMode(scope, mode) {
  try {
    window.localStorage.setItem(KEY_PREFIX + scope, normalizeReplyMode(mode));
  } catch (_) {
    // Not remembered on this device; the choice still applies to this page.
  }
}

export function mountReplyModeToggle(select, { scope, onChange } = {}) {
  const mode = loadReplyMode(scope);
  if (!select) return mode;
  select.value = mode;
  select.addEventListener("change", () => {
    const next = normalizeReplyMode(select.value);
    saveReplyMode(scope, next);
    if (onChange) onChange(next);
  });
  return mode;
}
