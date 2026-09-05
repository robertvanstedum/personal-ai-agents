# When a collection of AI agents became a team

## A case study in design, independent verification, and human ownership

**Draft v7 · Author of this round: OpenAI Codex · September 5, 2026**
**Based on Claude Code draft v6 and Robert van Stedum's product direction**

Over several days, I worked with four AI systems as a small delivery team. I
set the direction and made the decisions. Claude.ai helped develop and
reconcile the product design. Grok challenged the early design position.
Claude Code turned the agreed direction into a detailed design and then built
it. Codex independently reviewed both the design and the committed code.

The important result was not that several models participated. It was that
they worked through stable roles, durable handoffs, independent evidence, and
human decision gates. The agents did not merge their own work. The agent that
wrote the code did not approve it. Repository changes remained serialized even
when design, implementation, and review streams overlapped.

This was the point where an interesting experiment began to feel like an
operating method.

## From lower-consequence trials to consequential work

We first exercised the method on smaller and lower-consequence changes. Four
Guild improvements were reviewed and merged on September 3: a working
Experiment page, a sortable and filterable Prototype Lab table, card artwork,
and interface wording. A separate demonstration application, IoT Connect, went
through its own multi-round build and release reviews before being promoted
and verified on its live host.

Those runs mattered because they tested the mechanics. They did not establish
that I should trust the same process with personal work where provenance,
authorship, and judgment have consequences.

The next build did. It created the durable Work foundation for Chief of Staff,
with Career cover-letter collaboration as the first intended use. The common
service is deliberately not a cover-letter generator. It keeps the identity of
a piece of work, preserves supplied sources, records drafts and revisions,
distinguishes authorship, controls one-time authority, records dispositions,
and recovers safely after interrupted writes. It contains no model, vendor, or
network dependency.

This distinction is foundational to my idea of mini-moi. I am not trying to
automate myself out of the work. For a role I care about, I expect to rewrite a
first letter many times and put my own language into it directly. For an
exploratory application, I may ask Chief of Staff to adapt a standard letter.
Over time, ten rewrites should become two or three—not because the system takes
over, but because it has accumulated a better understanding of my history,
evidence, preferences, and voice.

The same foundation should later support memos, meeting preparation, decision
analysis, and work products in subjects that have nothing to do with Career.
That is why it is product-neutral and why the design intentionally avoids a
large Career-specific workflow.

## The operating method

The method is simple enough to reuse:

1. **Develop the intent in the open.** Preserve the important human statements,
   disagreements, and design positions in documents rather than relying on one
   chat session.
2. **Challenge the design before implementation.** An independent reviewer
   returns a controlled verdict: revise, ready, blocked, or ready for Robert.
3. **Freeze the accepted design.** Pin the exact document by SHA-256 so the
   implementation can be reviewed against what was actually approved.
4. **Authorize one bounded build.** One builder works in a clean worktree and
   produces evidence before writing the handoff file.
5. **Freeze and verify the committed diff.** A different agent reviews the
   actual commit, reruns tests, and reproduces any claimed defect with an
   executable probe.
6. **Revise monotonically.** Every new revision names the review it answers.
   The reviewer either returns another `REVISE` or signs off on the exact
   commit.
7. **Return control to the person.** I review the cleared diff and decide
   whether it merges. Passing review never authorizes the next gate.

The handoff protocol made asynchronous work dependable. Twice, a review and a
new revision crossed in flight. Because both named exact revisions and commits,
the work was not lost or silently reviewed against the wrong state.

## What independent review changed

Across the two foundation slices, Codex recorded 38 unique findings: 25 during
design checkpoints and 13 against implementations. Eleven review documents
returned `REVISE` before the relevant checkpoints were cleared.

The most important implementation defect went directly to the product premise.
A permission object was marked immutable, but a mapping inside it could still
be changed. An independent probe created permission to store external material,
changed its provenance after validation, and caused the service to accept it as
Robert-authored material. The first correction was also incomplete because a
read-only wrapper could retain a mutable backing dictionary. A second review
round closed the remaining path.

