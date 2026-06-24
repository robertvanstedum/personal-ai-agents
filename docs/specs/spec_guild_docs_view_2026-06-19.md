# Spec — Guild Docs View
*Created: 2026-06-19 — Claude.ai*
*Status: Ready for `_working/`*
*Priority: Medium — useful now, small build*

---

## Context

The Guild UI has QUEUE, BUILD LOG, and ROADMAP but no way to browse
the `docs/` folder from within minimoi. Reference documents like
`docs/AWS_MIGRATION_PLAN.md`, `docs/CODE_REVIEW_PLAN.md`, and
`docs/LEARNING_SYSTEM_ROADMAP.md` are only accessible via GitHub
or by knowing the filename. Robert needs to find and read these
documents easily from the Guild UI — they are the record of what
was built, why, and what's planned.

---

## What this builds

A DOCS tab in the Guild nav. Reads from `docs/` in the repo.
Lists all documents grouped by type. Click any entry to read it
rendered as markdown. Same renderer as the ROADMAP view.

No database. No index file to maintain. Reads the directory directly.

---

## Navigation

Add DOCS to Guild sub-nav:

```
← GUILD   QUEUE   BUILD LOG   ROADMAP   DOCS
```

Route: `/guild/docs`
Default view: document list (grouped)
Click entry: document reader at `/guild/docs/[filename]`

---

## Document list view

Group documents from `docs/` into four sections.
Grouping is by filename prefix or directory:

### Reference
Documents describing what was built and why:
- `AWS_MIGRATION_PLAN.md`
- `CODE_REVIEW_PLAN.md`
- `LEARNING_SYSTEM_ROADMAP.md`
- `GESPRACHE_FORWARD_SPEC.md`
- `GESPRACHE_ROADMAP.md`
- `COS_PAGE_ROADMAP.md`
- `MEIN_DEUTSCH_PRODUCT_DESCRIPTION.md`

### Process
Documents describing how the team works:
- `DECISION_RECORD_PRACTICE.md`
- `DESIGN_SESSION_PROMPT.md`
- `HANDOFF_PROCESS.md`

### Domain docs
Product descriptions and domain-specific references:
- `MEIN_DEUTSCH_V11_RELEASE.md`
- Any file in `releases/` subdirectory

### Decision Records
- Show count: "X decision records committed"
- Link: "View all →" → lists files in `docs/decision-records/`
- Each DR shows: date (from filename), title (from first `#` heading),
  domain and status (from frontmatter), LoRA candidate badge

**Grouping logic:**
Read the `docs/` directory. Assign files to groups by name pattern:
- `*_PLAN.md`, `*_ROADMAP.md`, `*_SPEC.md`, `*_DESCRIPTION.md` → Reference
- `DECISION_RECORD*`, `DESIGN_SESSION*`, `HANDOFF*` → Process
- `releases/*`, `*_RELEASE*` → Domain docs
- `decision-records/*.md` → Decision Records section
- Anything else → Reference (default)

**Each list entry shows:**
```
AWS Migration Plan                          2026-06-18
Containerization through GPU instance.      docs/AWS_MIGRATION_PLAN.md
Replaces Mac Mini purchase.
```
- Title: first `# ` heading from the file
- Date: file's git last-modified date
- Subtitle: second line of the file (usually the italic description)
- Filename: shown small below

---

## Document reader view

Route: `/guild/docs/[filename]`

Renders the document as markdown. Same renderer as ROADMAP:
- Georgia serif headings
- Parchment background
- Amber accents
- Status badges if present
- Code blocks styled

Header bar above the document:
```
← Back to Docs    AWS_MIGRATION_PLAN.md    Last updated: 2026-06-18
                                            [View on GitHub →]
```

"View on GitHub →" links to the file on GitHub:
`https://github.com/robertvanstedum/personal-ai-agents/blob/main/docs/[filename]`

---

## Decision Records sub-view

Route: `/guild/docs/decisions`

Table view of all DRs in `docs/decision-records/`:

```
DECISION RECORDS                    [Filter: domain ▾] [status ▾] [LoRA ▾]

3 records · most recent 2026-06-18

2026-06-18  Gespräche Async Analysis       german/platform  active  [LoRA]
2026-06-18  Monitoring Stack Selection     platform         active  [LoRA]
2026-06-18  Mac Mini vs AWS               platform         active  [LoRA]
2026-06-17  DR Practice MVP               platform         active  [LoRA]
2026-06-16  Gespräche Mobile Priority     german/platform  active  [LoRA]
2026-06-17  Guild Roadmap Database (cancelled) guild       cancelled
```

Reads frontmatter from each DR file for domain, status, lora-candidate.
Title from first `#` heading. Date from filename.
Click row → document reader for that DR.

Filters: domain, status (active/superseded/cancelled), LoRA candidate.

---

## Implementation notes

**Directory read:** Flask route reads `docs/` using `os.listdir()` or
`pathlib.Path('docs/').iterdir()`. No index file needed — reads live
from the filesystem (which is the repo on EC2).

**Git last-modified date:** Same approach as ROADMAP — `git log -1
--format="%ci" docs/[filename]` per file. Cache results for 5 minutes
to avoid running git on every page load.

**Markdown rendering:** Reuse the existing ROADMAP markdown renderer.
No new renderer needed.

**Decision Record frontmatter:** Parse the YAML frontmatter block
(`---` delimited) at the top of each DR file. Extract `domain`,
`status`, `lora-candidate`. Use PyYAML or simple string parsing —
the frontmatter is simple key-value, no complex YAML needed.

**On AWS:** `docs/` is part of the repo, cloned to EC2. The docs
view reads from the local clone. On git pull (triggered by CI/CD
deploy), new docs are immediately available. No S3 needed for docs.

---

## What does NOT change

- `docs/` directory structure — no reorganization needed
- Any existing doc files — no reformatting required
- QUEUE, BUILD LOG, ROADMAP views — no regression
- GitHub as the authoritative source — docs view is a reader,
  not an editor

---

## Update _working/ROADMAP.md

Add to Guild domain, Agreed targets:

```markdown
| Docs browser | v1 | target | this spec |
```

---

## Definition of Done

- DOCS tab appears in Guild nav
- `/guild/docs` lists all files in `docs/` grouped into four sections
- Each entry shows title, date, subtitle, filename
- Click any entry → rendered markdown at `/guild/docs/[filename]`
- "View on GitHub →" link correct for each document
- `/guild/docs/decisions` lists all DRs with frontmatter metadata
- Frontmatter filters (domain, status, LoRA) work
- Click DR row → rendered DR
- Git last-modified date shows correctly per document
- Decision Records count badge accurate
- No regression on QUEUE, BUILD LOG, ROADMAP views
- Verified: Robert can find and read `docs/AWS_MIGRATION_PLAN.md`
  from the Guild UI without going to GitHub

## Commit

`Add Guild Docs view — browse and read docs/ from minimoi UI,
Decision Records table with frontmatter filters.`

---

*Spec · 2026-06-19 · Claude.ai*
