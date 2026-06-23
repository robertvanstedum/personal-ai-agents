# Handoff — Queue Fix + CoS GitHub Sanity Check
*Created: 2026-06-17 — Claude.ai*
*Audience: Claude Code*
*Scope: Three quick actions, no new builds*

---

## Action 1 — Confirm Gespräche specs in `_working/`

The following specs were produced in the June 15/16 session and should
be in `_working/` as Spec Ready items. They are not visible in the
current queue view. Confirm they exist and are correctly filed:

| Spec file | What it does | Priority |
|-----------|-------------|----------|
| `spec_gesprache_testrun_followup_2026-06-15.md` | Part C (dead-end fix) highest, Part A (spacing), Part B (latency) | **Highest** |
| `spec_gesprache_consolidated_2026-06-15.md` | 7 Gespräche improvements, Parts A+C fold in | High |
| `spec_gesprache_transcript_paste_panel_2026-06-16.md` | Clipboard paste panel, mobile-first | High |

If any are missing from `_working/`, copy them from the session outputs
and confirm they file as Spec Ready (all three have DoD + Commit sections
so should file as `ready`, not `design`).

If they exist but aren't showing in the queue, check whether the Dev
Agent has processed them since the filing rules update.

**Expected result:** Three Gespräche items appear as SPEC READY in the
queue, above the Guild Milestone & Goals Editor.

---

## Action 2 — Cancel Domain Product Descriptions queue row

The item "Domain Product Descriptions: Content Writing Session" in
INCOMPLETE is a planning doc (`plan_domain_product_descriptions_writing_session_2026-06-16.md`),
not a build spec. It should not be in the queue — the writing session
produces specs, and those specs get queued when the session happens.

**Action:** Delete or mark as Deferred the queue row for this item.
No DR needed — trivial filing error per the new allowlist rules.
Note in the build log: "Removed — planning doc, not a spec. Will
produce specs when writing session runs."

---

## Action 3 — Add GitHub sanity check to CoS periodic tasks

This is not a build item. Add to the CoS periodic task list:

**Task: GitHub / repo sanity check**
Frequency: periodic — after significant build sessions or when queue
drift is suspected.

What CoS checks:
- Confirm what's actually committed to the repo vs. what specs say
  was shipped
- Check for drift between `_working/ROADMAP.md` status and actual
  commit history
- Flag any specs marked Done in the queue that don't have a
  corresponding commit
- Flag any commits that didn't produce a queue update
- Surface findings to Robert as a briefing note, not as build queue
  items

This is an audit function, not a build function. Output is a short
briefing ("3 items confirmed, 1 drift found: X was marked done but
no commit found"), not a spec.

Add to `cos_context.json` or wherever CoS periodic tasks are tracked.

---

## No Definition of Done required

These are queue and config fixes, not builds. Completion is confirmed
when:
- Gespräche specs appear as SPEC READY in the queue
- Domain Product Descriptions row is gone from INCOMPLETE
- CoS periodic task list includes GitHub sanity check

---

*Handoff · 2026-06-17 · Claude.ai*
