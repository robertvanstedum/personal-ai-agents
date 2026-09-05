# When a collection of AI agents became a team

## A case study in design, independent verification, and human ownership

**Draft v9 · Author of this round: Claude.ai · September 5, 2026 · Status: Draft — living draft awaiting file-level and real Career-use evidence**
**Baseline: Claude.ai draft v8, with narrow corrections from the Codex and Grok reviews of September 5, 2026. Product intent from Robert van Stedum's mini-moi Work vision, a companion document.**

Over several days in early September 2026, Robert van Stedum worked with four AI systems as a small delivery team, building the first durable piece of a personal system he calls mini-moi. He set the direction and made every decision that mattered. Claude.ai developed and reconciled the product design with him. Grok challenged the early design position. Claude Code turned the agreed direction into a detailed design and then built it. Codex independently reviewed both the design and the committed code.

The result worth writing about is not that several models participated. It is that they worked through stable roles, durable handoffs, independent evidence, and human decision gates. No agent merged any work. The agent that wrote the code did not approve it. Repository changes stayed serialized even while design, implementation, and review overlapped in time.

That is the point at which an interesting experiment started to look like an operating method.

## From lower-consequence trials to consequential work

The method was first exercised on smaller changes where a mistake would cost little. On September 3, four Guild improvements were reviewed and merged as PRs #195–#198: a working Experiment page, a sortable and filterable Prototype Lab table, card artwork, and interface wording. A separate demonstration application, IoT Connect, went through its own multi-round build and release reviews before being promoted as PR #199 and verified on its live host.

Those runs tested the mechanics. They did not establish that the same process could be trusted with personal work — where provenance, authorship, and judgment carry consequences.

The next build raised the stakes and prepared that test. It created the durable Work foundation for Chief of Staff, the relational center of mini-moi, with cover-letter collaboration for Robert's active job search as the first intended use. The service is deliberately not a cover-letter generator. It keeps the identity of a piece of work, preserves supplied sources, records drafts and revisions, distinguishes who authored what, controls one-time authority, records dispositions, and recovers safely after an interrupted write. It contains no model, vendor, or network dependency. Of its two slices, the first was merged and the second had been independently reviewed but not yet merged at the recorded fact-check point; the evidence section gives the exact state.

That distinction is foundational to what Robert is building. The aim is not to automate himself out of the work. For a role he cares about, he expects to rewrite a first letter many times and put his own language into it directly — his hands on the keyboard are part of the design, not friction to remove. For an exploratory application, he may ask Chief of Staff to adapt a standard letter and review the result. Over time, ten rewrites should become two or three, not because the system takes over but because it has accumulated a better understanding of his history, evidence, preferences, and voice. The companion vision document sets out that larger intent; this case study covers only the method and its evidence.

The same foundation is meant to support memos, meeting preparation, decision analysis, and work in subjects unrelated to Career. That is why it is product-neutral, and why the design avoids a large Career-specific workflow.

## The operating method

The method is simple enough to reuse.

1. **Develop the intent in the open.** Preserve the important human statements, disagreements, and design positions in documents, not in one chat session.
2. **Challenge the design before implementation.** An independent reviewer returns a controlled verdict: revise, ready, blocked, or ready for Robert.
3. **Freeze the accepted design.** Pin the exact document by SHA-256 so the implementation can be reviewed against what was actually approved.
4. **Authorize one bounded build.** One builder works in a clean worktree and produces evidence before writing the handoff.
5. **Freeze and verify the committed diff.** A different agent reviews the actual commit, reruns the tests, and reproduces any claimed defect with an executable probe.
6. **Revise monotonically.** Each revision names the review it answers. The reviewer returns another `REVISE` or signs off on the exact commit.
7. **Return control to the person.** Robert reviews the cleared diff and decides whether it merges. Passing review never authorizes the next gate.

The handoff protocol is what made asynchronous work dependable. Twice, a review and a new revision crossed in flight. Because both named exact revisions and commits, nothing was lost and nothing was silently reviewed against the wrong state.

The same separation of authorship, review, and human authority is also being applied to this document. Claude Code produced the first draft; Codex corrected counts, scopes, and attributions; Claude.ai wrote this round; Grok reviewed it; and Robert holds final editorial authority. The case study is therefore being tested by part of the discipline it describes.

## What independent review changed

Across the two foundation slices, Codex recorded 38 distinct findings: 25 at design checkpoints and 13 against implementations. Eleven review documents returned `REVISE` before the relevant checkpoint cleared.

