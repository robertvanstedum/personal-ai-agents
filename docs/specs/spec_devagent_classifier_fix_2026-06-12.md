# Spec — Design/Dev Classifier Fix: Truncation + Structural Checks
*mini-moi · Guild*
*Created: 2026-06-12 22:48 CDT — Claude.ai*
*For: Claude Code*

---

## What's wrong

`dev_agent.py` (line 231) truncates document content to `content[:800]`
before sending it to the LLM for classification — roughly 10-15 lines. Both
`spec_guild_nav_restructure_2026-06-12.md` and
`spec_housekeeping_2026-06-12.md` have their `## Definition of Done` and
`## Commit` sections well past character 800. The LLM returns
`has_dod: false, has_commit: false` simply because it never sees those
sections — both got incorrectly flagged `incomplete`.

---

## The fix

1. **`has_dod` / `has_commit` — direct structural check, not LLM.** Search
   the *full* (untruncated) `content` for the headings directly —
   case-insensitive substring match: `'## definition of done' in
   content.lower()` and `'## commit' in content.lower()` (adjust for any
   reasonable heading variants). These are purely structural — presence of
   a heading — so they shouldn't depend on the LLM seeing them, or on any
   truncation length. Makes the check correct regardless of document length,
   permanently.

2. **Bump `content[:800]` → `content[:3000]`** for the LLM call itself —
   still used for `spec_title` and `referenced_files` extraction, which
   benefit from more context on longer documents.

---

## Definition of Done

- [ ] `has_dod` / `has_commit` computed via direct string search on full
      `content`, independent of the LLM call and its truncation
- [ ] LLM call truncation bumped to `content[:3000]` for `spec_title` /
      `referenced_files` extraction
- [ ] **Verification — re-classify the spec that exposed this bug**:
      manually re-run Design/Dev classification on
      `_working/spec_housekeeping_2026-06-12.md` (content unchanged). Should
      now return `has_dod: true, has_commit: true`, and its `design_log`
      status should update to `spec_ready`.
- [ ] Robert confirms `spec_housekeeping_2026-06-12.md` shows `SPEC READY`
      on Build Log/Queue after re-classification — this is the real test of
      the fix, not just a code review

**Note, not part of this fix's verification:** `spec_guild_nav_restructure_2026-06-12.md`
will likely *also* resolve `has_dod`/`has_commit` correctly on
re-classification (probably landing on `spec_ready`) — but its true status
is `done` (already built and verified). That's a separate manual-override
action, unrelated to whether this classifier fix is correct. Housekeeping is
the clean test case because `spec_ready` genuinely *is* its correct final
state.

---

## Commit

```bash
git add domains/guild/agents/dev_agent.py
git commit -m "fix: Design/Dev classifier — has_dod/has_commit via direct
structural search (not LLM-dependent), bump LLM context to 3000 chars

Fixes false-positive 'incomplete' flags on well-formed specs whose DoD/Commit
sections fall past the old 800-char truncation. Verified by re-classifying
spec_housekeeping_2026-06-12.md, which now correctly resolves to spec_ready."
git push origin main
```

---

*Spec · Guild · 2026-06-12*
