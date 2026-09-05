# A repeatable pattern for building with an agent team

## Proof-tested on small work. Then pointed at something that mattered.

**Draft v6 · September 2026**

---

## Where this becomes serious

The pattern below was proof-tested over two or three days on work chosen
deliberately because it was not critical: a demonstration application, a data
table, some interface wording, a deployment. The point of those runs was to
exercise the mechanics, not to produce anything that mattered. It behaved well,
which was encouraging and proved nothing important — none of it would have hurt
if it had been wrong.

The run described here is different on two axes at once, and both have to be
true for it to count.

**The method got harder.** The pattern was pointed at a foundation — code that
other work will depend on, holding rules about authorship, permission, and
whether a saved thing was really saved. Thirty-eight findings were raised and
closed before anything reached a human for approval, and the ones that mattered
were integrity defects rather than cosmetic ones: text that could be relabelled
as someone else's writing, a single-use permission that could be spent twice, a
truncated file reported as durably saved. None would have been caught by reading
the code and forming an opinion.

**The thing being built started to matter.** Everything before this was a
demonstration or a proof of concept — built to show that something was possible,
not to be used again next week. This is the first component intended for
ongoing production use, and it sits in the career domain: helping a person
prepare cover letters that are accurate and genuinely theirs.

That domain is unforgiving in a specific way. Two requirements pull against each
other and both are hard failures. Every factual claim has to be supported by the
person's real history, because an overstatement in a job application is
consequential. And the result has to sound like them rather than like a
generated candidate, because a technically true letter in nobody's voice is
worthless. The system's usefulness depends on holding both at once, over months,
across many letters.

Which is why one defect from the review matters more than the rest. The design's
central rule is that the system's own drafts must never become evidence of the
person's voice; otherwise it retrieves its own writing as though it were theirs
and quietly closes a loop of imitation. The reviewer found a way to break
exactly that rule: it issued a permission for external material, changed one
field afterwards, and the service accepted that text as the owner's own writing.

In a demonstration, that is a bug. Here it is the entire premise failing
silently, in a way the person would never see and could not correct. That is the
difference between proof-testing a method and depending on one.

---

## The pattern

1. **Design in the open, with disagreement.** People and models argue about what
   the thing is. Documents, not chat. Every position preserved.
2. **Attack the specification until it stops yielding.** An independent reviewer
   tries to break the design on paper, repeatedly, before any code exists.
3. **Freeze the specification and pin it by hash.** The build is later reviewed
   against exactly what was approved, not a memory of it.
4. **Build unattended.** One agent implements. It writes its evidence, writes a
   handoff file last, then freezes its working tree.
5. **Verify adversarially.** A different agent reviews the exact committed diff
   and must reproduce every defect it claims with a working probe.
6. **Loop until the reviewer signs off.** Revisions are numbered and each names
   the review it answers.
7. **A human approves the reviewed diff.** No agent merges.

That is the whole method. It is written down because the value is not in any
one run of it. It is in the fact that it can be run again, on the next thing,
and the next, without renegotiating how the work is done.

---

## The roles

| Participant | Role |
|---|---|
| Decision owner | Product intent; authorizes or refuses each gate and each diff |
| Design partner | Long-form thinking about what the capability is, before specification |
| Architecture reviewer | Independent read of the design position; returns a verdict |
| Independent reviewer | Owns the record; reviews every design revision and every commit |
| Builder | Writes the detailed specification and all the code |

Two rules hold it up. **The agent that writes the code never approves it**, and
roles reverse between projects but never within one. **No agent merges.** Both
exist so that no participant is ever author and judge at once.

---

## Proof-testing, at three sizes

Before the foundation build, the same protocol ran at three scales across two
to three days, some of it concurrently, on work chosen for being low-consequence.

**Small, during the day, an hour or less each.** A data table rebuilt as a
sortable, filterable grid after a review found that deep-linked filters were
invisible and could not be cleared. Card artwork replaced. Two pieces of
interface wording corrected. Each ran the same cycle — build, test, independent
read, merge on approval — while the design conversation for the larger build was
still going. Four merged changes before the foundation build was authorized.

**Medium, overnight, one gate.** A demonstration application promoted into the
repository and deployed to cloud hosting. Seven reviewed revisions. The reviewer
verified the live deployment, not only the diff, and confirmed the running image
matched the approved one.

**Large, overnight, two gates.** The foundation. Five design rounds and five
build rounds. Described below.

The small changes and the large one used the same handoff files, the same freeze
protocol, and the same verdict vocabulary. Nothing was bespoke. A small change
completes the loop in one pass; a foundation takes ten.

Running them concurrently is what makes the pattern worth writing down. The
design argument for the foundation could stay slow and human-led precisely
because the smaller work was progressing under the same rules without needing
that attention.

---

## The serious run, in detail

### Design: nineteen findings before any code

The specification went through **five review rounds** before implementation was
authorized. Three findings show what design review is for.

- A later piece of work could **read** prior approved work but had no way to
  **cite** it as a hash-checked input. That silently defeats the purpose of the
  capability, which is that each round of work builds on the last.
