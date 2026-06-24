# Spec 2 — Portuguese Domain Shell
*Created: 2026-06-24 — Claude.ai*
*Status: spec_ready*
*Gates on: Spec 1 (Auth) complete*
*Part of: Portuguese Domain + Multi-User Platform*

---

## Goal

Build the Portuguese language domain structure — all five tabs,
navigation, design system, and routing. No content yet (personas,
reading lists). The shell that Specs 3-9 fill in.

---

## Design system

Parallel to Mein Deutsch but distinct visual identity.

**Mein Deutsch:** Vienna café aesthetic. Dark nav #2A1F14, parchment
#F5F0E8, accent #C68A5E. Book metaphor.

**Português:** Rio de Janeiro aesthetic. Warmer, more vibrant.
Suggested palette for design session confirmation:
- Nav: deep tropical green #1A3A2A or ocean deep #0A2A3A
- Background: warm cream #F7F2E8 (slightly warmer than parchment)
- Accent: Rio sun amber #E8A020 or bougainvillea #C4365A
- Georgia serif stays — reads as educated, not corporate

Robert to confirm palette in design session before build.
One design constraint: must feel like you're in Rio, not in a
European café.

---

## Five tabs

| Tab | Portuguese | Equivalent | Content |
|-----|-----------|-----------|---------|
| 1 | Leitura | Lesen | Brazilian Portuguese reading lists |
| 2 | Conversas | Gespräche | KI-Persona voice/text sessions |
| 3 | Escrita | Schreiben | Writing drills |
| 4 | Palavras | Wörter | Phrasebook + vocabulary |
| 5 | Arquivo | Archiv | Session history |

Admin tab (Robert + wife only): visible only to admin role.

---

## Natural language chat (on every page)

Floating chat button, bottom right, every page. Opens an input.
User types anything in any language.

Backend:
- Route: `POST /api/pt/chat`
- Model: configurable, default gpt-4o-mini (fast, cheap)
- System prompt: knows what page the user is on, what the domain is,
  what mini-moi is, what each tab does
- Classification: help_request / feedback / bug_report / conversation
- All messages logged to `portuguese.chat_log` with classification
- Help responses: returned immediately to user
- Feedback/bugs: logged, included in Robert's daily summary

This replaces all help modals, tooltips, onboarding flows, and
feedback forms.

---

## Database schema additions

```sql
CREATE SCHEMA portuguese;

CREATE TABLE portuguese.sessions (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER REFERENCES auth.users(id),
    persona     VARCHAR(100),
    scene       VARCHAR(100),
    started_at  TIMESTAMP DEFAULT NOW(),
    ended_at    TIMESTAMP,
    turns       INTEGER DEFAULT 0
);

CREATE TABLE portuguese.chat_log (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER REFERENCES auth.users(id),
    page            VARCHAR(100),
    message         TEXT NOT NULL,
    classification  VARCHAR(50),  -- help_request, feedback, bug_report, conversation
    response        TEXT,
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE TABLE portuguese.user_personas (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER REFERENCES auth.users(id),
    name        VARCHAR(100) NOT NULL,
    context     TEXT NOT NULL,
    level       VARCHAR(50) NOT NULL,  -- iniciante, intermediário, avançado
    notes       TEXT,
    created_at  TIMESTAMP DEFAULT NOW(),
    is_active   BOOLEAN DEFAULT TRUE
);
```

---

## Flask app structure

New Flask app parallel to `domains/german/html_server.py`:

```
domains/portuguese/
  html_server.py        — Flask app, routes
  templates/
    portuguese_base.html
    leitura.html
    conversas.html
    escrita.html
    palavras.html
    arquivo.html
    admin.html
  static/
    portuguese.css      — domain-specific styles
  personas/             — persona .txt files (Spec 3)
  data/                 — reading lists, progress (Spec 9)
```

Port: 8770 (8768 = Operations agent, 8769 = cos-scheduler)

---

## Docker + ECR

New container: `minimoi/portuguese`
New ECR repo: `minimoi/portuguese`
Add to `docker-compose.prod.yml`
Add to CI/CD pipeline image build list (Spec E)

---

## Portal routing

Portal proxy adds Portuguese routes:
```python
PORTUGUESE_BACKEND = os.environ.get('PORTUGUESE_BACKEND', 'http://portuguese:8768')

@app.route('/portuguese/<path:path>')
@requires_domain('portuguese')
def portuguese_proxy(path):
    return proxy_to(PORTUGUESE_BACKEND, path)
```

---

## Preview page update

Current preview: Curator + German.
After this spec: Curator + German + Portuguese.

Portuguese card on preview:
- Screenshot or placeholder
- "Português — Brazilian Portuguese conversation practice"
- "Request Access →" (links to request form with domain=portuguese)
- Note: "Access to Portuguese domain is separate from German"

---

## Definition of Done

- [ ] domains/portuguese/ directory structure created
- [ ] Flask app running on port 8768
- [ ] All five tabs rendering (empty content OK)
- [ ] Natural language chat route working on all pages
- [ ] Chat log table created and logging messages
- [ ] Classification working (help vs feedback vs bug)
- [ ] Design system applied (palette confirmed by Robert first)
- [ ] Docker container built and running locally
- [ ] ECR repo created: minimoi/portuguese
- [ ] Image pushed to ECR
- [ ] Added to docker-compose.prod.yml
- [ ] Portal proxy routing /portuguese/* correctly
- [ ] requires_domain('portuguese') protecting all routes
- [ ] Preview page updated with Portuguese card
- [ ] Admin tab visible to admin role only
- [ ] Test: daughter logs in, sees Conversas tab, can open chat

## Commit message

`Portuguese domain: shell — five tabs, natural language chat,
auth integration, Docker container, preview page update`

---

*Spec 2 · Portuguese Domain series · 2026-06-24 · Claude.ai*
*Gates on: Spec 1 complete*
*Next: Spec 3 — Initial personas*
