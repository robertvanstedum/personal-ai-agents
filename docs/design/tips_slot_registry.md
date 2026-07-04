# mini-moi Tips Slot Registry
**File:** `docs/design/tips_slot_registry_2026-07-04.md`
**Status:** Living document — update when slots are added or tip text changes
**Created:** 2026-07-04
**System:** Contextual Tips — spec #115

---

## How the system works

Tips are served from `config/curator/tips.json`. Each slot has a key, a text value, and an active flag. The server reads the slot key and returns the tip text if active, nothing if not. To change a tip, edit the file and sync. To hide a tip, set `active: false`. No redeploy needed — `config/curator/tips.json` is volume-mounted on EC2.

```json
{
  "slot.key": {
    "active": true,
    "text": "Tip text shown to user."
  }
}
```

To add a new slot: add the key here first, then add the server-side `tips.get('slot.key')` call in the relevant domain server, then add the frontend render point.

---

## Slot Registry

### Curator

| Slot key | Page | Context | Active |
|---|---|---|---|
| `briefing.main` | Curator briefing | Main briefing view | ✅ |

**`briefing.main`**
> Your daily briefing — curated signals across geopolitics, finance, and technology. Use the category filters to focus, or read straight through.

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
> Four categories — Cotidiano, Cultura, Notícias, Rio. Hover any word to translate it inline. Open an article to take notes and save words to Palavras.

**`portuguese.leitura.article`**
> Hover a word for a quick translation. Hit '+ Em Palavras' to save it to your vocabulary. Use Notas to write a reaction in Portuguese — then hit Corrigir for feedback.

**`portuguese.conversas`**
> Pick a persona and start a session. Maria is good for short practical exchanges; Lucas for casual conversation. Check 'Preparação de hoje' before starting to warm up.

**`portuguese.escrita`**
> Three modes: Diário (free write), Em Contexto (prompted), Vocabulário (use your saved words). Check 'Solicitar correção' to get feedback when you submit.

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
> Four categories — Alltag, Kultur, Nachrichten, Wien. Articles are selected and summarized for your level. Hover any word to translate it inline.

**`german.lesen.article`**
> Hover a word to translate it. Save words to Wörter with one click. Use the notes field to write a reaction in German — Corrigir gives you feedback on what you wrote.

**`german.gesprache`**
> Two modes: KI-Personas for structured practice with Vienna characters, Konversation for free conversation. Eight personas available — each with a specific context and register.

**`german.schreiben`**
> Three modes — free write, prompted, and vocabulary-driven. Always worth checking the correction toggle before submitting.

**`german.woerter`**
> Your German vocabulary saved from Lesen. Filter by origin or status, export to Anki, or switch to Treino mode to drill what you've saved.

**`german.archiv`**
> Full history of your German sessions — conversations, writing, and reading. Useful for tracking consistency and revisiting good sessions.

---

## Changelog

| Date | Change |
|---|---|
| 2026-07-04 | Initial registry — 15 slots across Curator, Meu Português, Mein Deutsch |

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
