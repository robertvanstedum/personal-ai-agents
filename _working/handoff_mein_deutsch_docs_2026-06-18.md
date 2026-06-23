# Handoff — Mein Deutsch Domain Docs Update
*Created: 2026-06-18 — Claude.ai*
*Audience: Claude Code*
*Scope: Two new files, one replacement, one GitHub release update*

---

## Context

`docs/GERMAN.md` exists in the repo but is the old version — written
before v1.1, predates voice sessions, KI-Sitzung, mobile work, and
the cross-workflow pipeline. It needs to be replaced.

Two new documents have been written today in Robert's voice. These are
the first honest, non-brochure descriptions of the German domain.

---

## Action 1 — Replace docs/GERMAN.md

**Source:** `docs_MEIN_DEUTSCH_PRODUCT_DESCRIPTION_2026-06-18.md`
**Destination:** `docs/MEIN_DEUTSCH_PRODUCT_DESCRIPTION.md` (new filename)

This is the German domain product description — the evergreen reference
document. Written in Robert's voice, not brochure language. Covers why
the system exists, how it works, what shipped in v1.1, and what's coming.

**Also:** Archive the old `docs/GERMAN.md` by renaming it to
`docs/GERMAN_ARCHIVE_pre-v1.1.md` — don't delete, it has historical
value. The new `docs/MEIN_DEUTSCH_PRODUCT_DESCRIPTION.md` replaces it
as the current reference.

---

## Action 2 — Add v1.1 release note

**Source:** `docs_MEIN_DEUTSCH_V11_RELEASE_2026-06-18.md`
**Destination:** `docs/releases/MEIN_DEUTSCH_V11_RELEASE_2026-06-18.md`

The v1.1 release note — shorter, for the GitHub release body.
Create `docs/releases/` directory if it doesn't exist.

---

## Action 3 — Update GitHub release

Update the existing `mein-deutsch-v1.1` GitHub release tag description
with the content from `docs_MEIN_DEUTSCH_V11_RELEASE_2026-06-18.md`.

The current release description is the old brochure version. Replace
with the new version — honest, personal, includes today's Gespräche
mobile improvements.

---

## Action 4 — Update README.md

In the German / Mein Deutsch section of README.md, update the link:
- Old: `[docs/GERMAN.md]` → remove or update
- Add: `[Mein Deutsch](docs/MEIN_DEUTSCH_PRODUCT_DESCRIPTION.md)` —
  German language practice domain

---

## Definition of Done

- `docs/MEIN_DEUTSCH_PRODUCT_DESCRIPTION.md` exists and renders
  correctly on GitHub
- `docs/GERMAN.md` renamed to `docs/GERMAN_ARCHIVE_pre-v1.1.md`
- `docs/releases/MEIN_DEUTSCH_V11_RELEASE_2026-06-18.md` exists
- GitHub release `mein-deutsch-v1.1` description updated
- README.md updated with correct link
- No other files changed in this commit

## Commit

`Update Mein Deutsch domain docs — new product description in Robert's
voice, v1.1 release note, archive old GERMAN.md.`

---

*Handoff · 2026-06-18 · Claude.ai*