- A file-reading helper passed its allowed-file-type rule to one of two calls.
  The other defaults to text and Markdown, so every internal record would have
  been rejected on the first run.
- Crash recovery could be starved by leftover temporary files sharing a folder
  with a bounded scan. Given enough of them, resuming work would quietly stop
  working, with no error raised.

The first would have surfaced months later as "why does this never improve." The
third would probably never have been found. The approved design ran to 2,862
lines and was pinned by hash.

### Verification: twelve findings, each reproduced before it was reported

Four review rounds against the actual committed diff. Four findings worth
naming.

**Forged authorship**, described above, and the one that justifies the whole
exercise. A permission object was declared immutable, but the dictionary inside
it was not. The first fix was itself incomplete — it still passed through an
externally backed read-only view — and the next round closed that too. Two
rounds of adversarial review to fully close a defect that a reading would not
have seen at all.

**A single-use permission spent twice.** Verification and consumption were
separate steps with no lock. A two-thread test produced two accepted uses of one
permission.

**A truncated file reported as durably saved.** The operating system may write
fewer bytes than requested; three call sites ignored the return value. Forced
short writes produced a file containing a single character, under its final
name, with no error.

**A malformed recovery record changing real files.** The recovery parser
accepted shapes the writer never produces. Demonstrated end to end: a record
replaced and a pointer installed, with no committed marker to show for it.

None appear on the normal path. A review that read the code and formed an
opinion would have passed all four.

### Result

| | |
|---|---|
| Gates completed | 2 |
| Code | ~8,400 lines |
| Tests | ~10,100 lines |
| Test suite at completion | 695 in the affected area, 1,318 repository-wide |
| Findings raised and closed | 38 |
| Findings before any code existed | 19 |
| Code findings, each independently reproduced | 12 |
| Build revisions | 10 |
| Divergences declared rather than hidden | 3, all accepted |

---

## The protocol that makes it reusable

The pattern only repeats because the mechanics are boring and fixed.

- **Atomic handoffs.** Evidence first, handoff file last, then freeze. Nothing
  changes underneath a review.
- **Hash-pinned verdicts.** Every review names the commit it applies to. Twice a
  review and a new revision crossed in flight; because both were pinned, nothing
  was lost or misattributed.
- **Monotonic revisions.** A resubmission always names the review it answers.
- **A closed verdict vocabulary.** Revise, ready, blocked, ready for the decision
  owner. No prose interpretation needed.
- **Declared divergences.** Where the builder could not follow the design
  exactly, it said so in writing rather than deviating quietly.
- **Preserved history.** Fourteen review documents, every design revision, every
  divergence, still on disk. The argument is reconstructable.

None of this is novel. All of it is what turns a good session into a procedure.

---

## What this scales, and what it does not

**What scales.** Verification, and the number of things in flight. The reviewer
does not need the decision owner present, does not tire, and applies the same
standard to the tenth revision as the first. Once a specification is frozen, the
build and review loop becomes capacity that can be pointed at the next piece of
work. Several streams run this way at once because each carries its own frozen
specification, its own handoff folder, and its own audit trail — which is how
four small changes shipped while the foundation was still being designed.

**What does not scale, and should not.** Design. The daytime argument about
what the thing is stayed slow, verbal, and human-led, and that is where the
quality came from. Adversarial review changed what was built, not merely what
was caught: the final design is smaller and stricter than the first, because a
reviewer showed that some rules could not be enforced and some boundaries could
be crossed. Feeding a weak specification into this loop produces a
well-verified implementation of the wrong thing.

**The practical limit is review capacity, not build capacity.** That is a better
constraint than the one it replaces, and it is measurable: findings per round,
rounds to sign-off, defects reproduced rather than asserted.

---

## What this run does not show

- **The capability has not been used for real work yet.** It is built and tested
  against synthetic data. The first real letter is the test that finally counts,
  and because this is meant for ongoing use rather than a demonstration, working
  once would not settle it either. The standard is whether it still holds after
  many letters, which no amount of review can establish in advance.
- **Nothing is wired into the running system.** No interface, no model calls, no
  network access. That is deliberately the following gate.
- **Thirty-eight findings means thirty-eight defects existed.** The builder
  produced sound normal-path code and defective boundaries. That is an argument
  for the second reviewer, not against the method.
- **A handful of runs is a pattern, not a proof.** It is enough to write down
  and reuse. It is not enough to generalize about every kind of software, and
  the sample is one repository and one decision owner.

---

## The shape of it

A specification worth handing to a builder is one that has already survived
several participants trying to break it. A build worth approving is one that a
different participant has already tried to break again.

Proof-testing a method on work that does not matter tells you the mechanics
function. It does not tell you whether you would trust the output. The answer
arrives only when the method is pointed at something with real consequences,
built to be used again next week rather than demonstrated once — and the
reviewer comes back nine times before it will sign off.

Two things had to change together. The verification got serious enough to find
defects that a reading never would. And the work got serious enough to deserve
it. That is the run worth writing down.
