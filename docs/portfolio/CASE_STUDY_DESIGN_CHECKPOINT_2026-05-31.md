# Solution Checkpoint: Lining Up Against Industry Practice

*A working checkpoint on the mini-moi solution design — how the choices line up against established industry practice, before the build goes deeper.*

**Robert van Stedum · 2026-05-31**

---

## Why this checkpoint

mini-moi runs on a documented way of working, and Curator — the geopolitics and finance domain — is a real production system I use. The approach is already established in the project's own docs and headlines. As the build moves from concept into the deeper work, and as I plan to extend this to other domains including in my professional work, I want a checkpoint against outside practice.

The purpose is practical, not academic. Three things worth knowing before committing further:

- Are we way off from how the field does this?
- Is there a gotcha — a foundational choice that looks fine now but bites deep into the build, when it's expensive to undo?
- Will the solution extend, or has a choice quietly painted it into a corner?

Fast, co-creative work is productive, but it can drift without an outside reference. This is that reference: the design decisions measured against named industry practice, to catch a problem early instead of late.

---

## Where the solution lines up

Most of the design maps cleanly onto recognized practice. That's the result I wanted — lining up with established patterns means the choices aren't arbitrary and won't surprise someone who knows the field.

- The governing constraint — nothing expensive runs without my engagement — is **lazy evaluation** and **pull-based** design, with **progressive disclosure** at the interface. Cost and depth appear only when asked for.
- Retiring the legacy mechanism by halting new writes and letting existing entries expire, rather than forcing a migration, is the **strangler fig pattern**.
- Routing by exact string-match instead of an AI call where a cheaper exact tool works is the **principle of least power**.
- Free-form tags with a simple alias file, deferring any management UI until it's needed, is **folksonomy with progressive formalization** plus **YAGNI**.
- Collapsing seven scattered entry points into one gesture is **progressive disclosure** and lower **cognitive load**.
- Renaming in a single all-or-nothing commit is **atomicity**.

The belief-tracking object at the center — a view that moves from open question, to a loosely-held lean, to a committed position, and can soften back — rests on decision-science ground: **decision quality versus outcome quality** (judge the reasoning under uncertainty, not whether it happened to work out), **Bayesian belief updating**, **two-way-door** reversible decisions, and **strong opinions, loosely held**. The step-check-adjust rhythm is an **observe-orient-decide-act** loop.

These are all low-risk. They're how this kind of thing is done.

---

## Where it pushes the line — deliberately

Two choices run a little past common practice, on purpose:

- **Treating AI inference cost as a first-class design constraint.** Most AI products run inference eagerly. Building the interaction model around "no model call without engagement" is less common, and I think it gets more relevant as inference cost becomes a real concern.
- **Designing the AI's role against sycophancy** — calibrated to confirm *and* complicate, neither flattering nor reflexively contrarian, with the human kept as the decision-maker.

Pushing or mixing here is a bet made with eyes open, not a mistake. Neither is "wrong"; both are defensible against current practice.

---

## The two risks, directly

**Gotcha risk — anything that bites later?** The honest watch items:

- *The two-phase graph.* Building Postgres-shaped now, with Neo4j traversal deferred. The risk is shipping a schema that doesn't actually map to nodes and edges cleanly, then hitting a wall at migration. Watch item: validate the schema genuinely maps to the graph model before a lot of data accumulates on it.
- *Tag drift.* Free-form tags can get messy before the alias file catches up. If months of data accumulate with inconsistent tags, cleanup gets harder. Watch item: keep the alias file current; don't let drift compound.

Neither is a structural flaw. Both are manageable if watched. Nothing in the design looks like it bites unavoidably.

**Extendibility — does it grow to other domains and professional use?**

- The main hedge is modeling a neutral group as the base object with an opinionated view as an optional layer — **composition over inheritance**, **separation of concerns**, **open-closed**. This is specifically what lets a different domain reuse the model without a rewrite, and it's the choice I'd most want to be right. It checks out.
- Keeping the data Postgres-shaped for later graph migration is the data-side extendibility hedge.
- The open caveat: whether the domain boundary — sources, scoring, what's in-territory — is config-driven or hardcoded. If hardcoded, extending to a new domain is real rework. Flagged to confirm, not assumed. This is the one extendibility item to verify rather than trust.

**Verdict:** not way off, no obvious gotcha, and it extends — with two things to watch (validate the Postgres-to-graph mapping before heavy data; confirm the domain boundary is configuration, not hardcoded) and a couple of deliberate bets that sit slightly ahead of common practice.

---

## Close

A checkpoint, not a conclusion. The build keeps moving; the right move is to re-check at the next major commit, especially on the two watch items. The point is simple: make sure "it works" means it holds up against something outside itself, not just that we agreed it does.

---

*Part of mini-moi.*