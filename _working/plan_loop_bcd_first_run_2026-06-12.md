# Loop B/C/D — First-Run Review Checklist
*mini-moi · Guild · CoS Intelligence Loops*
*Created: 2026-06-12 11:43 CDT — Claude.ai*
*Process doc, not spec-track — no build required, won't appear on the Queue*
*For: Robert, Sunday 6/15 morning*

---

## Why this exists

Loops B/C/D fire for the first time this Sunday. Loop A's history is the
template: it ran, produced real output, and *then* needed calibration
(article discrimination, staleness detection, warm-lead fixes). Expect the
same here — first-run review isn't pass/fail, it's "what needs adjusting
before the next fire."

Each loop already has a defined output destination — review means looking
at what's there with these questions in hand, not building anything new.

---

## Loop B — German watch (fires 09:00)

**Where to look:** Telegram (minimoi_cmd_bot), and check
`cos_agenda` / German domain memory for any written entries.

**Expected output:**
- Practice cadence check — days since last Gespräche/Lesen/etc. session
- Emerging language tool search — any new tools/resources surfaced
- Tutor/group search — Vienna-area conversation groups or tutors found

**Review questions:**
- Did it fire? (check `guild.agent_log` for the run timestamp)
- Is the practice-cadence number *accurate* — does it match your actual
  session history? (This is the easiest one to sanity-check immediately.)
- Tool/tutor search: relevant to German learning specifically, or generic
  noise? Anything genuinely new to you?

**Go:** cadence accurate, search results plausible and on-topic → loop
continues as scheduled (next: Sunday 6/22).

**Recalibrate:** cadence wrong → likely a data-source bug (wrong table/field
for session history) — note specifics for Claude Code. Search results
off-topic or empty → prompt/query tuning, similar to Loop A's early fixes.

---

## Loop C — Curator scout (fires 10:00)

**Where to look:** Curator → Desk → "Suggested by CoS" queue.

**Expected output:** emerging topics surfaced for potential Curator
coverage, added to the Desk's suggestion queue.

**Review questions:**
- Did anything land in "Suggested by CoS"?
- Are the topics genuinely *emerging* (not things already well-covered in
  recent Curator briefings)?
- Do they fit Curator's scope (geopolitics/finance) and your interests, or
  feel like generic trending-topics noise?

**Go:** topics present, novel relative to recent coverage, on-scope → loop
continues (next: Sunday 6/22).

**Recalibrate:** empty queue → check the search/scoring pipeline ran at all.
Topics redundant with recent coverage → needs a "already covered recently"
filter, similar to Curator's existing age-penalty logic.

---

## Loop D — Novelty watch (fires 1st + 15th, 08:00)

**Where to look:** Telegram, and/or a written report — check
`guild.agent_log` for where Loop D wrote its output.

**Expected output:** competitive scan of the mini-moi space — new tools,
products, or projects in the personal-AI-agent space — each categorized as
threat / complement / incorporate.

**Review questions:**
- Did it find anything real, or is the list empty/generic?
- Are the threat/complement/incorporate categorizations *sensible* —
  do you agree with how something got bucketed?
- Anything here that should feed into the roadmap (`docs/ROADMAP.md`)?

**Go:** real findings, categorization makes sense (even if nothing urgent)
→ loop continues (next fire: 7/1).

**Recalibrate:** empty or hallucinated findings → search query/source tuning.
Categorization feels off → may need clearer criteria for what
threat/complement/incorporate mean — worth a quick design note if so.

---

## After reviewing all three

- Anything "Go" — nothing to do, loops continue on schedule.
- Anything "Recalibrate" — note specifics (which loop, what's wrong) and
  bring to a Claude.ai session for a small calibration spec, same pattern
  as Loop A's fixes. Doesn't need to happen same-day.
- Anything genuinely interesting from Loop D → add a line to
  `docs/ROADMAP.md` under the relevant section.

This whole review should take 10-15 minutes — it's a sanity check, not a
deep audit.

---

*Loop B/C/D first-run review · 2026-06-12*
*Not spec-track — informational, for Robert's Sunday review*
