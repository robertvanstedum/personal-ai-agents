# Feature Spec — Lesen Writing Drill (Contextual Correction + Retype)
*mini-moi · Mein Deutsch · German domain*
*Authored: 2026-06-08 — Claude.ai · Register as GitHub issue, build later*

---

## What it is

When a user writes a note (NOTIZEN) on the Lesen reading page, they can request a correction
or translation — and then immediately retype the corrected version for muscle memory.

This turns the note-taking moment into a live drill session rooted in real article context.
The result is stored for future personalized tuning.

---

## The two directions

**Direction A — German in, correction + translation out**
User writes in German (imperfectly). System returns:
1. Corrected German (grammar, spelling, word choice)
2. English translation of the corrected version

User sees the corrected German, then retypes it in a clean field.

**Direction B — English in, German out**
User writes in English (their native language). System returns:
1. German translation of what they wrote
2. The original English (already known)

User sees the target German, then retypes it in a clean field.

Both directions are equally valid. The UI detects which mode based on the language of the
input, or offers a simple toggle. Direction B is especially useful for new language learners
who want to express a thought in the target language but can't yet formulate it directly.

---

## User flow (both directions)

```
1. User reads article
2. User types a note in NOTIZEN (German or English, imperfect)
3. User clicks [Korrigieren / Übersetzen] or submits the note
4. System calls LLM (same fallback chain: Grok → Haiku → Ollama)
5. Page shows the result below the original:
   ┌────────────────────────────────────────────────────────┐
   │ Deine Notiz:                                           │
   │ Ich habe nicht guwusst daß der Philippinen Erdbeben    │
   │ haben                                                  │
   ├────────────────────────────────────────────────────────┤
   │ Korrigiert:                                            │
   │ Ich wusste nicht, dass die Philippinen Erdbeben haben. │
   ├────────────────────────────────────────────────────────┤
   │ Auf Englisch:                                          │
   │ I didn't know that the Philippines has earthquakes.    │
   └────────────────────────────────────────────────────────┘
6. A clean empty text field appears below labeled [Tippe es nach]:
   ┌────────────────────────────────────────────────────────┐
   │ Tippe die korrigierte Version:                         │
   │ [                                                   ]  │
   └────────────────────────────────────────────────────────┘
7. User types it (muscle memory). Field turns green on match (optional).
8. On submit: original + corrected + retyped stored to phrasebook/session record.
```

---

## What gets stored

```json
{
  "article_id": "...",
  "user": "robert",
  "timestamp": "2026-06-08T...",
  "direction": "de_in",
  "original": "Ich habe nicht guwusst daß der Philippinen Erdbeben haben",
  "corrected": "Ich wusste nicht, dass die Philippinen Erdbeben haben.",
  "translation": "I didn't know that the Philippines has earthquakes.",
  "retyped": "Ich wusste nicht, dass die Philippinen Erdbeben haben.",
  "retyped_correct": true,
  "model_used": "grok-4-1-fast",
  "context_article_url": "...",
  "context_article_title": "Mehrere Tote bei schwerem Erdbeben auf den Philippinen"
}
```

Storage path: `domains/german/data/lesen_drills/YYYY-MM-DD.json` (one file per day)
OR extend the existing phrasebook (`phrasebook.json`) with a `lesen_context` flag.

These records feed:
- Future Anki card candidates (errors = strong card targets)
- Personalized Wörter sidebar tuning (what does this user struggle with?)
- Archiv: a learner can review their drill history by article

---

## Multi-language generalization

The feature is built for German but the pattern generalizes. The `user` field in the German
domain already supports multiple users (Robert/German, daughter/French+Portuguese). The drill
should work for any language pair the domain supports — the LLM prompt just changes the
target language. Design the storage and the prompt builder language-agnostically so French
and Portuguese learners (and any future user) get the same feature without a rewrite.

---

## LLM integration

**Same fallback chain as `german_domain.py`:** Grok (grok-4-1-fast) → Haiku → Ollama
**Model tier:** Haiku sufficient for correction + short translation (mechanical, not qualitative)
**Prompt structure:**
```
Direction A (German in):
  "Correct this German note. Return: 1) corrected German, 2) English translation.
   Keep it natural and conversational. Note: [user_text]
   Context: This was written while reading an article titled [article_title]."

Direction B (English in):
  "Translate this English note into natural German.
   Return: 1) German translation. Note: [user_text]
   Context: Article titled [article_title]."
```
Response: JSON `{ "corrected": "...", "translation": "..." }`

---

## UI changes (Lesen page only)

**Current state:** NOTIZEN textarea + [+ Mehr] [− Weniger] [Merken] [Vorlesen] buttons

**Add:**
- A [Korrigieren ✓] button alongside the existing buttons
- Below the textarea, on click: the corrected block (Korrigiert + Auf Englisch) renders
- Below that: the [Tippe es nach] retype field — **auto-focused with all text selected
  after the correction renders** so the user can immediately begin retyping (saves a click,
  feels natural)
- Optional: green highlight on retype field when the typed text matches corrected text
  (case-insensitive, trimmed)
- The WÖRTER sidebar: optionally add newly-corrected words to the sidebar as vocabulary
  (words the user got wrong = strong addition candidates)

**No changes to:** Gespräche, Schreiben, Wörter, Archiv, Admin pages in this feature.

---

## Files to touch

- `templates/german/lesen_article.html` — add button, corrected block, retype field
- `domains/german/german_domain.py` — add `/api/lesen/correct` endpoint
- `domains/german/data/lesen_drills/` — new directory for drill storage
- Optionally: `phrasebook.json` writer to add corrected phrases with `lesen_context` flag

---

## Definition of done

- [ ] User can type a note and click Korrigieren to see corrected German + English translation
- [ ] User can type a note in English and see the German translation
- [ ] The retype field appears below and accepts input
- [ ] Correct match on retype (case-insensitive) provides visual confirmation
- [ ] Drill record stored to `lesen_drills/YYYY-MM-DD.json` with all fields above
- [ ] Multi-user field populated correctly (`user` field from session)
- [ ] LLM fallback chain functioning (Grok → Haiku → Ollama)
- [ ] No regression on existing Lesen functionality (Mehr/Weniger/Merken/Vorlesen)

---

## GitHub issue

**Title:** `[German] Lesen writing drill — contextual correction and retype`
**Labels:** `enhancement`, `german`, `lesen`
**Body:** paste this spec or link to `_working/feature_lesen_writing_drill_2026-06-08.md`

---

*Register as issue. Build after Guild autonomous agents build window (post Jun 13).*
