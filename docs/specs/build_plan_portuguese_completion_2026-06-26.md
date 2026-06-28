# Build Plan — Portuguese Domain: Backend Completion
*Created: 2026-06-26 — Claude.ai*
*For: Claude Code — morning session 2026-06-26*
*Goal: fully functional Portuguese domain, ready for weekend test*

---

## Context

CSS parity done. Landing photo confirmed good. All tab shells
rendering. Voice session architecture in place. Backend stubs
return empty — routes exist but no DB, no real data.

Today completes the backend. Afternoon tweaks photos page by page.
Weekend is end-to-end test with first real user.

---

## Build order — do in this sequence

### 1. Conversas backend (first — highest value)

**Spec:** `docs/specs/spec_portuguese_conversas_2026-06-25.md`
**Why first:** Voice sessions are the core feature. Daughters use
this day one. Everything else builds on having real sessions saved.

**What's missing:**
- `portuguese.sessions` table doesn't exist yet
- `_save_session()` is a stub — doesn't write to DB
- `/api/pt/sessions` returns empty list

**Migration:** `domains/guild/db/migrations/003_portuguese_sessions.sql`

```sql
CREATE SCHEMA IF NOT EXISTS portuguese;

CREATE TABLE portuguese.sessions (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER REFERENCES auth.users(id),
    persona         VARCHAR(100),
    scene           VARCHAR(100),
    started_at      TIMESTAMP DEFAULT NOW(),
    ended_at        TIMESTAMP,
    turns           INTEGER DEFAULT 0,
    transcript      TEXT,
    reviewer_output JSONB
);

CREATE INDEX idx_pt_sessions_user ON portuguese.sessions(user_id);
CREATE INDEX idx_pt_sessions_date ON portuguese.sessions(started_at DESC);
```

**Routes to wire (html_server.py):**
- `/api/pt/review` → save to `portuguese.sessions` after analysis
- `/api/pt/sessions` → SELECT from `portuguese.sessions` WHERE user_id
- Arquivo Conversas tab → reads from `/api/pt/sessions`

**Test:** Start session with Maria → speak → end → analyse →
check Arquivo Conversas shows the session.

---

### 2. Translation + Palavras backend

**Spec:** `docs/specs/spec_portuguese_translation_palavras_2026-06-25.md`
**Why second:** Word capture from Leitura feeds Palavras. Both
need to work before Leitura has real articles.

**Migration:** `domains/guild/db/migrations/005_portuguese_vocabulary.sql`

```sql
CREATE TABLE portuguese.translation_cache (
    id          SERIAL PRIMARY KEY,
    portuguese  VARCHAR(500) UNIQUE NOT NULL,
    english     TEXT NOT NULL,
    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE TABLE portuguese.vocabulary (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER REFERENCES auth.users(id),
    portuguese      VARCHAR(500) NOT NULL,
    english         TEXT,
    source          VARCHAR(50),
    source_sentence TEXT,
    status          VARCHAR(20) DEFAULT 'biblioteca',
    added_at        TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, portuguese)
);
```

**Routes to wire:**
- `/api/pt/translate` → DeepL primary, Claude Haiku fallback,
  cache in `portuguese.translation_cache`
- `/api/pt/palavras` → SELECT from `portuguese.vocabulary`
  WHERE user_id, with source/status filters
- `/api/pt/palavras-add` → INSERT into `portuguese.vocabulary`
- `/api/pt/palavras-status` → UPDATE status WHERE id AND user_id

**Test:** Open Leitura (even with empty articles) → manually test
translate endpoint → check Palavras tab shows saved words.

---

### 3. Escrita backend

**Spec:** `docs/specs/spec_portuguese_escrita_backend_2026-06-25.md`
**Why third:** Depends on Palavras being wired (Vocabulário mode
saves to Palavras).

**Migration:** `domains/guild/db/migrations/006_portuguese_writing.sql`

```sql
CREATE TABLE portuguese.writing_sessions (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER REFERENCES auth.users(id),
    mode            VARCHAR(50),
    prompt          TEXT,
    original_text   TEXT NOT NULL,
    corrected_text  TEXT,
    feedback        JSONB,
    article_id      INTEGER,
    created_at      TIMESTAMP DEFAULT NOW()
);
```

**Routes to wire:**
- `/api/pt/escrita/prompt` → daily prompt from rotation list
- `/api/pt/escrita/correct` → Claude Haiku correction
- `/api/pt/escrita/save` → INSERT into `portuguese.writing_sessions`
- `/api/pt/arquivo/escrita` → SELECT from `portuguese.writing_sessions`
  WHERE user_id

**Test:** Diário mode → daily prompt loads → write text → Corrigir
→ correction displays → save → Arquivo Escrita shows session.

---

### 4. Leitura backend

**Spec:** `docs/specs/spec_portuguese_leitura_backend_2026-06-25.md`
**Why last:** Largest scope. Can work without it — Conversas,
Escrita, Palavras all function without articles.

**Migration:** `domains/guild/db/migrations/004_portuguese_articles.sql`

```sql
CREATE TABLE portuguese.articles (
    id              SERIAL PRIMARY KEY,
    url             VARCHAR(500) UNIQUE NOT NULL,
    title           TEXT NOT NULL,
    excerpt         TEXT,
    full_text       TEXT,
    source          VARCHAR(100),
    category        VARCHAR(50) NOT NULL,
    level           VARCHAR(20) DEFAULT 'intermediario',
    published_at    TIMESTAMP,
    added_at        TIMESTAMP DEFAULT NOW(),
    added_by        INTEGER REFERENCES auth.users(id),
    is_active       BOOLEAN DEFAULT TRUE
);
```

