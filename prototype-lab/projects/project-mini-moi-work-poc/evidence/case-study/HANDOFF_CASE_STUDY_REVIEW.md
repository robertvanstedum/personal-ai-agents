# Handoff — case study review

**To:** Claude (chat), Grok, Codex · **From:** Robert (via Claude Code) · **Date:** 2026-09-05
**Artifact:** `CASE_STUDY_DRAFT_v6.md` in this directory · **Draft author:** Claude Code
**Status:** first draft for editorial review. Robert is prime author and final editor.

---

## 1. The thesis, in Robert's words

> "This is the pattern that can be repeated and leveraged to scale up and do more
> build, not a convenience for me."
> "We proof tested the process over a few days but this to me is the real point
> it becomes serious."
> "In the 2-3 days before, for less critical tasks, but to prove the point."

The subject of the piece is **the method**, not the person and not the product.
The arc is:

1. A repeatable pattern is stated plainly enough to reuse: design in the open,
   attack the specification until it stops yielding, freeze and hash it, build
   unattended, verify adversarially with reproduced defects, loop until the
   reviewer signs off, human approves the diff.
2. It was **proof-tested for two to three days on deliberately low-consequence
   work** — a demo application, a data table, interface wording, a deployment.
   Those runs exercised the mechanics and proved nothing important.
3. It was then **pointed at a foundation** — code other work depends on, holding
   rules about authorship, permission, and whether a saved thing was really
   saved. **That is where it becomes serious**, and it held.
   **Two axes changed at once, and Robert wants both stated:** (a) the joint
   independent build, i.e. the method got harder; and (b) the significance of the
   product — everything before was a genuine demo or proof of concept, whereas
   this is the first component intended for ongoing production use. Both are
   required for the inflection claim.
4. What scales is verification and the number of streams in flight. What does
   not scale, and should not, is the design argument.

### The domain, and why it is named

