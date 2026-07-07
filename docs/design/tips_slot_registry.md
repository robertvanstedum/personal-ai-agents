# mini-moi Tips Slot Registry
**File:** `docs/design/tips_slot_registry_2026-07-04.md`
**Status:** Living document — update when slots are added or tip text changes
**Created:** 2026-07-04
**System:** Contextual Tips — spec #115

---

## How the system works

**One file controls all tips across all domains — Curator, Mein Deutsch, and Meu Português:**

```
config/curator/tips.json
```

Each slot has a key, a text value, and an active flag:

```json
{
  "slot.key": {
    "active": true,
    "text": "Tip text shown to user."
  }
}
```

**To change tip text:** edit `config/curator/tips.json` directly — changes are immediate on dev (server reads on each request) and immediate on EC2 (file is volume-mounted, no redeploy needed).

**To hide a tip without deleting it:** set `"active": false`.

**To add a new slot:** add the key here first, then wire the server-side `tips.get('slot.key')` call in the relevant domain server, then add the frontend render point — then update this registry.

---

## Slot Registry

### Curator

| Slot key | Page | Context | Active |
|---|---|---|---|
| `briefing.main` | Daily (Curator briefing) | Main briefing view | ✅ |
| `curator.reading_room` | Reading Room | Article library view | ✅ |
| `curator.scans-dives` | Scans & Dives | Scan browse view | ✅ |
| `curator.leanings` | Leanings | Beliefs tracker | ✅ |
| `curator.archive` | Archive | Reading history | ✅ |

**`briefing.main`**
> Your daily briefing — curated signals across geopolitics, finance, and technology. Use the category filters to focus, or read straight through.

**`curator.reading_room`**
> Articles you've saved for deeper reading. Open any article to read the full text, take notes, and save words to your vocabulary.

**`curator.scans-dives`**
> Quick pass through today's top stories — scored and ranked. Click into any article to go deeper.

**`curator.leanings`**
> Track beliefs in progress — from open question to held position. Add a leaning, set its state, and watch your thinking evolve over time.

**`curator.archive`**
> Your reading history — articles you've opened or saved. Use it to revisit stories or track what you've been following.

---

### Meu Português

| Slot key | Page | Context | Active |
|---|---|---|---|
| `portuguese.landing` | Landing / Início | Hero section | ✅ |
| `portuguese.leitura` | Leitura | Article browse | ✅ |
| `portuguese.leitura.article` | Leitura | Article detail view | ✅ |
| `portuguese.conversas` | Conversas | Persona selection | ✅ |
| `portuguese.escrita` | Escrita | Writing interface | ✅ |
| `portuguese.palavras` | Palavras | Vocabulary list | ✅ |
| `portuguese.arquivo` | Arquivo | History view | ✅ |

**`portuguese.landing`**
> Meu Português pulls live articles from Brazilian and European Portuguese sources daily. Start in Leitura to read, or jump to Conversas to practice with a persona.

**`portuguese.leitura`**
> Best on desktop — read, hover to translate, and save words to Palavras in one flow. On mobile, tap and hold to translate.

**`portuguese.leitura.article`**
> Hover a word for a quick translation. Hit '+ Em Palavras' to save it to your vocabulary. Use Notas to write a reaction in Portuguese — then hit Corrigir for feedback.

**`portuguese.conversas`**
> For the best voice experience, copy the prompt and use it in Grok, Claude, Gemini, or ChatGPT on your phone — native apps have faster, more natural voice. In-app voice works but has more latency due to API calls.

**`portuguese.escrita`**
> Best on desktop for the full correction experience. On mobile, dictate into the text field and submit — Corrigir works the same way.

**`portuguese.palavras`**
> Words saved from Leitura, Conversas, and Escrita all land here. Filter by origin or status, add words manually, or export to Anki. Switch to Treino mode to drill what you've saved.

**`portuguese.arquivo`**
> Your full history — past conversations, writing sessions, and reading. Use it to track consistency and revisit sessions worth reviewing.

---

### Mein Deutsch

| Slot key | Page | Context | Active |
|---|---|---|---|
| `german.landing` | Landing | Hero section | ✅ |
| `german.lesen` | Lesen | Article browse | ✅ |
| `german.lesen.article` | Lesen | Article detail view | ✅ |
| `german.gesprache` | Gespräche | Persona selection | ✅ |
| `german.schreiben` | Schreiben | Writing interface | ✅ |
| `german.woerter` | Wörter | Vocabulary list | ✅ |
| `german.archiv` | Archiv | History view | ✅ |

**`german.landing`**
> Mein Deutsch pulls live German articles daily, focused on Vienna and Austria. Start in Lesen to read, or go to Gespräche to practice with a Vienna persona.

**`german.lesen`**
> Best on desktop — read, hover to translate, and save words to Wörter in one flow. On mobile, tap and hold to translate.

**`german.lesen.article`**
> Hover a word to translate it. Save words to Wörter with one click. Use the notes field to write a reaction in German — Corrigir gives you feedback on what you wrote.

**`german.gesprache`**
> For the best voice experience, copy the prompt and use it in Grok, Claude, Gemini, or ChatGPT on your phone — native apps have faster, more natural voice. In-app voice works but has more latency due to API calls.

**`german.schreiben`**
> Best on desktop for the full correction experience. On mobile, dictate into the text field and submit — Corrigir works the same way.

**`german.woerter`**
> Your German vocabulary saved from Lesen. Filter by origin or status, export to Anki, or switch to Treino mode to drill what you've saved.

**`german.archiv`**
> Full history of your German sessions — conversations, writing, and reading. Useful for tracking consistency and revisiting good sessions.

---

## Changelog

| Date | Change |
|---|---|
| 2026-07-04 | Initial registry — 15 slots across Curator, Meu Português, Mein Deutsch |
| 2026-07-05 | Synced tip texts to updated tips.json — leitura, conversas, escrita, lesen, gesprache, schreiben revised |
| 2026-07-07 | Added 4 Curator slots: curator.reading_room, curator.scans-dives, curator.leanings, curator.archive |

---

## Adding a new slot — checklist

1. Add entry to this registry (slot key, page, context, tip text)
2. Add `tips.get('slot.key', {})` call in the relevant domain server
3. Add frontend render point in the relevant template
4. Add entry to `config/curator/tips.json`
5. Run `sync_docs.sh` to push updated registry to EC2
6. Test on dev before prod

---

*Living document · mini-moi · Do not delete — update in place*