**Routes to wire:**
- `/api/pt/leitura-category` → SELECT from `portuguese.articles`
  WHERE category AND is_active
- `/api/pt/article/<id>` → fetch full text on demand, store result
- Admin form → POST `/api/pt/admin/article` to add articles

**Robert action after build:** Add 3-4 test articles via admin
form (one per category) to verify the full read flow works.

---

## Afternoon — photo review page by page

After all four backends are wired and tested, review each tab:

**Work order and candidates from handoff:**

| Tab | Current photo | Notes | Candidates to try |
|-----|-------------|-------|------------------|
| Conversas | Coconut bar "Boas Coisas" | May have glare | IMG_8996 (moody bar at dusk), IMG_8886 (fitness promenade) |
| Escrita | Bamboo forest Parque Lage | Calm/contemplative — probably right | Check object-position |
| Palavras | Coffee + pão de queijo | Solid for vocabulary | Verify right panel width |
| Arquivo | Favela + rock face | Dramatic — may feel heavy | IMG_8976 (sunset/atmospheric), IMG_8975 (lone umbrella) |
| Leitura hero | Recreio beach umbrellas | Good wide shot | Check object-position |
| Cotidiano card | Busy lanchonete Festa Junina | Good energy | Keep |
| Cultura card | Colonial Sarau bar yellow chairs | Good | Keep |
| Notícias card | Padaria street exterior | OK but generic | Consider swap |
| Cidade card | Beach volleyball palm trees | Good | Keep |

For each tab: open on phone + desktop, check photo reads well
at both sizes, adjust `object-position` if key subject is cropped.

---

## Weekend — end-to-end test plan

**Test as Robert first (owner):**

```
1. Conversas
   → Start session with Maria (padaria)
   → Speak 5+ turns in Portuguese
   → End session → Analisar
   → Check Arquivo Conversas shows session with analysis

2. Leitura (if articles added)
   → Browse categories
   → Open article
   → Highlight word → translation popover
   → Save word → Palavras shows it

3. Escrita
   → Diário mode → daily prompt loads
   → Write 3-4 sentences
   → Corrigir → correction displays with errors marked
   → Save → Arquivo Escrita shows session

4. Palavras
   → Words from Leitura and Conversas visible
   → Filter by source works
   → Status promotion works (biblioteca → praticando)

5. Arquivo
   → All three tabs show correct history
   → Only Robert's sessions visible (not system test data)
```

**Test as new user (invite first daughter):**

```
1. Daughter goes to minimoi.ai preview page
2. Clicks "Request Access" — Portuguese domain
3. Robert gets Telegram notification
4. Robert approves via /approve command
5. Daughter gets email with 48h login link
6. Daughter clicks link → sets password
7. Daughter sees Portuguese domain only (not German, not Guild)
8. Daughter starts Conversas with Maria
9. Robert checks Arquivo — sees his sessions only
10. Daughter checks Arquivo — sees her sessions only
   (user isolation confirmed)
```

**What to watch for:**
- VAD sensitivity — does it trigger correctly on phone?
- TTS voice quality — nova for Maria/Juliana, onyx for Carlos/Lucas
- Session save — does Arquivo update after each session?
- User isolation — critical, daughters must never see each other's data
- EC2 load — check Grafana (once built) or CoS health check during test

---

## Migration run order

Run on local DB first, then EC2:

```bash
# Local
docker exec postgres-ai-agents psql -U postgres personal_agents \
  < domains/guild/db/migrations/003_portuguese_sessions.sql
docker exec postgres-ai-agents psql -U postgres personal_agents \
  < domains/guild/db/migrations/004_portuguese_articles.sql
docker exec postgres-ai-agents psql -U postgres personal_agents \
  < domains/guild/db/migrations/005_portuguese_vocabulary.sql
docker exec postgres-ai-agents psql -U postgres personal_agents \
  < domains/guild/db/migrations/006_portuguese_writing.sql

# EC2 (after CI/CD deploys)
# CI/CD pipeline will deploy code — run migrations via SSM:
aws ssm send-command \
  --instance-ids i-0d13db821169627e2 \
  --document-name "AWS-RunShellScript" \
  --parameters "commands=[
    'docker exec minimoi-postgres psql -U postgres personal_agents \
     < /opt/minimoi/migrations/003_portuguese_sessions.sql'
  ]"
```

---

## Definition of Done — full Portuguese domain

Before first daughter is invited:

**Conversas:**
- [ ] Voice session works end-to-end on phone
- [ ] Session saved to `portuguese.sessions`
- [ ] Arquivo Conversas shows real sessions
- [ ] Analysis (Portuguese grammar feedback) displays

**Palavras:**
- [ ] `/api/pt/translate` returns English for Portuguese word
- [ ] Word saved from Leitura popover appears in Palavras
- [ ] Status promotion works

**Escrita:**
- [ ] Daily prompt loads
- [ ] Correction endpoint returns Portuguese grammar feedback
- [ ] Session saved and visible in Arquivo

**Leitura:**
- [ ] Admin can add articles via form
- [ ] Articles appear in correct category
- [ ] Full text loads on article open
- [ ] At least 2 articles per category added by Robert

**Auth + isolation:**
- [ ] Daughter can request access and create account
- [ ] Daughter sees Portuguese only
- [ ] Daughter's Arquivo shows only her sessions

**Photos:**
- [ ] All tabs have photos that match tab mood
- [ ] No cropping issues on phone

---

*Build Plan · 2026-06-26 · Claude.ai*
*Backends first, photos after, test this weekend*
*All specs in docs/specs/ — read before building*
EOF