If an agent's own or external language can be relabelled as mine, the system may
later retrieve that language as evidence of my voice. It begins learning from
itself while presenting the result as a better understanding of me. That is not
a minor bug in a cover-letter tool. It breaks the mini-moi premise silently.

Three other findings show why executable reproduction mattered:

- A one-time grant could be accepted by two concurrent threads because checking
  and consuming it were separate operations.
- Three write paths assumed the operating system wrote every byte in one call.
  A forced short write produced a canonical one-character file without an
  error.
- A malformed recovery record could install a candidate as canonical state
  before the parser discovered that required evidence was missing.

All of these sat outside the normal path. The probes turned plausible concerns
into repeatable failures, and the resulting regression tests now make the same
failures harder to reintroduce.

## Evidence from the run

The foundation was split into two separately authorized slices:

| Slice | Status on September 5 | Change size |
|---|---|---:|
| W0a: accumulation and bounded retrieval | Merged as PR #200, commit `476568d` | 41 files; 6,329 additions |
| W0b: durable Work service | Independently cleared at `c9cdde7`; PR #201 still awaiting Robert's merge decision | 25 files; 12,202 additions and 17 deletions |

Together, the reviewed tree adds 18,514 lines across 61 files: 8,366 lines in
the provider-neutral Work package and 10,148 lines in tests and synthetic
fixtures. The size is not itself an achievement; it shows how much executable
boundary evidence accompanied the service.

At the final W0b review, the builder reported 695 passing CoS tests with one
skip, and 1,318 passing repository tests with 18 skips. Codex independently
reproduced the same total collection with 695/1 in the CoS suite and
1,317/19 repository-wide; the one-test passed/skip difference was
environment-dependent, with no failures.

The W0b delivery went through ten numbered handoffs: five design rounds and
five implementation rounds. W0a separately reached implementation revision
four. Fourteen Codex review documents preserve 11 `REVISE` verdicts and three
checkpoint-clearing verdicts. W0b's accepted design is 2,862 lines and is pinned
by SHA-256. Three implementation divergences were declared, examined, and
accepted rather than hidden.

## What scales—and what should remain human

The repeatable part is not model output. It is the control structure around the
output: bounded authority, independent review, reproducible evidence, exact
versioning, and a human decision between gates. Once a design is accepted, a
builder-reviewer loop can continue asynchronously and return a reviewed diff
rather than an unexamined implementation.

This creates capacity, but not unlimited parallel mutation. Several workstreams
can advance in design or review, while repository editing remains serialized.
The scarce resource becomes careful review and decision-making rather than raw
code generation.

The part I do not want to scale away is the design conversation. The question
of what should be built—especially what an agent may remember, infer, write, or
represent as mine—remains collaborative and human-led. A rigorous delivery loop
can produce a well-verified version of the wrong idea. It cannot replace product
judgment.

## What has not been proved yet

This case has honest limits. W0b has passed independent review, but PR #201 is
not merged as of this draft. Nothing is connected to the running Chief of Staff.
No private Career material has entered the service, and no cover letter has
been produced through it. The current evidence comes from synthetic data and
adversarial tests in one repository with one human decision owner.

The next proof is deliberately plain. First, I will run a file-level acceptance
test against the merged service. Then the thin Chief of Staff integration should
let me attach a job description and say: **“Start cover letter.”** Chief of
Staff should preserve the description, use only the Career sources I have
authorized, create and store a first draft, show me the committed result, and
wait for my review. It must not submit anything.

One successful letter will not complete the case. The deeper test is whether
the collaboration becomes more useful across many letters while I remain the
author, editor, and decision owner. If that works, the same foundation becomes
credible for the broader work of mini-moi.

That is the inflection point I see: not agents producing more code, but a human
and several agents developing a repeatable way to design, challenge, build, and
trust work that matters.