Robert is content for the career domain to be named: this is about preparing
cover letters that are **accurate and authentically his own voice**. That is the
point of the second axis. The domain is unforgiving because two requirements pull
against each other and both are hard failures — every factual claim must be
supported by real history (an overstatement in an application is consequential),
and the result must sound like him rather than a generated candidate (a true
letter in nobody's voice is worthless). It must hold both over months and many
letters.

This is what makes the **forged-authorship defect** the centrepiece rather than
one item in a list: the design's central rule is that the system's own drafts
never become evidence of the person's voice, and the reviewer found a way to
break exactly that rule. In a demo it is a bug; here it is the premise failing
silently in a way the person could not see or correct. Draft v6 now leads with
that connection.

### Explicitly NOT the message

- **Not speed.** Overnight timing is incidental and must not be the headline.
- **Not convenience** for one person.
- **No personal biography.** The domain is named; Robert's own history,
  employers, materials, and sleeping habits are not. Keep those out.
- Not a product pitch for mini-moi, and not a vendor comparison.

---

## 2. What each reviewer is asked to do

- **Claude (chat):** structure and argument. Is the pattern stated crisply enough
  that a reader could actually run it? Does the proof-test → serious arc land?
  Cut anything that drifts toward marketing register.
- **Grok:** the skeptical technical read. Is any claim overstated? Would an
  engineer find a hole? Is the defect evidence as compelling as it reads, and is
  the scaling argument honest about its limits?
- **Codex:** factual accuracy against the record. Every figure below is checkable
  in the repository and the preserved review documents. Flag anything wrong,
  misleadingly rounded, or missing context.

Voice: plain, short sentences, no hype, honest about limits, "we" for the team.

---

## 3. Verified data points

From the repository and `planning-studio/initiatives/INIT-2026-0004-mini-moi-collaborative-work/`.

### The serious run (two gates, one foundation)

| Fact | Value |
|---|---|
| Gate 1 merged | commit `476568d`, 2026-09-04 21:46 CDT |
| Gate 2 ready for approval | commit `c9cdde7`, 2026-09-05 02:53 CDT |
| Gate 1 size | 41 files, 6,329 lines |
| Gate 2 size | 25 files, 12,202 lines |
| Code, both gates | ~8,400 lines |
| Tests, both gates | ~10,100 lines |
| Test counts at completion | 695 passed / 1 skipped in the affected area; 1,318 passed / 18 skipped repository-wide |
| Product or vendor identifiers in the package | zero, enforced by test |
| Build revisions | 10 (5 design, 5 implementation) |
| Findings before any code existed | **19** |
| Code findings, each reproduced with a probe | **12** |
| Total findings raised and closed | **38** across both gates |
| Divergences declared rather than hidden | 3, all examined and accepted |
| Review documents preserved | 14 |
| Approved design | 2,862 lines, pinned by SHA-256 |
| Reviewer sign-off refusals before READY | 9 across the two gates |

### Proof-testing runs, two to three days prior, deliberately low-consequence

| Size | Work | Outcome |
|---|---|---|
| Small (≤1 hour each, daytime, concurrent with design) | Data table rebuilt as a sortable/filterable grid after a review found deep-linked filters invisible and unclearable; card artwork; two interface wording corrections | 4 merged changes before the foundation build was authorized |
| Medium (overnight, one gate) | Demonstration application formalized, then promoted and deployed to cloud hosting | 5 revisions then 7 revisions; reviewer verified the live deployment and confirmed the running image matched the approved one |

### The four code defects worth naming, all reproduced by the reviewer

1. **Forged authorship.** A permission object was frozen but its inner mapping
   was not. The reviewer issued a permission for external material, altered one
   field afterwards, and the service accepted that text as the owner's own
   writing — the exact boundary the design exists to defend. The first fix was
   itself incomplete (it still passed through an externally backed read-only
   view); the next round closed it.
2. **A single-use permission spent twice.** Verification and consumption were
   separate steps with no lock; a two-thread barrier test produced two accepted
   uses of one permission.
3. **A truncated file reported as durably saved.** `os.write` may write fewer
   bytes than requested; three call sites ignored the return value. Forced
   one-byte writes produced a canonical file containing a single character with
   no error raised.
4. **A malformed recovery record changing real files.** The recovery parser
   accepted shapes the writer never emits. Demonstrated end to end: a work
   record replaced (620 → 335 bytes) and a pointer installed, with no committed
   marker.

### The three design catches worth naming, before any code existed

1. A later piece of work could read prior approved work but could not cite it as
   a hash-checked input — silently defeating the purpose of the capability.
2. A file-reading helper passed its allowed-file-type rule to one of two calls;
   the other defaults to text and Markdown, so every internal record would have
   been rejected on first run.
3. Crash recovery could be starved by leftover temporary files sharing a folder
   with a bounded scan, so resuming work would quietly stop working with no error.

### The protocol elements that make it reusable

Atomic handoffs (evidence first, handoff file last, then freeze); hash-pinned
verdicts naming the exact commit; monotonic revision numbers each naming the
review they answer; a closed verdict vocabulary; declared divergences; preserved
history. Two reviews crossed a new revision in flight and nothing was lost or
misattributed because both were hash-pinned.

---

## 4. What must stay

- The **proof-test → serious** arc, and that the earlier work was chosen for
  being low-consequence on purpose.
- **Two structural rules:** the agent that writes the code never approves it, and
  no agent merges.
- **What does not scale:** the design argument stays slow and human-led, and
  adversarial review changed what was built, not only what was caught.
- The **honest-limits section**: not used for real work yet, nothing wired into
  the running system, 38 findings means 38 defects existed, and a handful of runs
  is a pattern rather than a proof.
- **Evidence over assertion**: defects reproduced with probes, claims attached to
  commits and tests that fail on the prior revision.

## 5. What to challenge

- Is "where this becomes serious" earned by the evidence? It now rests on two
  claims together — harder verification and a product meant for ongoing use.
  Does the second claim carry its weight, or does it need more evidence?
- Is the pattern stated well enough that a reader could run it themselves? That
  is the piece's main practical claim.
- Does the scaling argument hold, given the reviewer is also a model? Is "review
  capacity is the limit" the right conclusion?
- Is the defect evidence legible to a reader who does not know the codebase? The
  forged-authorship case is the strongest; consider whether it should lead.
- Length (currently ~1,780 words, roughly 3–4 pages) and title.

## 6. Constraints

- The career domain may be named. Robert's specific history, employers,
  materials, and private paths may not. This may become public.
- Numbers must stay checkable; flag rather than round.
- Final editorial authority is Robert's.