The most important implementation defect went straight to the product premise. A permission object was marked immutable, but a mapping inside it could still be changed. An independent probe created permission to store external material, altered its provenance after validation, and caused the service to accept the material as Robert-authored. The first correction was itself incomplete — a read-only wrapper could still hold a mutable backing dictionary — and a second review round closed the remaining path.

Why this mattered more than an ordinary bug: if an agent's own language, or outside text, can be relabeled as Robert's, the system may later retrieve it as evidence of his voice. It begins learning from itself while presenting the result as a deepening understanding of him. In a system whose whole purpose is to accumulate genuine understanding of one person, that is not a defect in a cover-letter tool. It is the failure the system exists to prevent, and it would have been silent.

The reason it was caught is structural. The agent that wrote the permission object did not review it, and the reviewer was required to demonstrate the defect rather than describe it.

Three other findings show why executable reproduction was the rule:

- A one-time grant could be consumed by two concurrent threads, because checking it and consuming it were separate operations.
- Three write paths assumed the operating system wrote every byte in a single call. A forced short write produced a canonical one-character file with no error.
- A malformed recovery record could install a candidate as canonical state before the parser discovered that required evidence was missing.

All three sat outside the ordinary path. Each implementation finding was made concrete with a reproducible probe, and the resulting regression tests make the same failures harder to reintroduce.

## Evidence from the run

The foundation was split into two separately authorized slices.

| Slice | Status at last recheck, September 5, 10:19 CDT | Change size |
|---|---|---:|
| W0a: accumulation and bounded retrieval | Merged as PR #200, commit `476568d` (September 4, 21:46 CDT) | 41 files; 6,329 additions |
| W0b: durable Work service | Independently cleared at `c9cdde7` (September 5, ~02:53 CDT); PR #201 open, awaiting Robert's merge decision | 25 files; 12,202 additions, 17 deletions |

Together the reviewed tree contains 61 unique paths — five W0b paths revise files introduced in W0a — and adds 18,514 lines: 8,366 in the provider-neutral Work package and 10,148 in tests and synthetic fixtures. The size is not the achievement. It shows how much executable boundary evidence accompanied the service.

At the final W0b review, the builder reported 695 passing tests with one skip in the CoS suite and 1,318 passing with 18 skips repository-wide. Codex independently collected the same totals: 695/1 and 1,317/19. The one passed-versus-skipped difference was environment-dependent; neither run had a failure.

W0b went through ten numbered handoffs — five design rounds and five implementation rounds. W0a separately reached implementation revision four. Fourteen Codex review documents preserve 11 `REVISE` verdicts and three checkpoint-clearing verdicts. W0b's accepted design runs to 2,862 lines and is pinned by SHA-256. Three implementation divergences from that design were declared, examined, and accepted rather than hidden.

## What scales, and what should stay human

The repeatable part is not model output. It is what surrounded the output in this run: a design pinned by hash before anyone built against it, handoffs that named exact commits so crossed reviews could not silently diverge, defects that had to be reproduced before they counted, and a merge decision that stayed with Robert. Once a design was accepted, the builder–reviewer loop ran asynchronously and returned a reviewed diff instead of an unexamined implementation.

That creates capacity, not unlimited parallel mutation. Several workstreams can advance in design or review while repository editing stays serialized. The scarce resource becomes careful review and decision-making rather than raw code generation.

The part Robert does not want to scale away is the design conversation. What should be built — especially what an agent may remember, infer, write, or represent as his — remains collaborative and human-led. A rigorous delivery loop can produce a thoroughly verified version of the wrong idea. It cannot replace product judgment.

## What has not been proved

The limits are real. W0b passed independent review, but PR #201 was still open at the last recheck, September 5 at 10:19 CDT. Nothing is connected to the running Chief of Staff. No private Career material has entered the service, and no cover letter has been produced through it. The evidence so far comes from synthetic data and adversarial tests in one repository, with one human decision owner.

The next proof is deliberately plain. First, a file-level acceptance test against the merged service. Then a thin Chief of Staff integration should let Robert attach a job description and say: **"Start cover letter."** Chief of Staff should preserve the description, use only the Career sources Robert has authorized, create and store a first draft, show him the committed result, and wait. It must not submit anything.

One successful letter will not complete the case. The deeper test is whether the collaboration grows more useful across many letters while Robert remains the author, editor, and decision owner. If that holds, the same foundation becomes credible for the broader work mini-moi is meant to do.

That is the inflection point worth recording — not agents producing more code, but a person and several agents arriving at a repeatable way to design, challenge, build, and trust work that matters.
