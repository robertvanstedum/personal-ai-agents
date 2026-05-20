# Build Note: Steps 6 + 7 — Lesen Feed and Schreiben Three-Mode UI
**Date:** 2026-05-20
**Branch:** feat/german-html-interface
**Commit:** 13581d1
**Design brief:** `_working/design_brief_lesen_schreiben.md`

---

## What Steps 6 + 7 are

**Step 6 — Lesen:** Daily reading room. Austrian/Vienna RSS articles, word-capture popover
that connects Lesen → Bibliothek → Üben. Article feedback (👍/👎/🔖) stored for future
curation tuning.

**Step 7 — Schreiben:** Three writing modes sharing one pane. Im Kontext (writing in
response to a reading), Wörter (manual word capture), Tagebuch (freeform diary with
Vienna prompts). All modes share a session history footer.

---

## Files modified

| File | Changes |
|---|---|
| `german_domain.py` | +234 lines — 13 new public functions (see below) |
| `html_server.py` | +104 lines — updated /lesen, /schreiben routes + 6 new API routes |
| `templates/german_lesen.html` | Full implementation (was placeholder) |
| `templates/german_schreiben.html` | Full implementation (was placeholder) |
| `static/german.css` | +534 lines — article cards, popover, mode toggle, correction display |

---

## New functions in `german_domain.py`

| Function | Purpose |
|---|---|
| `_load_lesen_articles()` | Load lesen_articles.json (creates if absent) |
| `_save_lesen_articles(data)` | Persist article pool |
| `_strip_html(raw)` | BeautifulSoup HTML stripping for RSS summaries |
| `get_lesen_pool()` | Return active + pinned articles, pinned first |
| `fetch_lesen_articles()` | feedparser fetch from 5 RSS sources, deduplicate by URL |
| `refresh_lesen_feed()` | Fetch → append → save; return `{added, pool_size}` |
| `lesen_action(article_id, action)` | Update article status; append to lesen_feedback.json |
| `translate_phrase(phrase)` | Phrasebook cache check → LLM fallback; returns `(str, bool)` |
| `save_lesen_phrase(german, english, context, title)` | Append to phrasebook with scene=lesen |
| `get_tagebuch_prompts()` | Return 8 Vienna-themed diary prompts (module constant) |
| `correct_writing(text, context)` | LLM correction; returns `{corrected, notes}`; JSON parse fallback |
| `save_writing_entry(...)` | Append to writing_sessions.json; trim to last 50 |

---

## RSS sources (verified working at build time)

| Source | Feed URL |
|---|---|
| ORF Wien | `https://rss.orf.at/wien.xml` |
| Vienna.at | `https://www.vienna.at/rss` |
| Standard Kultur | `https://www.derstandard.at/rss/kultur` |
| ORF Sport | `https://rss.orf.at/sport.xml` |
| Falter | `https://www.falter.at/rss` |

Original plan URLs (ORF Wien, Heute, Krone Wien, Wiener Zeitung, Kicker) all returned 404
or 403. Above URLs verified live via feedparser at build time.

---

## API routes added

| Route | Method | Handler |
|---|---|---|
| `/api/lesen-refresh` | POST | `refresh_lesen_feed()` |
| `/api/lesen-action` | POST | `lesen_action(article_id, action)` |
| `/api/translate` | POST | `translate_phrase(phrase)` |
| `/api/save-phrase` | POST | `save_lesen_phrase(...)` |
| `/api/write-correct` | POST | `correct_writing(text, context)` |
| `/api/write-save` | POST | `save_writing_entry(...)` |

---

## Data files created on first use

| File | Contents |
|---|---|
| `config/lesen_articles.json` | Article pool — `{articles: [...], last_fetched: ...}` |
| `config/lesen_feedback.json` | Action log — `[{article_id, action, timestamp}, ...]` |
| `config/writing_sessions.json` | Writing history — `{entries: [...]}`, capped at 50 |

---

## Design decisions

**feedparser over requests+BS4 for RSS:** feedparser already in requirements.txt and used
by curator_rss_v2.py. Proven, consistent entry/summary normalization. BS4 still used
for HTML stripping of summary content.

**Article pool not day-bounded:** Pool persists across sessions. Manual refresh trigger.
Active count < 2 threshold not yet wired to auto-prompt — user manually hits refresh.

**translate_phrase cache:** Checks phrasebook.json first (case-insensitive German match)
before making an LLM call. Saves cost on repeated lookups of previously saved words.

**Wörter mode saves to same `/api/save-phrase` endpoint as Lesen popover:** Both routes
write to the same phrasebook.json. No duplicate endpoint needed.

**Tagebuch prompts are a module constant** (`_TAGEBUCH_PROMPTS` in german_domain.py),
not in a config file. 8 Vienna-themed prompts. To add prompts: edit the list in german_domain.py.

**correct_writing JSON parse fallback:** LLM sometimes returns prose instead of JSON.
Fallback returns `{"corrected": original_text, "notes": ["(Correction unavailable)"]}`.

---

## Verified

- `/lesen` — empty state with "Feed laden" renders correctly ✓
- `/schreiben` — three mode buttons, Im Kontext pane visible by default ✓
- Both routes return HTTP 200 with running server ✓
- `python3 -c "import german_domain; import html_server"` passes cleanly ✓
- RSS fetch, word capture, article actions, Schreiben modes: pending Robert UAT

---

## Known gaps / next steps

- UAT not yet run by Robert
- Lesen source quality: Falter may be paywalled for full articles (linked out); summary
  visible, full read requires subscription
- Polish pass (from Step 5 backlog) still pending: logo wordmark, nav active color,
  subtitle breathing room, vignette
- Steps 8 onward not yet started
