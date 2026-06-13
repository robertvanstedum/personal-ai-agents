# Spec — Guild Landing Copy: minimoi.ai "What's Running"
*mini-moi · Guild*
*Created: 2026-06-13 — Claude.ai*
*For: Claude Code*

---

## What's changing

The Guild entry in minimoi.ai's "What's running" section is stale — it
describes an early "Chief of Staff model: intent register, domain health,
four-cabinet coordination layer" design that predates today's actual build
(Daily Briefing, Career Focus pipeline, Build Discipline queue, Operations
agent).

Replace the Guild entry with the text below, finalized this session through
Claude.ai/Grok review.

**Curator and Mein Deutsch entries are unchanged** — both confirmed still
accurate against what's live. Do not touch either.

---

## New Guild copy

> Guild is where the agents and I improve together.
>
> Build focuses on clear specs and visible pipelines so roadmap items and
> direction stay with me instead of drifting across domains. Every feature
> moves through spec to queue to done at /guild/build. Operations keeps the
> system stable and memory from turning into noise. Chief of Staff sits
> across everything — it coordinates the agents in Guild and pulls in
> Curator and German so my real goals and private concerns don't get lost in
> domain work.

**Note the structure:** unlike Curator/Mein Deutsch (single paragraph), this
entry is **two paragraphs** — the opening sentence stands alone, then the
rest follows as a second paragraph. Preserve that break, using whatever
paragraph markup the template already uses for this section.

---

## Definition of Done

- [ ] Locate the Guild entry within the "What's running" section of the
      minimoi.ai landing template
- [ ] Replace its content with the text above, preserving the two-paragraph
      structure (short opener, then the rest)
- [ ] Curator and Mein Deutsch entries confirmed unchanged — no edits to
      either
- [ ] Visual check on minimoi.ai — Guild entry renders cleanly, paragraph
      spacing readable, no leftover stale "intent register / four-cabinet"
      language anywhere on the page
- [ ] Robert confirms on the live site

---

## Commit

```bash
git add <minimoi.ai landing template path>
git commit -m "content: refresh Guild entry on minimoi.ai landing page

Replaces stale 'Chief of Staff model / intent register / four-cabinet'
design language with a current description of the Daily Briefing, Career
Focus pipeline, Build Discipline queue, and Operations agent. Finalized via
Claude.ai/Grok review. Curator and Mein Deutsch entries unchanged."
git push origin main
```

---

*Spec · Guild · 2026-06-13*
