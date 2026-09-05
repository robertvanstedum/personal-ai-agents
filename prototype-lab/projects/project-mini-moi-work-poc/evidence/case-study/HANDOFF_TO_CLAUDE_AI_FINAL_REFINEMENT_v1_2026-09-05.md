# Handoff to Claude.ai — final pre-testing refinement

**Prepared by:** OpenAI Codex
**Date:** 2026-09-05
**Next-round author:** Claude.ai
**Decision owner and final editor:** Robert van Stedum
**Baseline:** `CASE_STUDY_DRAFT_v8_CLAUDE_AI_2026-09-05.md`
**Status sought:** living public draft; not final release copy

## Robert's direction

V8 is very strong. Do not rewrite it. Apply only the narrow corrections shared
by Codex and Grok plus the two factual clarifications Grok identified. After
this round, hold substantive narrative changes until file-level and real Career
testing produce new evidence.

The case study should remain third-person and stand alone for an external
reader. Keep personality through exact choices, defects, and judgments. Robert
must remain visible as creator, collaborator, editor, decision owner, and
intended user.

## Inputs

1. `CASE_STUDY_DRAFT_v8_CLAUDE_AI_2026-09-05.md`
2. `robert-intent-and-mini-moi-work-vision--9bc367fd.md`
3. `GROK_REVIEW_OF_CLAUDE_v8_2026-09-05.txt`
4. `CODEX_REVIEW_OF_CLAUDE_v8_2026-09-05.md`

Grok's response ended mid-sentence under “One thing to cut.” Preserve that as
an incomplete transmission. Do not guess what Grok intended to remove. The
existing close may remain unless Robert later supplies the missing feedback.

## Fact refresh at handoff

At 2026-09-05 10:19 CDT, GitHub still reported PR `#201` as `OPEN`, with no
merge time and head commit
`c9cdde7fa0d572f7f122d75e55e099afea9b3ac9`. Recheck immediately before v9 is
returned; this is a timestamped fact, not a permanent status.

## Required narrow edits

1. Replace “The next build was that test” with:
   “The next build raised the stakes and prepared that test.”
2. Replace “No agent merged its own work” with:
   “No agent merged any work.”
3. Explain the file arithmetic in the evidence section. Use wording such as:
   “Together the reviewed tree contains 61 unique paths—five W0b paths revise
   files introduced in W0a—and adds 18,514 lines.”
4. Qualify the self-referential paragraph so it claims only the discipline
   actually used for prose. Codex's proposed replacement is approved as the
   starting point:

   “The same separation of authorship, review, and human authority is also
   being applied to this document. Claude Code produced the first draft; Codex
   corrected counts, scopes, and attributions; Claude.ai wrote this round;
   Grok reviewed it; and Robert holds final editorial authority. The case study
   is therefore being tested by part of the discipline it describes.”
5. Add one early status qualifier after the opening description of the Work
   foundation. It must say that W0a was merged and W0b was independently
   reviewed but not yet merged at the recorded fact-check point. Recheck PR
   #201 before writing; if its status has changed, update every related passage
   together and name the time basis.
6. Keep the lower-consequence trial paragraph but attach the verified PR range
   `#195-#198` to the four Guild increments. IoT Connect's promotion is PR
   `#199`. Do not add more release detail.
7. Replace or sharpen the paragraph Grok identified as generic (“The repeatable
   part is not model output…”). Make it specific to this run without adding
   length: pinned design, frozen handoff, reproduced failure, and Robert's gate
   are the useful concrete anchors.

## Preserve

- The title and overall structure.
- The authorship defect as the narrative center.
- The 25/13 finding split, 11 `REVISE` verdicts, W0a/W0b revision scopes,
  line counts, test counts, and stated limitations.
- The important-versus-exploratory Career distinction and hands-on-keyboard
  language.
- No first-person narrator.
- The companion vision as a separate document, not material to absorb into the
  case study.
- The next proof in future tense: file-level acceptance, then handing Chief of
  Staff a job description and saying “Start cover letter.”

## Deliverables

Return without overwriting v8:

1. `CASE_STUDY_DRAFT_v9_CLAUDE_AI_2026-09-05.md`
2. The identical draft as `.txt` for later review and portability.
3. `CHANGE_NOTE_v9_CLAUDE_AI_2026-09-05.md`, limited to the changes above.
4. A short public-readiness note identifying any fact that must be refreshed
   after user testing or immediately before release.

Do not call this final. Mark it as a living draft awaiting file-level and real
Career-use evidence.

## Public GitHub destination after review

Robert has authorized public GitHub storage for the living draft and companion
vision so they are not lost and can evolve with testing. After v9 returns and
Codex verifies the exact files, prepare a reviewed diff in a clean worktree for:

```text
docs/portfolio/mini-moi-collaborative-work/
  CASE_STUDY_AGENT_TEAM.md
  MINI_MOI_WORK_VISION.md
```

The stable filenames should carry `Status: Draft` inside the documents; Git
history supplies versioning. Do not publish local review packets, `_working/`
paths, Planning Studio identifiers, or handoff files. No release announcement
is part of this draft commit. Robert will separately approve the reviewed diff;
no agent merges it. The eventual Chief of Staff release announcement may link
the final case study when user testing is complete.
