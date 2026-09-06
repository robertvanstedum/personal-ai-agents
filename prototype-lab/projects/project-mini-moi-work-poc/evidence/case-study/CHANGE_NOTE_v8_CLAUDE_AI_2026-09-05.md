# Change note — Case study v8 (Claude.ai round)

**Baseline:** `CASE_STUDY_DRAFT_v7_CODEX_2026-09-05.md`
**Constraints applied:** `CODEX_FACT_CHECK_AND_EDITORIAL_REVIEW_v1_2026-09-05.md`, all twelve items
**Companion:** `robert-intent-and-mini-moi-work-vision--9bc367fd.md` — referenced once, not merged
**Length:** v8 ≈ 1,700 words including header and table (v7: 1,515). Within the 1–3 page target.

## Material changes from v7

1. **Third-person throughout.** Every first-person construction converted to "Robert," "the team," or a neutral subject. Personality is carried by the specific defect, the important/exploratory contrast, the seven-step method, and Robert's named decisions — not by narrator voice.

2. **Dateline added to the opening.** "Early September 2026" and "a personal system he calls mini-moi" orient an external reader who has no prior context. v7 assumed the reader knew what mini-moi was.

3. **Method applied to the document itself.** New paragraph at the end of "The operating method" stating that the case study went through a Claude Code draft, Codex fact check, Claude.ai round, Grok review, and Robert's final authority. This is the strongest available evidence that the method is reusable, since it was reused on prose rather than code. **Robert may cut it if it reads as self-congratulatory.**

4. **"Why it was caught" made explicit.** One new sentence after the authorship defect: the agent that wrote the permission object did not review it, and the reviewer had to demonstrate rather than describe. v7 stated the rule and the defect separately; v8 connects them.

5. **Trust-boundary paragraph sharpened using the vision's language.** "It is the failure the system exists to prevent, and it would have been silent." Draws on the vision's "closed imitation loop" framing without importing the vision.

6. **Hands-on-keyboard stated as design intent.** "His hands on the keyboard are part of the design, not friction to remove" — from Robert's direction and the vision, where v7 had only "put my own language into it directly."

7. **Table timestamps added** so status is legible at a glance: W0a merge time, W0b clearance time, PR #201 open.

8. **Limits section headline changed** from "What has not been proved yet" to "What has not been proved," and opens with "The limits are real" — slightly firmer for an employer-facing reader.

## Fact-check compliance

| Codex item | How v8 handles it |
|---|---|
| 1. Finding split 25/13 combined | Stated as combined; no 19/12 figure used |
| 2. Eleven `REVISE` across fourteen documents | Stated exactly; "nine refusals" not used |
| 3. W0b ten handoffs 5+5; W0a impl rev four | Stated separately, not conflated |
| 4. W0a merged, W0b cleared, PR #201 open | Table, limits section, and table header "at fact check, September 5" |
| 5. Change sizes | Exact figures; "additions" used, not "lines" |
| 6. Builder vs Codex test runs | Both reported, difference attributed to environment |
| 7. "Canonical state" not "real files" | Used in the malformed-recovery bullet |
| 8. Substrate, not production capability | "No model, vendor, or network dependency"; limits section lists no runtime wiring |
| 9. Mutation serialized | "Repository changes stayed serialized even while … overlapped in time" |
| 10. Roles | Grok "challenged the early design position"; no implication Grok reviewed implementation |
| 11. No counterfactual | "Each implementation finding was made concrete with a reproducible probe" |
| 12. Length | ≈1,700 words; roughly 3 pages, under the 5-page ceiling |

## Not rechecked

**PR #201 status was not rechecked at the time of this writing.** v8 says "open at the time of the fact check" and "not merged at the time of the fact check." If PR #201 has merged by publication, the table row, the limits paragraph, and the "against the merged service" phrase in the next-proof section all need updating together.

## Editorial choices Robert may want to revisit

- **Title unchanged.** "When a collection of AI agents became a team" is retained. An alternative that foregrounds Robert rather than the agents: *"A team of one person and four agents: designing, challenging, and verifying work that matters."* Offered, not recommended.
- **The self-referential paragraph** (change 3) — see note above.
- **"Robert" appears fourteen times.** Third-person prose about one named person can start to feel like a profile. If it does, some instances can become "the team" or passive-voice-free neutral constructions without losing agency.
